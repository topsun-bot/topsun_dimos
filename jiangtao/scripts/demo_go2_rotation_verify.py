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

"""Go2 4G 真机验证: odom 时间戳 / 零速停止 / 闭环旋转.

对应计划: jiangtao/plan/2026-07-29-Go2旋转反馈时间戳与零速度执行验证计划.md
凭据从环境变量读取 (参考 jiangtao/run-self.md).
"""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass, field
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
from dimos.types.timestamped import to_timestamp
from dimos.utils.trigonometry import angle_diff

OUT_DIR = Path(__file__).resolve().parent.parent / "cache" / "rotation_verify"


@dataclass
class OdomSample:
    seq: int
    source_ts: float
    host_rx_wall_ts: float
    host_rx_monotonic_ns: int
    yaw_rad: float
    host_source_delta: float


@dataclass
class StopTrial:
    omega: float
    hold_s: float
    direction: str
    delta_during_cmd_deg: float
    delta_after_zero_0_5s_deg: float
    delta_after_zero_1_0s_deg: float
    delta_after_zero_2_0s_deg: float
    imu_gyro_z_last: float | None


@dataclass
class RotateTrial:
    target_deg: float
    achieved_deg: float
    ok: bool
    ticks: int
    timeout: bool
    max_same_tf_repeats: int
    avg_control_period_ms: float


@dataclass
class VerifyReport:
    connection: str
    serial: str
    static_duration_s: float
    odom_hz: float
    host_source_delta_median: float
    host_source_delta_p95: float
    host_source_delta_std: float
    source_interval_median_ms: float
    host_interval_median_ms: float
    backlog_burst_count: int
    timestamp_conclusion: str
    lowstate_has_gyroscope: bool
    lowstate_keys: list[str]
    imu_keys: list[str]
    stop_trials: list[StopTrial] = field(default_factory=list)
    rotate_trials: list[RotateTrial] = field(default_factory=list)
    hypothesis: dict[str, str] = field(default_factory=dict)
    recommendations: list[str] = field(default_factory=list)


def _setup(conn: UnitreeWebRTCConnection) -> tuple[Any, Any, list[OdomSample], dict[str, Any]]:
    odom_samples: list[OdomSample] = []
    lowstate_info: dict[str, Any] = {
        "keys": [],
        "imu_keys": [],
        "has_gyroscope": False,
        "sample": None,
    }
    seq = [0]

    def on_raw_odom(msg: dict[str, Any]) -> None:
        try:
            data = msg.get("data", msg)
            stamp = data["header"]["stamp"]
            source_ts = float(to_timestamp(stamp))
            odom = Odometry.from_msg(msg)  # type: ignore[arg-type]
            yaw = float(odom.orientation.to_euler().z)
            host_wall = time.time()
            host_mono = time.monotonic_ns()
            seq[0] += 1
            odom_samples.append(
                OdomSample(
                    seq=seq[0],
                    source_ts=source_ts,
                    host_rx_wall_ts=host_wall,
                    host_rx_monotonic_ns=host_mono,
                    yaw_rad=yaw,
                    host_source_delta=host_wall - source_ts,
                )
            )
        except Exception:
            return

    def on_lowstate(msg: dict[str, Any]) -> None:
        if lowstate_info["sample"] is not None:
            return
        try:
            data = msg.get("data", msg)
            lowstate_info["keys"] = sorted(str(k) for k in data.keys())
            imu = data.get("imu_state") or data.get("imu") or {}
            if isinstance(imu, dict):
                lowstate_info["imu_keys"] = sorted(str(k) for k in imu.keys())
                lowstate_info["has_gyroscope"] = "gyroscope" in imu or "gyro" in imu
            lowstate_info["sample"] = {"imu_state": imu}
        except Exception:
            return

    conn.conn.datachannel.pub_sub.subscribe(RTC_TOPIC["ROBOTODOM"], on_raw_odom)
    conn.conn.datachannel.pub_sub.subscribe(RTC_TOPIC["LOW_STATE"], on_lowstate)

    print("StandUp (5s) + BalanceStand + SwitchJoystick ...")
    conn.standup()
    time.sleep(5)
    conn.balance_stand()
    time.sleep(2)
    conn.publish_request(
        RTC_TOPIC["SPORT_MOD"],
        {"api_id": SPORT_CMD["SwitchJoystick"], "parameter": {"data": True}},
    )
    time.sleep(1)

    # 等待 odom
    t0 = time.time()
    while time.time() - t0 < 15 and len(odom_samples) < 20:
        time.sleep(0.1)
    return on_raw_odom, on_lowstate, odom_samples, lowstate_info


def _analyze_timestamps(samples: list[OdomSample], duration_s: float) -> dict[str, Any]:
    if len(samples) < 5:
        return {"error": "insufficient samples"}

    deltas = [s.host_source_delta for s in samples]
    source_intervals = [
        (samples[i].source_ts - samples[i - 1].source_ts) * 1000
        for i in range(1, len(samples))
        if samples[i].source_ts >= samples[i - 1].source_ts
    ]
    host_intervals = [
        (samples[i].host_rx_monotonic_ns - samples[i - 1].host_rx_monotonic_ns) / 1e6
        for i in range(1, len(samples))
    ]

    # 追帧: host 间隔远小于 source 间隔的累计
    bursts = 0
    for i in range(1, len(samples)):
        si = (samples[i].source_ts - samples[i - 1].source_ts) * 1000
        hi = (samples[i].host_rx_monotonic_ns - samples[i - 1].host_rx_monotonic_ns) / 1e6
        if si > 30 and hi < 10:
            bursts += 1

    med_d = statistics.median(deltas)
    std_d = statistics.pstdev(deltas) if len(deltas) > 1 else 0.0
    sorted_d = sorted(deltas)
    p95_d = sorted_d[int(len(sorted_d) * 0.95)]

    # 分类
    if std_d < 0.5 and bursts < 3:
        conclusion = "CLOCK_OFFSET_DOMINANT"
    elif bursts >= 5 or std_d > 2.0:
        conclusion = "DYNAMIC_BACKLOG_CONFIRMED"
    elif std_d >= 0.5 and bursts >= 3:
        conclusion = "CLOCK_OFFSET_AND_BACKLOG"
    else:
        conclusion = "SOURCE_TIMESTAMP_UNUSABLE_OR_INCONCLUSIVE"

    # 检查 source 倒退
    backward = sum(
        1 for i in range(1, len(samples)) if samples[i].source_ts < samples[i - 1].source_ts - 0.001
    )

    return {
        "odom_hz": round(len(samples) / max(duration_s, 0.1), 2),
        "host_source_delta_median": round(med_d, 3),
        "host_source_delta_p95": round(p95_d, 3),
        "host_source_delta_std": round(std_d, 3),
        "source_interval_median_ms": round(statistics.median(source_intervals), 2)
        if source_intervals
        else 0,
        "host_interval_median_ms": round(statistics.median(host_intervals), 2)
        if host_intervals
        else 0,
        "backlog_burst_count": bursts,
        "source_backward_count": backward,
        "timestamp_conclusion": conclusion
        if backward < 3
        else "SOURCE_TIMESTAMP_UNUSABLE_OR_INCONCLUSIVE",
    }


def _current_yaw(samples: list[OdomSample]) -> float:
    return samples[-1].yaw_rad if samples else 0.0


def _stop(conn: UnitreeWebRTCConnection) -> None:
    conn.move(Twist(linear=Vector3(0, 0, 0), angular=Vector3(0, 0, 0)), duration=0.0)


def _hold_rotate(
    conn: UnitreeWebRTCConnection,
    samples: list[OdomSample],
    omega: float,
    hold_s: float,
) -> float:
    y0 = _current_yaw(samples)
    twist = Twist(linear=Vector3(0, 0, 0), angular=Vector3(0, 0, omega))
    t0 = time.monotonic()
    while time.monotonic() - t0 < hold_s:
        conn.move(twist, duration=0.0)
        time.sleep(0.05)
    return math.degrees(angle_diff(_current_yaw(samples), y0))


def _delta_since(samples: list[OdomSample], y0: float, wait_s: float) -> float:
    time.sleep(wait_s)
    return math.degrees(angle_diff(_current_yaw(samples), y0))


def _zero_stop_trials(
    conn: UnitreeWebRTCConnection,
    samples: list[OdomSample],
    lowstate_info: dict[str, Any],
) -> list[StopTrial]:
    trials: list[StopTrial] = []
    for omega in [0.2, 0.3, 0.4]:
        for sign, direction in [(1.0, "ccw"), (-1.0, "cw")]:
            hold_s = 1.5 if omega <= 0.2 else 1.0
            y_start = _current_yaw(samples)
            n0 = len(samples)
            d_cmd = _hold_rotate(conn, samples, sign * omega, hold_s)
            y_at_zero = _current_yaw(samples)
            t_zero = time.monotonic()
            _stop(conn)
            d_05 = math.degrees(angle_diff(_current_yaw(samples), y_at_zero))
            time.sleep(max(0.0, 0.5 - (time.monotonic() - t_zero)))
            d_05_total = math.degrees(angle_diff(_current_yaw(samples), y_at_zero))
            time.sleep(0.5)
            d_10_total = math.degrees(angle_diff(_current_yaw(samples), y_at_zero))
            time.sleep(1.0)
            d_20_total = math.degrees(angle_diff(_current_yaw(samples), y_at_zero))
            imu = lowstate_info.get("sample") or {}
            imu_state = imu.get("imu_state") or {}
            gyro_z = None
            if isinstance(imu_state, dict):
                g = imu_state.get("gyroscope") or imu_state.get("gyro")
                if isinstance(g, (list, tuple)) and len(g) >= 3:
                    gyro_z = float(g[2])
            trials.append(
                StopTrial(
                    omega=omega,
                    hold_s=hold_s,
                    direction=direction,
                    delta_during_cmd_deg=round(d_cmd, 2),
                    delta_after_zero_0_5s_deg=round(d_05_total, 2),
                    delta_after_zero_1_0s_deg=round(d_10_total, 2),
                    delta_after_zero_2_0s_deg=round(d_20_total, 2),
                    imu_gyro_z_last=gyro_z,
                )
            )
            print(
                f"  stop |rx|={omega:.0%} {direction}: cmd={d_cmd:+.1f}° "
                f"post0={d_05_total:+.1f}° post1={d_10_total:+.1f}° post2={d_20_total:+.1f}°"
            )
            time.sleep(0.8)
            _ = y_start
            _ = n0
            _ = d_05
    return trials


def _closed_loop_rotate(
    conn: UnitreeWebRTCConnection,
    samples: list[OdomSample],
    target_deg: float,
) -> RotateTrial:
    """模拟 rotate_in_place_degrees 的 20Hz 闭环."""
    tolerance_rad = math.radians(float(os.getenv("DIMOS_ROTATE_TOLERANCE_DEG", "5")))
    max_omega = float(os.getenv("DIMOS_ROTATE_MAX_RAD_S", "0.8"))
    min_omega = float(os.getenv("DIMOS_ROTATE_MIN_RAD_S", "0.2"))
    k_omega = float(os.getenv("DIMOS_ROTATE_KP", "1.2"))
    control_hz = 20.0
    settle_s = 0.35
    timeout_s = 30.0

    start_yaw = _current_yaw(samples)
    target_rad = math.radians(target_deg)
    accumulated = 0.0
    last_yaw = start_yaw
    same_tf_repeats = 0
    max_same = 0
    last_sample_seq = samples[-1].seq if samples else 0
    periods: list[float] = []
    ticks = 0
    deadline = time.monotonic() + timeout_s
    timed_out = False

    while time.monotonic() < deadline:
        t_tick = time.monotonic()
        cur_seq = samples[-1].seq if samples else 0
        if cur_seq == last_sample_seq:
            same_tf_repeats += 1
        else:
            same_tf_repeats = 0
            last_sample_seq = cur_seq
        max_same = max(max_same, same_tf_repeats)

        current_yaw = _current_yaw(samples)
        accumulated += angle_diff(current_yaw, last_yaw)
        last_yaw = current_yaw
        remaining = target_rad - accumulated
        if abs(remaining) <= tolerance_rad:
            break

        omega = max(-max_omega, min(max_omega, k_omega * remaining))
        if abs(omega) < min_omega:
            omega = min_omega if remaining >= 0 else -min_omega

        conn.move(
            Twist(linear=Vector3(0, 0, 0), angular=Vector3(0, 0, omega)),
            duration=0.0,
        )
        ticks += 1
        time.sleep(1.0 / control_hz)
        periods.append((time.monotonic() - t_tick) * 1000)
    else:
        timed_out = True

    _stop(conn)
    time.sleep(settle_s)
    achieved = math.degrees(accumulated)
    ok = abs(accumulated - target_rad) < tolerance_rad * 2
    avg_period = statistics.mean(periods) if periods else 0.0
    print(
        f"  rotate target={target_deg:+.0f}° achieved={achieved:+.1f}° ok={ok} "
        f"ticks={ticks} max_same_tf={max_same}"
    )
    return RotateTrial(
        target_deg=target_deg,
        achieved_deg=round(achieved, 2),
        ok=ok,
        ticks=ticks,
        timeout=timed_out,
        max_same_tf_repeats=max_same,
        avg_control_period_ms=round(avg_period, 2),
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--static-s", type=float, default=60.0, help="静止采样秒数")
    parser.add_argument("--skip-rotate", action="store_true")
    args = parser.parse_args()

    user = os.getenv("UNITREE_USERNAME")
    password = os.getenv("UNITREE_PASSWORD")
    serial = os.getenv("UNITREE_SERIAL")
    region = os.getenv("UNITREE_REGION", "cn")
    if not user or not password or not serial:
        print("缺少 UNITREE_USERNAME/PASSWORD/SERIAL", file=sys.stderr)
        return 2

    print(f"Connecting 4G Remote serial={serial} ...")
    conn = UnitreeWebRTCConnection(
        connection_method="remote",
        username=user,
        password=password,
        serial_number=serial,
        region=region,
    )
    try:
        _, _, samples, lowstate_info = _setup(conn)
        if len(samples) < 10:
            print("ERROR: odom 样本不足", file=sys.stderr)
            return 1

        print(f"\n=== 验证一: 静止 odom 时间戳 ({args.static_s:.0f}s) ===")
        n0 = len(samples)
        t_static = time.time()
        while time.time() - t_static < args.static_s:
            time.sleep(0.2)
        static_samples = samples[n0:]
        ts_stats = _analyze_timestamps(static_samples, args.static_s)
        print(json.dumps(ts_stats, indent=2, ensure_ascii=False))

        print("\n=== 验证二: LOW_STATE payload ===")
        print(f"  keys={lowstate_info['keys']}")
        print(f"  imu_keys={lowstate_info['imu_keys']}")
        print(f"  has_gyroscope={lowstate_info['has_gyroscope']}")

        stop_trials: list[StopTrial] = []
        rotate_trials: list[RotateTrial] = []
        if not args.skip_rotate:
            print("\n=== 验证三: 零速停止 (|rx|=0.2/0.3/0.4) ===")
            stop_trials = _zero_stop_trials(conn, samples, lowstate_info)

            print("\n=== 验证四: 闭环 rotate_in_place 模拟 ===")
            for tgt in [10.0, 15.0, 30.0, -30.0, 60.0]:
                rotate_trials.append(_closed_loop_rotate(conn, samples, tgt))
                time.sleep(1.0)

        # 假设判定
        hyp: dict[str, str] = {}
        hyp["H1"] = (
            "CONFIRMED"
            if ts_stats.get("timestamp_conclusion") == "CLOCK_OFFSET_DOMINANT"
            else "INCONCLUSIVE"
        )
        hyp["H2"] = "CONFIRMED" if ts_stats.get("backlog_burst_count", 0) >= 5 else "REJECTED"
        hyp["H4"] = "CONFIRMED" if ts_stats.get("source_backward_count", 0) >= 3 else "REJECTED"
        avg_post2 = (
            statistics.mean(abs(t.delta_after_zero_2_0s_deg) for t in stop_trials)
            if stop_trials
            else 0.0
        )
        hyp["H6"] = "CONFIRMED" if avg_post2 > 3.0 else "INCONCLUSIVE"
        hyp["H8"] = (
            "CONFIRMED"
            if any(t.max_same_tf_repeats >= 3 for t in rotate_trials)
            else "INCONCLUSIVE"
        )

        recs: list[str] = []
        if not lowstate_info["has_gyroscope"]:
            recs.append("Remote LOW_STATE 无 gyroscope, 停止确认只能依赖 odom yaw 差分")
        if ts_stats.get("timestamp_conclusion") != "CLOCK_OFFSET_DOMINANT":
            recs.append("禁止用 host-source 直接当网络延迟; 应用 seq/monotonic 判断反馈新鲜度")
        recs.append("零命令后至少等待 0.5~1.0s 并检查 odom 是否仍在变化, 再累计 remaining")
        small_fail = [t for t in rotate_trials if abs(t.target_deg) <= 15 and not t.ok]
        if small_fail:
            recs.append(
                f"小角度({', '.join(str(int(t.target_deg)) for t in small_fail)}°)闭环不可靠, "
                "建议改为: 短脉冲->显式零->等新 odom->重算 remaining"
            )

        report = VerifyReport(
            connection="remote_4g",
            serial=serial,
            static_duration_s=args.static_s,
            odom_hz=ts_stats.get("odom_hz", 0),
            host_source_delta_median=ts_stats.get("host_source_delta_median", 0),
            host_source_delta_p95=ts_stats.get("host_source_delta_p95", 0),
            host_source_delta_std=ts_stats.get("host_source_delta_std", 0),
            source_interval_median_ms=ts_stats.get("source_interval_median_ms", 0),
            host_interval_median_ms=ts_stats.get("host_interval_median_ms", 0),
            backlog_burst_count=ts_stats.get("backlog_burst_count", 0),
            timestamp_conclusion=ts_stats.get("timestamp_conclusion", "UNKNOWN"),
            lowstate_has_gyroscope=lowstate_info["has_gyroscope"],
            lowstate_keys=lowstate_info["keys"],
            imu_keys=lowstate_info["imu_keys"],
            stop_trials=stop_trials,
            rotate_trials=rotate_trials,
            hypothesis=hyp,
            recommendations=recs,
        )

        OUT_DIR.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y%m%d-%H%M%S")
        out_json = OUT_DIR / f"verify_{stamp}.json"
        out_json.write_text(json.dumps(asdict(report), indent=2, ensure_ascii=False))
        print(f"\nJSON: {out_json}")
        return 0
    finally:
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
