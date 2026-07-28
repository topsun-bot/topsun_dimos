#!/usr/bin/env python3
# Copyright 2026 Dimensional Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Go2 4G Remote: 标定摇杆角速度与最小可响应转角.

通过环境变量读凭据 (不要把密码写进脚本):

  export UNITREE_WEBRTC_METHOD=remote
  export UNITREE_USERNAME=...
  export UNITREE_PASSWORD=...
  export UNITREE_SERIAL=...
  export UNITREE_REGION=cn

  .venv/bin/python jiangtao/scripts/demo_go2_rotate_calibration.py

狗周围请留出空间; 会站立并原地左右转.
"""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import json
import math
import os
from pathlib import Path
import sys
import time
from typing import Any

from dimos.msgs.geometry_msgs.Twist import Twist
from dimos.msgs.geometry_msgs.Vector3 import Vector3
from dimos.robot.unitree.connection import UnitreeWebRTCConnection
from dimos.utils.trigonometry import angle_diff

OUT_DIR = Path(__file__).resolve().parent.parent / "cache" / "rotate_calibration"


@dataclass
class RateSample:
    omega_cmd: float
    rx: float
    hold_s: float
    delta_yaw_deg: float
    rate_deg_s: float
    responsive: bool


@dataclass
class AngleSample:
    target_deg: float
    omega_cmd: float
    hold_s: float
    delta_yaw_deg: float
    responsive: bool
    err_deg: float


def _install_odom_yaw_cache(conn: UnitreeWebRTCConnection) -> Any:
    """订阅 odom_stream, 缓存最新 yaw. 返回 subscription 防止被 GC."""
    conn._calib_yaw_deg = None  # type: ignore[attr-defined]
    conn._calib_yaw_rad = None  # type: ignore[attr-defined]
    conn._calib_odom_n = 0  # type: ignore[attr-defined]

    def _on_odom(pose: Any) -> None:
        try:
            yaw = float(pose.orientation.to_euler().z)
            conn._calib_yaw_rad = yaw  # type: ignore[attr-defined]
            conn._calib_yaw_deg = math.degrees(yaw)  # type: ignore[attr-defined]
            conn._calib_odom_n += 1  # type: ignore[attr-defined]
        except Exception:
            return

    return conn.odom_stream().subscribe(_on_odom)


def _wait_odom(conn: UnitreeWebRTCConnection, timeout: float = 15.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if getattr(conn, "_calib_odom_n", 0) > 3 and conn._calib_yaw_rad is not None:  # type: ignore[attr-defined]
            return True
        time.sleep(0.1)
    return False


def _stop(conn: UnitreeWebRTCConnection) -> None:
    conn.move(Twist(linear=Vector3(0, 0, 0), angular=Vector3(0, 0, 0)), duration=0.0)
    time.sleep(0.3)


def _hold_omega(conn: UnitreeWebRTCConnection, omega: float, hold_s: float) -> tuple[float, float]:
    """开环保持 omega (映射到 rx=-omega), 返回 (start_yaw_deg, delta_yaw_deg)."""
    assert conn._calib_yaw_rad is not None  # type: ignore[attr-defined]
    start = float(conn._calib_yaw_rad)  # type: ignore[attr-defined]
    twist = Twist(linear=Vector3(0, 0, 0), angular=Vector3(0, 0, omega))
    t0 = time.monotonic()
    while time.monotonic() - t0 < hold_s:
        conn.move(twist, duration=0.0)
        time.sleep(0.05)  # 20 Hz, 高于 cmd_vel_timeout
    _stop(conn)
    time.sleep(0.4)  # settle
    end = float(conn._calib_yaw_rad)  # type: ignore[attr-defined]
    delta = math.degrees(angle_diff(end, start))
    return math.degrees(start), delta


def calibrate_rates(conn: UnitreeWebRTCConnection, hold_s: float) -> list[RateSample]:
    omegas = [0.05, 0.08, 0.10, 0.12, 0.15, 0.20, 0.25, 0.30, 0.40, 0.60, 0.80]
    samples: list[RateSample] = []
    print(f"\n=== 角速度标定: 每档保持 {hold_s:.1f}s (正转=CCW, omega>0 → rx=-omega) ===")
    for omega in omegas:
        # 正负各测一次取平均幅值, 先测正
        _, d_pos = _hold_omega(conn, omega, hold_s)
        time.sleep(0.5)
        _, d_neg = _hold_omega(conn, -omega, hold_s)
        time.sleep(0.5)
        mag = 0.5 * (abs(d_pos) + abs(d_neg))
        rate = mag / hold_s
        responsive = mag >= 2.0  # 2° 噪声门限
        s = RateSample(
            omega_cmd=omega,
            rx=-omega,
            hold_s=hold_s,
            delta_yaw_deg=mag,
            rate_deg_s=rate,
            responsive=responsive,
        )
        samples.append(s)
        flag = "OK" if responsive else "NO/弱响应"
        print(
            f"  omega={omega:.2f} (|rx|={omega:.0%})  "
            f"Δyaw≈{mag:5.1f}°  rate≈{rate:5.1f}°/s  [{flag}]  "
            f"(+{d_pos:+.1f} / {d_neg:+.1f})"
        )
    return samples


def calibrate_min_angle(conn: UnitreeWebRTCConnection, omega: float) -> list[AngleSample]:
    """用开环: hold = target/rate_est, 看实际转了多少. rate_est 用 omega 对应经验值."""
    # 先用 2s 估速
    _, d = _hold_omega(conn, omega, 2.0)
    time.sleep(0.5)
    rate = abs(d) / 2.0
    if rate < 1.0:
        print(f"\nomega={omega} 几乎不转 (rate={rate:.2f}°/s), 跳过最小角测试")
        return []
    print(f"\n=== 最小转角标定: omega={omega} (|rx|={omega:.0%}), 估速 {rate:.1f}°/s ===")
    targets = [2.0, 3.0, 5.0, 8.0, 10.0, 12.0, 15.0, 20.0, 30.0]
    samples: list[AngleSample] = []
    for tgt in targets:
        hold = max(0.15, abs(tgt) / rate)
        # 交替方向减少累计漂移
        sign = 1.0 if int(tgt) % 2 == 0 else -1.0
        _, delta = _hold_omega(conn, sign * omega, hold)
        time.sleep(0.4)
        err = abs(delta) - abs(tgt)
        responsive = abs(delta) >= max(1.5, 0.4 * abs(tgt))
        s = AngleSample(
            target_deg=tgt,
            omega_cmd=omega,
            hold_s=hold,
            delta_yaw_deg=abs(delta),
            responsive=responsive,
            err_deg=err,
        )
        samples.append(s)
        flag = "OK" if responsive else "弱/无"
        print(
            f"  target={tgt:5.1f}° hold={hold:.2f}s  "
            f"actual={abs(delta):5.1f}° err={err:+5.1f}°  [{flag}]"
        )
    return samples


def main() -> int:
    parser = argparse.ArgumentParser(description="Go2 rotate calibration (4G remote)")
    parser.add_argument("--hold", type=float, default=2.0, help="rate-test hold seconds")
    parser.add_argument(
        "--min-angle-omega",
        type=float,
        default=0.4,
        help="omega used for min-angle open-loop test (default 0.4 = current MIN_RAD_S)",
    )
    parser.add_argument(
        "--also-omega",
        type=float,
        default=0.2,
        help="second omega for min-angle test (smaller stick)",
    )
    args = parser.parse_args()

    method = os.getenv("UNITREE_WEBRTC_METHOD", "remote")
    user = os.getenv("UNITREE_USERNAME")
    password = os.getenv("UNITREE_PASSWORD")
    serial = os.getenv("UNITREE_SERIAL")
    region = os.getenv("UNITREE_REGION", "cn")
    if not user or not password or not serial:
        print("缺少 UNITREE_USERNAME / PASSWORD / SERIAL", file=sys.stderr)
        return 2

    print("Connecting Go2 via WebRTC remote (no password logged)...")
    print(f"  serial={serial} region={region} user={user[:3]}***")
    conn = UnitreeWebRTCConnection(
        connection_method=method,
        username=user,
        password=password,
        serial_number=serial,
        region=region,
    )
    odom_sub: Any = None
    try:
        odom_sub = _install_odom_yaw_cache(conn)
        print("StandUp (wait 5s) + BalanceStand + SwitchJoystick ...")
        from unitree_webrtc_connect.constants import RTC_TOPIC, SPORT_CMD

        r_up = conn.standup()
        print(f"  standup ok={r_up}")
        time.sleep(5.0)
        r_bal = conn.balance_stand()
        print(f"  balance_stand ok={r_bal}")
        time.sleep(2.0)
        # 4G DataChannel 摇杆需显式打开, 否则 WIRELESS_CONTROLLER 不生效
        r_joy = conn.publish_request(
            RTC_TOPIC["SPORT_MOD"],
            {"api_id": SPORT_CMD["SwitchJoystick"], "parameter": {"data": True}},
        )
        print(f"  SwitchJoystick True -> {r_joy}")
        time.sleep(1.0)
        if not _wait_odom(conn):
            print("ERROR: 未收到 ROBOTODOM, 无法标定", file=sys.stderr)
            return 1
        print(f"odom OK, yaw={conn._calib_yaw_deg:.1f}° frames={conn._calib_odom_n}")  # type: ignore[attr-defined]

        rate_samples = calibrate_rates(conn, args.hold)
        angle_samples_a = calibrate_min_angle(conn, args.min_angle_omega)
        angle_samples_b: list[AngleSample] = []
        if abs(args.also_omega - args.min_angle_omega) > 1e-6:
            angle_samples_b = calibrate_min_angle(conn, args.also_omega)

        # 汇总
        ok_rates = [s for s in rate_samples if s.responsive]
        dead = [s for s in rate_samples if not s.responsive]
        print("\n========== 汇总 ==========")
        if dead:
            print("不响应/弱响应的 |omega|(|rx|): " + ", ".join(f"{s.omega_cmd:.2f}" for s in dead))
        if ok_rates:
            print("响应档位 °/s ≈ |omega| * k :")
            for s in ok_rates:
                k = s.rate_deg_s / s.omega_cmd if s.omega_cmd else 0
                print(
                    f"  |rx|={s.omega_cmd:.0%}  →  {s.rate_deg_s:5.1f} °/s  "
                    f"(k≈{k:.1f} °/s per 1.0 stick)"
                )
            # 用 0.4 档给推荐
            s04 = next((s for s in ok_rates if abs(s.omega_cmd - 0.4) < 1e-6), ok_rates[-1])
            print(
                f"\n推荐换算: |rx|=0.40 时约 {s04.rate_deg_s:.1f} °/s; "
                f"满杆外推约 {s04.rate_deg_s / s04.omega_cmd:.1f} °/s"
            )
            # 死区上沿
            min_stick = min(s.omega_cmd for s in ok_rates)
            print(f"最小可响应摇杆 |rx| ≈ {min_stick:.0%} (本轮测到的最低 OK 档)")

        def _min_ok(samples: list[AngleSample]) -> float | None:
            oks = [s.target_deg for s in samples if s.responsive]
            return min(oks) if oks else None

        for label, samples in (
            (f"omega={args.min_angle_omega}", angle_samples_a),
            (f"omega={args.also_omega}", angle_samples_b),
        ):
            if not samples:
                continue
            m = _min_ok(samples)
            print(
                f"开环最小可靠目标角 ({label}): "
                + (f"约 {m:.0f}°" if m is not None else "本轮未测到可靠响应")
            )

        OUT_DIR.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y%m%d-%H%M%S")
        out = OUT_DIR / f"rotate_calib_{stamp}.json"
        payload = {
            "serial": serial,
            "region": region,
            "hold_s": args.hold,
            "rate_samples": [asdict(s) for s in rate_samples],
            "angle_samples": {
                str(args.min_angle_omega): [asdict(s) for s in angle_samples_a],
                str(args.also_omega): [asdict(s) for s in angle_samples_b],
            },
        }
        out.write_text(json.dumps(payload, indent=2, ensure_ascii=False))
        print(f"\n结果已写: {out}")
        return 0
    finally:
        if odom_sub is not None:
            try:
                odom_sub.dispose()
            except Exception:
                pass
        try:
            _stop(conn)
            conn.liedown()
        except Exception:
            pass
        try:
            conn.disconnect()
        except Exception:
            pass


if __name__ == "__main__":
    raise SystemExit(main())
