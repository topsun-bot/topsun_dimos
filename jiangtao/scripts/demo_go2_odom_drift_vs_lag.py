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

"""第三轮: 区分 odom 静止漂移 vs 旋转后 yaw 变化."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import json
import math
import os
from pathlib import Path
import statistics
import sys
import time
from typing import Any

from unitree_webrtc_connect.constants import RTC_TOPIC, SPORT_CMD

from dimos.msgs.geometry_msgs.Twist import Twist
from dimos.msgs.geometry_msgs.Vector3 import Vector3
from dimos.robot.unitree.connection import UnitreeWebRTCConnection
from dimos.robot.unitree.type.odometry import Odometry
from dimos.utils.trigonometry import angle_diff

OUT = Path(__file__).resolve().parent.parent / "cache" / "rotation_verify"


@dataclass
class Point:
    mono_ns: int
    odom_yaw_deg: float
    imu_yaw_deg: float | None
    phase: str


def _yaw_rates(points: list[Point]) -> list[float]:
    rates: list[float] = []
    for i in range(1, len(points)):
        dt = (points[i].mono_ns - points[i - 1].mono_ns) / 1e9
        if dt <= 0:
            continue
        dy = math.degrees(
            angle_diff(
                math.radians(points[i].odom_yaw_deg),
                math.radians(points[i - 1].odom_yaw_deg),
            )
        )
        rates.append(abs(dy) / dt)
    return rates


def _window_stats(points: list[Point]) -> dict[str, Any]:
    if len(points) < 2:
        return {"n": len(points), "delta_yaw_deg": 0.0, "mean_abs_rate_deg_s": 0.0}
    rates = _yaw_rates(points)
    dy = math.degrees(
        angle_diff(
            math.radians(points[-1].odom_yaw_deg),
            math.radians(points[0].odom_yaw_deg),
        )
    )
    imu_dy = None
    imu_pts = [p for p in points if p.imu_yaw_deg is not None]
    if len(imu_pts) >= 2:
        imu_dy = math.degrees(
            angle_diff(
                math.radians(imu_pts[-1].imu_yaw_deg),  # type: ignore[arg-type]
                math.radians(imu_pts[0].imu_yaw_deg),  # type: ignore[arg-type]
            )
        )
    return {
        "n": len(points),
        "delta_yaw_deg": round(dy, 3),
        "mean_abs_rate_deg_s": round(statistics.mean(rates), 3) if rates else 0.0,
        "max_abs_rate_deg_s": round(max(rates), 3) if rates else 0.0,
        "imu_delta_yaw_deg": round(imu_dy, 3) if imu_dy is not None else None,
    }


def main() -> int:
    user = os.getenv("UNITREE_USERNAME")
    password = os.getenv("UNITREE_PASSWORD")
    serial = os.getenv("UNITREE_SERIAL")
    if not all([user, password, serial]):
        print("缺少凭据", file=sys.stderr)
        return 2

    static_s = float(os.getenv("DRIFT_STATIC_S", "120"))
    post_static_s = float(os.getenv("DRIFT_POST_STATIC_S", "60"))

    points: list[Point] = []
    seq = [0]
    imu_yaw_deg: list[float | None] = [None]
    phase = ["pre_static"]

    def on_odom(msg: dict[str, Any]) -> None:
        try:
            odom = Odometry.from_msg(msg)  # type: ignore[arg-type]
            yaw = math.degrees(float(odom.orientation.to_euler().z))
            seq[0] += 1
            points.append(
                Point(
                    mono_ns=time.monotonic_ns(),
                    odom_yaw_deg=yaw,
                    imu_yaw_deg=imu_yaw_deg[0],
                    phase=phase[0],
                )
            )
        except Exception:
            pass

    def on_lowstate(msg: dict[str, Any]) -> None:
        try:
            data = msg.get("data", msg)
            imu = data.get("imu_state") or {}
            rpy = imu.get("rpy")
            if isinstance(rpy, (list, tuple)) and len(rpy) >= 3:
                imu_yaw_deg[0] = math.degrees(float(rpy[2]))
        except Exception:
            pass

    print("连接 4G Remote ...")
    conn = UnitreeWebRTCConnection(
        connection_method="remote",
        username=user,
        password=password,
        serial_number=serial,
        region=os.getenv("UNITREE_REGION", "cn"),
    )
    conn.conn.datachannel.pub_sub.subscribe(RTC_TOPIC["ROBOTODOM"], on_odom)
    conn.conn.datachannel.pub_sub.subscribe(RTC_TOPIC["LOW_STATE"], on_lowstate)
    conn.standup()
    time.sleep(5)
    conn.balance_stand()
    time.sleep(2)
    conn.publish_request(
        RTC_TOPIC["SPORT_MOD"],
        {"api_id": SPORT_CMD["SwitchJoystick"], "parameter": {"data": True}},
    )
    time.sleep(1)

    try:
        # A: 旋转前静止漂移基线
        phase[0] = "pre_static"
        n0 = len(points)
        print(f"\n[A] 旋转前静止 {static_s:.0f}s (测 odom 本底漂移)")
        t0 = time.time()
        while time.time() - t0 < static_s:
            time.sleep(0.5)
        pre_static = points[n0:]
        pre_stats = _window_stats(pre_static)
        print(json.dumps(pre_stats, ensure_ascii=False))

        # B: 旋转 + 零 + 分段观察
        phase[0] = "cmd"
        len(points)
        omega = 0.4
        hold_s = 1.0
        twist = Twist(linear=Vector3(0, 0, 0), angular=Vector3(0, 0, omega))
        t_cmd = time.monotonic()
        while time.monotonic() - t_cmd < hold_s:
            conn.move(twist, duration=0.0)
            time.sleep(0.05)
        n_zero = len(points)
        y_zero = points[-1].odom_yaw_deg if points else 0.0
        mono_zero = time.monotonic_ns()
        conn.move(Twist(linear=Vector3(0, 0, 0), angular=Vector3(0, 0, 0)), duration=0.0)
        phase[0] = "post_zero"

        print(f"\n[B] |rx|=0.4 持 {hold_s}s 后发零, 分段统计 post_zero (对比 IMU rpy)")
        time.sleep(5.0)  # 零后连续 5s
        post_all = points[n_zero:]

        def slice_post(t_end_s: float) -> list[Point]:
            cutoff = mono_zero + int(t_end_s * 1e9)
            return [p for p in post_all if p.mono_ns <= cutoff]

        windows = {
            "0_0.5s": slice_post(0.5),
            "0.5_1.0s": [
                p for p in post_all if mono_zero + int(0.5e9) < p.mono_ns <= mono_zero + int(1.0e9)
            ],
            "1.0_2.0s": [
                p for p in post_all if mono_zero + int(1.0e9) < p.mono_ns <= mono_zero + int(2.0e9)
            ],
            "2.0_5.0s": [
                p for p in post_all if mono_zero + int(2.0e9) < p.mono_ns <= mono_zero + int(5.0e9)
            ],
            "0_5.0s_total": slice_post(5.0),
        }
        post_window_stats = {k: _window_stats(v) for k, v in windows.items()}
        for k, v in post_window_stats.items():
            print(f"  {k}: {json.dumps(v, ensure_ascii=False)}")

        # C: 旋转后再静止 — 漂移是否恢复基线
        phase[0] = "post_static"
        n_post_static = len(points)
        print(f"\n[C] 旋转后再静止 {post_static_s:.0f}s (漂移是否回到基线)")
        t1 = time.time()
        while time.time() - t1 < post_static_s:
            time.sleep(0.5)
        post_static = points[n_post_static:]
        post_stats = _window_stats(post_static)
        print(json.dumps(post_stats, ensure_ascii=False))

        # D: 反向再测一次 0.2 对比
        phase[0] = "cmd02"
        omega2 = 0.2
        hold2 = 1.5
        twist2 = Twist(linear=Vector3(0, 0, 0), angular=Vector3(0, 0, -omega2))
        t2 = time.monotonic()
        while time.monotonic() - t2 < hold2:
            conn.move(twist2, duration=0.0)
            time.sleep(0.05)
        n_zero2 = len(points)
        mono_zero2 = time.monotonic_ns()
        conn.move(Twist(linear=Vector3(0, 0, 0), angular=Vector3(0, 0, 0)), duration=0.0)
        phase[0] = "post_zero02"
        time.sleep(3.0)
        post02 = points[n_zero2:]
        post02_stats = {
            "0_3s": _window_stats([p for p in post02 if p.mono_ns <= mono_zero2 + int(3e9)]),
        }
        print(f"\n[D] |rx|=0.2 CW 1.5s 零后 3s: {json.dumps(post02_stats, ensure_ascii=False)}")

        # 判定
        pre_rate = pre_stats["mean_abs_rate_deg_s"]
        post_peak = post_window_stats["0_0.5s"]["max_abs_rate_deg_s"]
        post_total_5s = post_window_stats["0_5.0s_total"]["delta_yaw_deg"]
        post_static_rate = post_stats["mean_abs_rate_deg_s"]

        if pre_rate < 0.5 and post_peak > 5 * max(pre_rate, 0.05):
            lag_vs_drift = "NOT_DRIFT — post_zero yaw rate >> stationary drift baseline"
        elif abs(post_total_5s) < 2 * abs(pre_stats["delta_yaw_deg"]) * (5 / static_s) * 10:
            lag_vs_drift = "INCONCLUSIVE"
        else:
            lag_vs_drift = "LIKELY_DRIFT_OR_ESTIMATOR_CONTINUES"

        # IMU vs odom in post_zero first 0.5s
        imu_odom = post_window_stats["0_0.5s"]
        imu_delta = imu_odom.get("imu_delta_yaw_deg")
        odom_delta = imu_odom["delta_yaw_deg"]
        if imu_delta is not None:
            if abs(odom_delta) > 3 and abs(imu_delta) < 1:
                imu_conclusion = "odom_yaw_moves_but_imu_rpy_stable — odom估计滞后/滤波, 非机身在转"
            elif abs(imu_delta) > 3:
                imu_conclusion = "imu_rpy_also_moves — 机身可能仍在转或 IMU/odom 耦合"
            else:
                imu_conclusion = "both_small — 需更长窗口"
        else:
            imu_conclusion = "no_imu_data"

        report = {
            "round": 3,
            "test": "drift_vs_lag",
            "pre_static_s": static_s,
            "post_static_s": post_static_s,
            "pre_static": pre_stats,
            "post_zero_windows_rx04": post_window_stats,
            "post_rotate_static": post_stats,
            "post_zero_rx02": post02_stats,
            "yaw_at_zero_deg": round(y_zero, 2),
            "conclusions": {
                "stationary_drift_rate_deg_s": pre_rate,
                "stationary_total_yaw_deg_per_120s": pre_stats["delta_yaw_deg"],
                "post_zero_peak_rate_deg_s_rx04": post_peak,
                "post_zero_total_5s_deg_rx04": post_total_5s,
                "post_rotate_static_rate_deg_s": post_static_rate,
                "lag_vs_drift": lag_vs_drift,
                "imu_odom_comparison_0_5s": imu_conclusion,
                "interpretation": (
                    "若 post_zero 变化率 >> 静止漂移率, 则不是时间漂移; "
                    "若 IMU rpy 稳定而 odom yaw 变, 则是 odom 估计滞后; "
                    "若旋转后再静止 drift 率回到 pre_static 量级, 则非常驻漂移偏置."
                ),
            },
        }

        OUT.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y%m%d-%H%M%S")
        out = OUT / f"verify_r3_drift_{stamp}.json"
        out.write_text(json.dumps(report, indent=2, ensure_ascii=False))
        raw = OUT / f"samples_r3_{stamp}.jsonl"
        with raw.open("w") as f:
            for p in points:
                f.write(json.dumps(asdict(p)) + "\n")
        print(f"\n结论: {lag_vs_drift}")
        print(f"IMU: {imu_conclusion}")
        print(f"报告: {out}")
        return 0
    finally:
        try:
            conn.move(Twist(linear=Vector3(0, 0, 0), angular=Vector3(0, 0, 0)), duration=0.0)
            conn.liedown()
            conn.disconnect()
        except Exception:
            pass


if __name__ == "__main__":
    raise SystemExit(main())
