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

"""记录每次启动后 odom / TF / lidar / IMU / global_map, 验证 odom 原点是否随开机变化.

动机: 同一物理位置固定 T 重定位多次结果不同, 怀疑 Unitree 每次开机后
rt/utlidar/robot_pose (dimos 里叫 odom, frame 写成 world) 原点/朝向不同.
odom 初值不同 -> lidar(world) 不同 -> global_map 不同 -> 固定 T 视觉偏.

模式:
  lcm     与 blueprint 并行, 录 odom/tf/lidar/global_map (无 IMU)
  webrtc  — 单独连狗, 录 raw odom/lidar/lowstate IMU (无 global_map, 勿与 blueprint 并行)

示例:
  # 终端 A 已跑 unitree-go2-relocalization
  .venv/bin/python jiangtao/scripts/record_boot_consistency.py record \\
      --mode lcm --duration 25 --tag boot1

  .venv/bin/python jiangtao/scripts/record_boot_consistency.py record \\
      --mode webrtc --robot-ip 192.168.12.1 --duration 20 --tag boot1

  .venv/bin/python jiangtao/scripts/record_boot_consistency.py compare \\
      jiangtao/cache/boot_consistency/boot1_* jiangtao/cache/boot_consistency/boot2_*
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from datetime import datetime
import json
import os as _os
from pathlib import Path
import sys
import threading
import time
from typing import Any

import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_OUT_ROOT = PROJECT_ROOT / "jiangtao/cache/boot_consistency"


def rpy_deg_from_quat(qx: float, qy: float, qz: float, qw: float) -> dict[str, float]:
    from scipy.spatial.transform import Rotation

    roll, pitch, yaw = Rotation.from_quat([qx, qy, qz, qw]).as_euler("xyz")
    return {
        "roll_deg": float(np.degrees(roll)),
        "pitch_deg": float(np.degrees(pitch)),
        "yaw_deg": float(np.degrees(yaw)),
        "yaw_rad": float(yaw),
    }


def R_from_quat(qx: float, qy: float, qz: float, qw: float) -> np.ndarray:
    from scipy.spatial.transform import Rotation

    return Rotation.from_quat([qx, qy, qz, qw]).as_matrix()


def pts_world_to_base(pts: np.ndarray, t: np.ndarray, R: np.ndarray) -> np.ndarray:
    return (pts - t) @ R


def cloud_stats(pts: np.ndarray) -> dict[str, Any]:
    if pts is None or len(pts) == 0:
        return {"n_points": 0}
    p = np.asarray(pts, dtype=np.float64)
    c = p.mean(axis=0)
    mn = p.min(axis=0)
    mx = p.max(axis=0)
    return {
        "n_points": len(p),
        "centroid_m": [round(float(x), 4) for x in c],
        "aabb_min": [round(float(x), 3) for x in mn],
        "aabb_max": [round(float(x), 3) for x in mx],
        "aabb_size": [round(float(x), 3) for x in (mx - mn)],
        "mean_norm_m": round(float(np.linalg.norm(p, axis=1).mean()), 3),
    }


def append_jsonl(path: Path, obj: dict[str, Any]) -> None:
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(obj, ensure_ascii=False) + "\n")


def now_stamp() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S")


def pose_record(
    *,
    source: str,
    ts: float | None,
    x: float,
    y: float,
    z: float,
    qx: float,
    qy: float,
    qz: float,
    qw: float,
    frame_id: str = "",
    child_frame_id: str = "",
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    d: dict[str, Any] = {
        "wall_time": time.time(),
        "msg_ts": float(ts) if ts is not None else None,
        "source": source,
        "frame_id": frame_id,
        "child_frame_id": child_frame_id or "",
        "position_m": [round(float(x), 6), round(float(y), 6), round(float(z), 6)],
        "orientation_xyzw": [
            round(float(qx), 6),
            round(float(qy), 6),
            round(float(qz), 6),
            round(float(qw), 6),
        ],
    }
    d.update(rpy_deg_from_quat(qx, qy, qz, qw))
    if extra:
        d.update(extra)
    return d


@dataclass
class RecorderState:
    t0: float
    out_dir: Path
    lock: threading.Lock = field(default_factory=threading.Lock)
    odom_rows: list[dict[str, Any]] = field(default_factory=list)
    tf_rows: list[dict[str, Any]] = field(default_factory=list)
    imu_rows: list[dict[str, Any]] = field(default_factory=list)
    lidar_rows: list[dict[str, Any]] = field(default_factory=list)
    global_map_rows: list[dict[str, Any]] = field(default_factory=list)
    latest_odom: dict[str, Any] | None = None
    n_lidar_saved: int = 0
    n_global_map_saved: int = 0
    max_lidar_save: int = 5
    max_global_map_save: int = 3
    lidar_every: int = 5
    global_map_every: int = 3
    _lidar_i: int = 0
    _gmap_i: int = 0


def ensure_dirs(out_dir: Path) -> None:
    (out_dir / "lidar_frames").mkdir(parents=True, exist_ok=True)
    (out_dir / "global_map_frames").mkdir(parents=True, exist_ok=True)


def store_odom(state: RecorderState, rec: dict[str, Any]) -> None:
    with state.lock:
        state.odom_rows.append(rec)
        state.latest_odom = rec
    append_jsonl(state.out_dir / "odom.jsonl", rec)


def store_tf(state: RecorderState, rec: dict[str, Any]) -> None:
    with state.lock:
        state.tf_rows.append(rec)
    append_jsonl(state.out_dir / "tf.jsonl", rec)


def store_imu(state: RecorderState, rec: dict[str, Any]) -> None:
    with state.lock:
        state.imu_rows.append(rec)
    append_jsonl(state.out_dir / "imu.jsonl", rec)


def process_lidar_points(
    state: RecorderState,
    *,
    source: str,
    ts: float | None,
    frame_id: str,
    points: np.ndarray,
    origin: list[float] | None = None,
) -> None:
    state._lidar_i += 1
    force = state.n_lidar_saved < state.max_lidar_save
    if not force and (state._lidar_i % state.lidar_every) != 0:
        return

    world = cloud_stats(points)
    base_stats: dict[str, Any] = {"n_points": 0}
    odom_used = None
    with state.lock:
        odom = state.latest_odom
    if odom is not None and len(points) > 0:
        t = np.asarray(odom["position_m"], dtype=np.float64)
        qx, qy, qz, qw = odom["orientation_xyzw"]
        R = R_from_quat(qx, qy, qz, qw)
        base_stats = cloud_stats(pts_world_to_base(points, t, R))
        odom_used = {"position_m": odom["position_m"], "yaw_deg": odom["yaw_deg"]}

    rec: dict[str, Any] = {
        "wall_time": time.time(),
        "msg_ts": ts,
        "source": source,
        "frame_id": frame_id,
        "origin": origin,
        "world": world,
        "base": base_stats,
        "odom_used": odom_used,
    }
    with state.lock:
        state.lidar_rows.append(rec)
    append_jsonl(state.out_dir / "lidar_stats.jsonl", rec)

    if force and len(points) > 0:
        idx = state.n_lidar_saved
        np.save(
            state.out_dir / "lidar_frames" / f"frame_{idx:02d}_world.npy",
            points.astype(np.float32),
        )
        if odom is not None:
            t = np.asarray(odom["position_m"], dtype=np.float64)
            qx, qy, qz, qw = odom["orientation_xyzw"]
            R = R_from_quat(qx, qy, qz, qw)
            np.save(
                state.out_dir / "lidar_frames" / f"frame_{idx:02d}_base.npy",
                pts_world_to_base(points, t, R).astype(np.float32),
            )
        meta = {k: rec[k] for k in ("wall_time", "msg_ts", "frame_id", "origin", "world", "base")}
        (state.out_dir / "lidar_frames" / f"frame_{idx:02d}.json").write_text(
            json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        state.n_lidar_saved += 1


def process_global_map(
    state: RecorderState, *, ts: float | None, frame_id: str, points: np.ndarray
) -> None:
    state._gmap_i += 1
    force = state.n_global_map_saved < state.max_global_map_save
    if not force and (state._gmap_i % state.global_map_every) != 0:
        return

    stats = cloud_stats(points)
    rec: dict[str, Any] = {
        "wall_time": time.time(),
        "msg_ts": ts,
        "frame_id": frame_id,
        **stats,
    }
    with state.lock:
        state.global_map_rows.append(rec)
    append_jsonl(state.out_dir / "global_map_stats.jsonl", rec)

    if force and len(points) > 0:
        idx = state.n_global_map_saved
        save_pts = points
        if len(points) > 200_000:
            step = max(1, len(points) // 200_000)
            save_pts = points[::step]
        np.save(
            state.out_dir / "global_map_frames" / f"map_{idx:02d}.npy",
            save_pts.astype(np.float32),
        )
        (state.out_dir / "global_map_frames" / f"map_{idx:02d}.json").write_text(
            json.dumps(rec, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        state.n_global_map_saved += 1


def write_summary(state: RecorderState, meta: dict[str, Any]) -> None:
    odom = state.odom_rows
    summary: dict[str, Any] = {
        "mode": meta.get("mode"),
        "tag": meta.get("tag"),
        "started_at": meta.get("started"),
        "duration_s": round(time.time() - state.t0, 2),
        "counts": {
            "odom": len(odom),
            "tf": len(state.tf_rows),
            "imu": len(state.imu_rows),
            "lidar": len(state.lidar_rows),
            "global_map": len(state.global_map_rows),
        },
    }

    if odom:
        first, last = odom[0], odom[-1]
        pos = np.array([r["position_m"] for r in odom], dtype=float)
        yaws = np.array([r["yaw_deg"] for r in odom], dtype=float)
        summary["odom_first"] = {
            "position_m": first["position_m"],
            "yaw_deg": first["yaw_deg"],
            "frame_id": first.get("frame_id"),
            "msg_ts": first.get("msg_ts"),
        }
        summary["odom_last"] = {
            "position_m": last["position_m"],
            "yaw_deg": last["yaw_deg"],
        }
        summary["odom_stationary_check"] = {
            "pos_std_m": [round(float(x), 4) for x in pos.std(axis=0)],
            "pos_peak_to_peak_m": [round(float(x), 4) for x in (pos.max(0) - pos.min(0))],
            "yaw_std_deg": round(float(yaws.std()), 3),
            "yaw_peak_to_peak_deg": round(float(yaws.max() - yaws.min()), 3),
            "note": "站定时 std/ptp 应很小; 跨 run 的 odom_first 差大 = 开机原点不同",
        }
        if len(odom) >= 2 and first.get("msg_ts") and last.get("msg_ts"):
            dt = last["msg_ts"] - first["msg_ts"]
            if dt > 0:
                summary["odom_hz"] = round((len(odom) - 1) / dt, 2)

    world_base = [r for r in state.tf_rows if r.get("child_frame_id") == "base_link"]
    if world_base:
        summary["tf_world_base_first"] = {
            "position_m": world_base[0]["position_m"],
            "yaw_deg": world_base[0]["yaw_deg"],
            "frame_id": world_base[0].get("frame_id"),
        }

    world_map = [r for r in state.tf_rows if r.get("child_frame_id") == "map"]
    if world_map:
        summary["tf_world_map_first"] = {
            "position_m": world_map[0]["position_m"],
            "yaw_deg": world_map[0]["yaw_deg"],
        }

    if state.imu_rows:
        first_imu = state.imu_rows[0]
        rpy = np.array(
            [[r["roll_deg"], r["pitch_deg"], r["yaw_deg"]] for r in state.imu_rows],
            dtype=float,
        )
        summary["imu_first"] = {
            "rpy_deg": [first_imu["roll_deg"], first_imu["pitch_deg"], first_imu["yaw_deg"]],
            "foot_force": first_imu.get("foot_force"),
        }
        summary["imu_rpy_std_deg"] = [round(float(x), 3) for x in rpy.std(0)]

    if state.lidar_rows:
        summary["lidar_first"] = state.lidar_rows[0]
        base_c = [
            r["base"]["centroid_m"]
            for r in state.lidar_rows
            if r.get("base", {}).get("n_points", 0) > 0
        ]
        if base_c:
            bc = np.array(base_c, dtype=float)
            summary["lidar_base_centroid_mean_m"] = [round(float(x), 4) for x in bc.mean(0)]
            summary["lidar_base_centroid_std_m"] = [round(float(x), 4) for x in bc.std(0)]
            summary["lidar_base_note"] = "站定: base 系 centroid 应接近; world 系会随 odom 原点漂"

    if state.global_map_rows:
        summary["global_map_first"] = state.global_map_rows[0]
        summary["global_map_last"] = state.global_map_rows[-1]
        summary["global_map_note"] = "odom 开机原点不同时, 早期 global_map centroid 会跟着不同"

    summary["hypothesis_checklist"] = [
        "1. 对比多次 run 的 odom_first.position_m / yaw_deg — 差大则 odom 开机漂移成立",
        "2. odom_stationary_check — 单次 run 内是否真的站定",
        "3. lidar_base_centroid_std — 单帧在 base 系是否稳定",
        "4. lidar world centroid 跨 run 差 — 应与 odom 差同量级",
        "5. global_map_first.centroid — 应随 odom 漂移",
        "6. imu yaw (webrtc) — 与 odom yaw 是否同漂",
        "7. foot_force — 确认四脚着地",
    ]

    path = state.out_dir / "summary.json"
    path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    lines = [
        f"mode={summary.get('mode')} duration_s={summary['duration_s']}",
        f"counts={summary['counts']}",
    ]
    if "odom_first" in summary:
        of = summary["odom_first"]
        lines.append(
            f"odom_first: pos={of['position_m']} yaw={of['yaw_deg']:.2f}deg "
            f"frame={of.get('frame_id')}"
        )
    if "odom_stationary_check" in summary:
        sc = summary["odom_stationary_check"]
        lines.append(f"odom_stationary: pos_std={sc['pos_std_m']} yaw_std={sc['yaw_std_deg']}deg")
    if "lidar_base_centroid_mean_m" in summary:
        lines.append(
            f"lidar_base_centroid_mean={summary['lidar_base_centroid_mean_m']} "
            f"std={summary['lidar_base_centroid_std_m']}"
        )
    if "global_map_first" in summary:
        gm = summary["global_map_first"]
        lines.append(f"global_map_first: n={gm.get('n_points')} centroid={gm.get('centroid_m')}")
    (state.out_dir / "summary.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n=== summary ===")
    print("\n".join(lines))
    print(f"wrote {path}")


def make_out_dir(args: argparse.Namespace, mode: str) -> Path:
    if args.out_dir:
        out = Path(args.out_dir)
    else:
        out = DEFAULT_OUT_ROOT / f"{args.tag}_{now_stamp()}_{mode}"
    out.mkdir(parents=True, exist_ok=True)
    ensure_dirs(out)
    return out


# ---------------------------------------------------------------------------
# LCM 模式
# ---------------------------------------------------------------------------


def cmd_record_lcm(args: argparse.Namespace) -> int:
    from dimos.core.transport import LCMTransport
    from dimos.msgs.geometry_msgs.PoseStamped import PoseStamped
    from dimos.msgs.sensor_msgs.PointCloud2 import PointCloud2
    from dimos.msgs.tf2_msgs.TFMessage import TFMessage
    from dimos.protocol.service.lcmservice import autoconf

    autoconf(check_only=True)

    out_dir = make_out_dir(args, "lcm")
    state = RecorderState(t0=time.time(), out_dir=out_dir)
    meta = {
        "mode": "lcm",
        "tag": args.tag,
        "started": datetime.now().isoformat(timespec="seconds"),
        "topics": {
            "odom": args.odom_topic,
            "tf": args.tf_topic,
            "lidar": args.lidar_topic,
            "global_map": args.global_map_topic,
        },
        "note": "GO2Connection 不发 IMU; 需要 IMU 请用 --mode webrtc (且不要同时开 blueprint)",
    }
    (out_dir / "meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"[lcm] recording -> {out_dir} for {args.duration}s")
    print("  topics:", meta["topics"])

    transports: list[Any] = []

    def handle_odom(msg: PoseStamped) -> None:
        store_odom(
            state,
            pose_record(
                source="lcm/odom",
                ts=getattr(msg, "ts", None),
                x=float(msg.position.x),
                y=float(msg.position.y),
                z=float(msg.position.z),
                qx=float(msg.orientation.x),
                qy=float(msg.orientation.y),
                qz=float(msg.orientation.z),
                qw=float(msg.orientation.w),
                frame_id=getattr(msg, "frame_id", "") or "world",
                child_frame_id="base_link",
            ),
        )

    def handle_tf(msg: TFMessage) -> None:
        for t in msg.transforms:
            store_tf(
                state,
                pose_record(
                    source="lcm/tf",
                    ts=getattr(t, "ts", None),
                    x=float(t.translation.x),
                    y=float(t.translation.y),
                    z=float(t.translation.z),
                    qx=float(t.rotation.x),
                    qy=float(t.rotation.y),
                    qz=float(t.rotation.z),
                    qw=float(t.rotation.w),
                    frame_id=getattr(t, "frame_id", "") or "",
                    child_frame_id=getattr(t, "child_frame_id", "") or "",
                ),
            )

    def handle_lidar(msg: PointCloud2) -> None:
        pts, _ = msg.as_numpy()
        process_lidar_points(
            state,
            source="lcm/lidar",
            ts=getattr(msg, "ts", None),
            frame_id=getattr(msg, "frame_id", "world") or "world",
            points=np.asarray(pts, dtype=np.float64),
        )

    def handle_gmap(msg: PointCloud2) -> None:
        pts, _ = msg.as_numpy()
        process_global_map(
            state,
            ts=getattr(msg, "ts", None),
            frame_id=getattr(msg, "frame_id", "") or "world",
            points=np.asarray(pts, dtype=np.float64),
        )

    for topic, typ, cb in [
        (args.odom_topic, PoseStamped, handle_odom),
        (args.tf_topic, TFMessage, handle_tf),
        (args.lidar_topic, PointCloud2, handle_lidar),
        (args.global_map_topic, PointCloud2, handle_gmap),
    ]:
        tr = LCMTransport(topic, typ)
        tr.subscribe(cb)
        transports.append(tr)
        print(f"  subscribed {topic} ({typ.__name__})")

    last_print = -1
    try:
        while time.time() - state.t0 < args.duration:
            time.sleep(0.1)
            elapsed = int(time.time() - state.t0)
            if elapsed != last_print and elapsed % 5 == 0:
                last_print = elapsed
                print(
                    f"  t={elapsed}s odom={len(state.odom_rows)} "
                    f"lidar={len(state.lidar_rows)} gmap={len(state.global_map_rows)}"
                )
    except KeyboardInterrupt:
        print("\n[lcm] interrupted")
    finally:
        for tr in transports:
            try:
                tr.stop()
            except Exception:
                pass
        write_summary(state, meta)
    return 0


# ---------------------------------------------------------------------------
# WebRTC
# ---------------------------------------------------------------------------


def cmd_record_webrtc(args: argparse.Namespace) -> int:
    from dimos.robot.unitree.connection import UnitreeWebRTCConnection

    ip = args.robot_ip or _os.environ.get("DIMOS_ROBOT_IP") or _os.environ.get("ROBOT_IP", "")
    if not ip:
        print("ERROR: --robot-ip 或 DIMOS_ROBOT_IP / ROBOT_IP 必填", file=sys.stderr)
        return 2

    aes = args.aes_key or _os.environ.get("DIMOS_UNITREE_AES_128_KEY")
    out_dir = make_out_dir(args, "webrtc")
    state = RecorderState(t0=time.time(), out_dir=out_dir)
    meta = {
        "mode": "webrtc",
        "tag": args.tag,
        "robot_ip": ip,
        "started": datetime.now().isoformat(timespec="seconds"),
        "note": "勿与 blueprint 同时连狗; 无 global_map",
    }
    (out_dir / "meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"[webrtc] connecting {ip} -> {out_dir} for {args.duration}s")

    conn = UnitreeWebRTCConnection(ip, aes_128_key=aes)
    conn.start()
    disposables: list[Any] = []

    def handle_raw_odom(msg: Any) -> None:
        try:
            data = msg["data"]
            pose = data["pose"]
            header = data.get("header", {})
            frame_id = header.get("frame_id", "odom")
            stamp = header.get("stamp", {})
            ts = None
            if isinstance(stamp, dict) and "sec" in stamp:
                ts = stamp["sec"] + stamp.get("nanosec", 0) * 1e-9
            store_odom(
                state,
                pose_record(
                    source="webrtc/raw_odom",
                    ts=ts,
                    x=float(pose["position"]["x"]),
                    y=float(pose["position"]["y"]),
                    z=float(pose["position"]["z"]),
                    qx=float(pose["orientation"]["x"]),
                    qy=float(pose["orientation"]["y"]),
                    qz=float(pose["orientation"]["z"]),
                    qw=float(pose["orientation"]["w"]),
                    frame_id=str(frame_id),
                    child_frame_id="base_link",
                    extra={"dimos_frame_id_after_parse": "world"},
                ),
            )
        except Exception as e:
            print(f"  odom parse error: {e}")

    def handle_raw_lidar(msg: Any) -> None:
        try:
            data = msg["data"]
            origin = data.get("origin")
            frame_id = data.get("frame_id", "world")
            stamp = data.get("stamp")
            pts = np.asarray(data["data"]["points"], dtype=np.float64)
            if pts.ndim == 1:
                pts = pts.reshape(-1, 3)
            process_lidar_points(
                state,
                source="webrtc/raw_lidar",
                ts=float(stamp) if stamp is not None else None,
                frame_id=frame_id,
                points=pts,
                origin=list(origin) if origin is not None else None,
            )
        except Exception as e:
            print(f"  lidar parse error: {e}")

    def handle_lowstate(msg: Any) -> None:
        try:
            data = msg["data"]
            imu = data.get("imu_state", {})
            rpy = imu.get("rpy", [0.0, 0.0, 0.0])
            roll, pitch, yaw = float(rpy[0]), float(rpy[1]), float(rpy[2])
            rec = {
                "wall_time": time.time(),
                "source": "webrtc/lowstate",
                "roll_deg": float(np.degrees(roll)),
                "pitch_deg": float(np.degrees(pitch)),
                "yaw_deg": float(np.degrees(yaw)),
                "rpy_rad": [float(roll), float(pitch), float(yaw)],
                "foot_force": data.get("foot_force"),
                "power_v": data.get("power_v"),
            }
            with state.lock:
                if state.imu_rows and (rec["wall_time"] - state.imu_rows[-1]["wall_time"]) < 0.09:
                    return
            store_imu(state, rec)
        except Exception as e:
            print(f"  imu parse error: {e}")

    disposables.append(conn.raw_odom_stream().subscribe(handle_raw_odom))
    disposables.append(conn.raw_lidar_stream().subscribe(handle_raw_lidar))
    disposables.append(conn.lowstate_stream().subscribe(handle_lowstate))

    last_print = -1
    try:
        while time.time() - state.t0 < args.duration:
            time.sleep(0.1)
            elapsed = int(time.time() - state.t0)
            if elapsed != last_print and elapsed % 5 == 0:
                last_print = elapsed
                print(
                    f"  t={elapsed}s odom={len(state.odom_rows)} "
                    f"lidar={len(state.lidar_rows)} imu={len(state.imu_rows)}"
                )
    except KeyboardInterrupt:
        print("\n[webrtc] interrupted")
    finally:
        for d in disposables:
            try:
                d.dispose()
            except Exception:
                pass
        try:
            conn.stop()
        except Exception:
            pass
        write_summary(state, meta)
    return 0


# ---------------------------------------------------------------------------
# compare
# ---------------------------------------------------------------------------


def _load_summary(path: Path) -> dict[str, Any]:
    s = path / "summary.json"
    if not s.exists():
        raise FileNotFoundError(f"missing {s}")
    return json.loads(s.read_text(encoding="utf-8"))


def _yaw_diff_deg(a: float, b: float) -> float:
    return float((a - b + 180.0) % 360.0 - 180.0)


def cmd_compare(args: argparse.Namespace) -> int:
    runs: list[tuple[str, dict[str, Any]]] = []
    for p in args.runs:
        path = Path(p)
        candidates: list[Path] = []
        if path.is_dir():
            candidates = [path]
        else:
            candidates = sorted(DEFAULT_OUT_ROOT.glob(p))
            if not candidates:
                candidates = list(Path(".").glob(p))
        for m in candidates:
            if m.is_dir() and (m / "summary.json").exists():
                runs.append((m.name, _load_summary(m)))
            elif path.is_dir():
                print(f"skip missing summary: {m}", file=sys.stderr)

    if len(runs) < 2:
        print("需要至少 2 个有效 run 目录 (含 summary.json)", file=sys.stderr)
        return 2

    print(f"\n=== compare {len(runs)} runs ===\n")
    print(f"{'run':40s} {'odom_xyz':40s} {'yaw_deg':>10} {'gmap_n':>8s}")
    print("-" * 100)

    base_name, base_sum = runs[0]
    base_odom = base_sum.get("odom_first")

    for name, s in runs:
        o = s.get("odom_first") or {}
        pos = o.get("position_m", [])
        yaw = o.get("yaw_deg", float("nan"))
        gm = (s.get("global_map_first") or {}).get("n_points", "-")
        print(f"{name[:40]:40s} {pos!s:40s} {yaw:10.2f} {gm!s:>8}")

    print("\n--- delta vs first run ---")
    if not base_odom:
        print("base 无 odom_first, 无法对比")
        return 1

    bp = np.array(base_odom["position_m"], dtype=float)
    by = float(base_odom["yaw_deg"])
    for name, s in runs[1:]:
        o = s.get("odom_first")
        if not o:
            print(f"{name}: no odom_first")
            continue
        p = np.asarray(o["position_m"], dtype=float)
        dy = _yaw_diff_deg(float(o["yaw_deg"]), by)
        print(
            f"{name}: dpos={np.round(p - bp, 4).tolist()} m  "
            f"|dpos|={float(np.linalg.norm(p - bp)):.3f} m  dyaw={dy:.2f} deg"
        )

        lf = s.get("lidar_first") or {}
        bf = base_sum.get("lidar_first") or {}
        if lf.get("world") and bf.get("world"):
            cw = np.asarray(lf["world"].get("centroid_m", [0, 0, 0]), dtype=float)
            bw = np.asarray(bf["world"].get("centroid_m", [0, 0, 0]), dtype=float)
            print(
                f"  lidar_world_centroid_delta={np.round(cw - bw, 3).tolist()} "
                f"|d|={float(np.linalg.norm(cw - bw)):.3f} m"
            )
        if lf.get("base") and bf.get("base"):
            c1 = np.asarray(lf["base"].get("centroid_m", [0, 0, 0]), dtype=float)
            c0 = np.asarray(bf["base"].get("centroid_m", [0, 0, 0]), dtype=float)
            if len(c1) == 3 and len(c0) == 3 and lf["base"].get("n_points", 0) > 0:
                print(
                    f"  lidar_base_centroid_delta={np.round(c1 - c0, 4).tolist()} "
                    f"|d|={float(np.linalg.norm(c1 - c0)):.3f}m  (base 应接近 0)"
                )

        gf = s.get("global_map_first") or {}
        g0 = base_sum.get("global_map_first") or {}
        if gf.get("centroid_m") and g0.get("centroid_m"):
            cg = np.asarray(gf["centroid_m"], dtype=float)
            c0 = np.asarray(g0["centroid_m"], dtype=float)
            print(
                f"  global_map_centroid_delta={np.round(cg - c0, 3).tolist()} "
                f"|d|={float(np.linalg.norm(cg - c0)):.3f}m"
            )

    print(
        "\n解读: odom_first 跨 run 差大 + lidar_base 接近 0 "
        "→ 确认是 odom 开机原点不同, 不是 lidar 本身变了。"
    )
    return 0


def cmd_record(args: argparse.Namespace) -> int:
    if args.mode == "lcm":
        return cmd_record_lcm(args)
    if args.mode == "webrtc":
        return cmd_record_webrtc(args)
    print(f"unknown mode {args.mode}", file=sys.stderr)
    return 2


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="记录/对比开机 odom 一致性")
    sub = p.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("record", help="录一次启动")
    r.add_argument("--mode", choices=["lcm", "webrtc"], default="lcm")
    r.add_argument("--duration", type=float, default=25.0, help="秒")
    r.add_argument("--tag", default="boot", help="输出目录前缀")
    r.add_argument("--out-dir", default=None, help="指定输出目录")
    r.add_argument("--robot-ip", default=None)
    r.add_argument("--aes-key", default=None)
    r.add_argument("--odom-topic", default="/odom")
    r.add_argument("--tf-topic", default="/tf")
    r.add_argument("--lidar-topic", default="/lidar")
    r.add_argument("--global-map-topic", default="/global_map")
    r.set_defaults(func=cmd_record)

    c = sub.add_parser("compare", help="对比多次 summary.json")
    c.add_argument("runs", nargs="+", help="run 目录或 glob")
    c.set_defaults(func=cmd_compare)

    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
