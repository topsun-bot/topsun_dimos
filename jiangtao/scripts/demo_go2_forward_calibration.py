#!/usr/bin/env python3
"""Go2 4G Remote: 用 odom 标定最小可响应前进线速度, 并抓帧诊断二维码.

不依赖 ArUco. 凭据从 .env / GlobalConfig 读取.

  source .venv/bin/activate && set -a && source .env && set +a
  uv run python jiangtao/scripts/demo_go2_forward_calibration.py

狗前方需留足安全距离; 会站立并低速前进.
"""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import json
import math
import os
from pathlib import Path
import threading
import time
from typing import Any

import cv2
import numpy as np

from dimos.core.global_config import global_config
from dimos.msgs.geometry_msgs.Twist import Twist
from dimos.msgs.geometry_msgs.Vector3 import Vector3
from dimos.perception.fiducial.marker_pose import create_aruco_detector
from dimos.robot.unitree.connection import UnitreeWebRTCConnection
from dimos.robot.unitree.go2.recharge.config import RechargeConfig
from dimos.robot.unitree.go2.recharge.vision import ArucoRechargeVision

OUT_DIR = Path(__file__).resolve().parent.parent / "cache" / "forward_calibration"


def _event(name: str, **fields: object) -> None:
    print(json.dumps({"event": name, **fields}, ensure_ascii=False), flush=True)


@dataclass
class ForwardSample:
    vx_mps: float
    hold_s: float
    delta_xy_m: float
    rate_mps: float
    responsive: bool


class LatestPose:
    """线程安全: 最新 odom 位姿."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._x = 0.0
        self._y = 0.0
        self._yaw = 0.0
        self._n = 0

    def on_odom(self, pose: Any) -> None:
        try:
            x = float(pose.position.x)
            y = float(pose.position.y)
            yaw = float(pose.orientation.to_euler().z)
        except Exception:
            return
        with self._lock:
            self._x = x
            self._y = y
            self._yaw = yaw
            self._n += 1

    def snapshot(self) -> tuple[float, float, float, int]:
        with self._lock:
            return self._x, self._y, self._yaw, self._n


class LatestImage:
    """线程安全: 最新相机帧."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._bgr: np.ndarray | None = None
        self._width = 0
        self._height = 0
        self._n = 0
        self._at: float | None = None

    def on_image(self, image: object) -> None:
        try:
            bgr = image.to_opencv()  # type: ignore[attr-defined]
            width = int(image.width)  # type: ignore[attr-defined]
            height = int(image.height)  # type: ignore[attr-defined]
        except Exception:
            return
        with self._lock:
            self._bgr = bgr.copy()
            self._width = width
            self._height = height
            self._n += 1
            self._at = time.monotonic()

    def snapshot(self) -> tuple[np.ndarray | None, int, int, int, float | None]:
        with self._lock:
            age = None if self._at is None else max(0.0, time.monotonic() - self._at)
            return (
                None if self._bgr is None else self._bgr.copy(),
                self._width,
                self._height,
                self._n,
                age,
            )


def _twist(vx: float = 0.0, yaw: float = 0.0) -> Twist:
    return Twist(linear=Vector3(vx, 0.0, 0.0), angular=Vector3(0.0, 0.0, yaw))


def _enable_motion(conn: UnitreeWebRTCConnection) -> None:
    standup_ok = conn.standup()
    time.sleep(5.0)
    balance_ok = conn.balance_stand()
    time.sleep(2.0)
    joystick_ok = conn.switch_joystick(True)
    conn.stop_movement()
    _event(
        "forward_calib_motion_enabled",
        standup=standup_ok,
        balance=balance_ok,
        joystick=joystick_ok,
    )


def _xy_distance(x0: float, y0: float, x1: float, y1: float) -> float:
    return math.hypot(x1 - x0, y1 - y0)


def _diagnose_frame(bgr: np.ndarray, path: Path) -> dict[str, Any]:
    """保存原图, 并用多种字典尝试检测, 画出结果."""
    path.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(path), bgr)
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY) if bgr.ndim == 3 else bgr
    dictionaries = [
        "DICT_APRILTAG_36h11",
        "DICT_4X4_50",
        "DICT_4X4_100",
        "DICT_5X5_50",
        "DICT_6X6_50",
        "DICT_ARUCO_ORIGINAL",
    ]
    hits: list[dict[str, Any]] = []
    annotated = bgr.copy()
    for name in dictionaries:
        try:
            detector = create_aruco_detector(name)
            corners, ids, _ = detector.detectMarkers(gray)
        except Exception as exc:
            hits.append({"dictionary": name, "error": str(exc)})
            continue
        if ids is None or len(ids) == 0:
            hits.append({"dictionary": name, "count": 0})
            continue
        ids_flat = [int(v) for v in ids.flatten()]
        side_px: list[float] = []
        for corner in corners:
            pts = np.asarray(corner, dtype=np.float64).reshape(4, 2)
            lengths = np.linalg.norm(pts - np.roll(pts, -1, axis=0), axis=1)
            side_px.append(float(np.min(lengths)))
        hits.append(
            {
                "dictionary": name,
                "count": len(ids_flat),
                "ids": ids_flat,
                "min_side_px": [round(v, 1) for v in side_px],
            }
        )
        cv2.aruco.drawDetectedMarkers(annotated, corners, ids)
    annotated_path = path.with_name(path.stem + "_annotated.png")
    cv2.imwrite(str(annotated_path), annotated)

    # 也跑一遍生产 vision 管线
    vision = ArucoRechargeVision(RechargeConfig())

    class _FakeImage:
        def __init__(self, arr: np.ndarray) -> None:
            self._arr = arr
            self.width = arr.shape[1]
            self.height = arr.shape[0]
            self.ts = time.time()

        def to_opencv(self) -> np.ndarray:
            return self._arr

    obs = vision.observe(_FakeImage(bgr))  # type: ignore[arg-type]
    production = None
    if obs is not None:
        production = {
            "x_m": round(obs.x_m, 4),
            "y_m": round(obs.y_m, 4),
            "z_m": round(obs.z_m, 4),
            "yaw_rad": round(obs.yaw_rad, 4),
            "reprojection_error_px": round(obs.reprojection_error_px, 4),
        }
    return {
        "saved": str(path),
        "annotated": str(annotated_path),
        "shape": [int(bgr.shape[0]), int(bgr.shape[1])],
        "dictionary_hits": hits,
        "production_vision": production,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--speeds",
        default="0.05,0.06,0.08,0.10,0.12,0.15,0.18,0.20",
        help="Comma-separated forward speeds (m/s) to probe.",
    )
    parser.add_argument("--hold-s", type=float, default=3.0, help="Seconds to hold each speed.")
    parser.add_argument(
        "--min-delta-m",
        type=float,
        default=0.03,
        help="Minimum odom XY displacement to count as responsive (default 3 cm).",
    )
    parser.add_argument("--capture-only", action="store_true", help="Only capture/diagnose camera.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    serial = os.getenv("UNITREE_SERIAL")
    if not serial:
        raise SystemExit("UNITREE_SERIAL is required")

    speeds = [float(part.strip()) for part in args.speeds.split(",") if part.strip()]
    config = global_config
    pose = LatestPose()
    camera = LatestImage()
    conn = UnitreeWebRTCConnection(
        ip=None,
        connection_method="remote",
        username=config.unitree_username,
        password=config.unitree_password,
        serial_number=serial,
        region=config.unitree_region or "cn",
    )
    image_sub = None
    odom_sub = None
    results: list[ForwardSample] = []
    try:
        image_sub = conn.video_stream().subscribe(camera.on_image)
        odom_sub = conn.odom_stream().subscribe(pose.on_odom)
        time.sleep(2.0)

        # 等第一帧
        deadline = time.monotonic() + 20.0
        bgr = None
        while time.monotonic() < deadline:
            bgr, width, height, frames, age = camera.snapshot()
            if bgr is not None and frames > 5:
                break
            time.sleep(0.1)
        if bgr is None:
            _event("forward_calib_no_camera")
            return 2

        stamp = time.strftime("%Y%m%d-%H%M%S")
        frame_path = OUT_DIR / f"camera_{stamp}.png"
        diagnosis = _diagnose_frame(bgr, frame_path)
        _event("forward_calib_camera_diagnosis", **diagnosis)

        if args.capture_only:
            return 0

        _enable_motion(conn)
        # 再抓一帧站立后的画面
        time.sleep(1.0)
        bgr2, _, _, _, _ = camera.snapshot()
        if bgr2 is not None:
            stand_path = OUT_DIR / f"camera_stand_{stamp}.png"
            diagnosis2 = _diagnose_frame(bgr2, stand_path)
            _event("forward_calib_camera_after_stand", **diagnosis2)

        # 等 odom
        deadline = time.monotonic() + 15.0
        while time.monotonic() < deadline:
            _, _, _, n = pose.snapshot()
            if n > 5:
                break
            time.sleep(0.1)
        _, _, _, n = pose.snapshot()
        if n <= 5:
            _event("forward_calib_no_odom", odom_n=n)
            return 2
        _event("forward_calib_odom_ready", odom_n=n)

        for speed in speeds:
            x0, y0, yaw0, _ = pose.snapshot()
            _event(
                "forward_calib_probe_start",
                vx_mps=speed,
                x0=round(x0, 4),
                y0=round(y0, 4),
                yaw0=round(yaw0, 4),
            )
            deadline = time.monotonic() + args.hold_s
            while time.monotonic() < deadline:
                conn.move(_twist(speed))
                time.sleep(0.05)
            for _ in range(5):
                conn.move(_twist(0.0))
                time.sleep(0.05)
            time.sleep(0.5)
            x1, y1, yaw1, _ = pose.snapshot()
            delta = _xy_distance(x0, y0, x1, y1)
            rate = delta / args.hold_s
            responsive = delta >= args.min_delta_m
            sample = ForwardSample(
                vx_mps=speed,
                hold_s=args.hold_s,
                delta_xy_m=delta,
                rate_mps=rate,
                responsive=responsive,
            )
            results.append(sample)
            _event(
                "forward_calib_probe_result",
                **{
                    k: round(v, 4) if isinstance(v, float) else v
                    for k, v in asdict(sample).items()
                },
                x1=round(x1, 4),
                y1=round(y1, 4),
                yaw1=round(yaw1, 4),
            )
            # 累计位移过大就停, 避免撞桩
            if sum(s.delta_xy_m for s in results) > 1.2:
                _event("forward_calib_stopping_early", reason="total_displacement_gt_1_2m")
                break

        min_responsive = next((s.vx_mps for s in results if s.responsive), None)
        _event(
            "forward_calib_summary",
            min_responsive_vx_mps=min_responsive,
            results=[asdict(s) for s in results],
        )
        return 0 if min_responsive is not None else 3
    finally:
        for _ in range(5):
            conn.move(_twist(0.0))
            time.sleep(0.05)
        if image_sub is not None:
            image_sub.dispose()
        if odom_sub is not None:
            odom_sub.dispose()
        conn.stop()


if __name__ == "__main__":
    raise SystemExit(main())
