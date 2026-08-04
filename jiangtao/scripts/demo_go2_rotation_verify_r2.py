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

"""第二轮加长验证: 时钟偏差稳定性 + odom 滞后逐样本分析."""

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
from dimos.types.timestamped import to_timestamp
from dimos.utils.trigonometry import angle_diff

OUT = Path(__file__).resolve().parent.parent / "cache" / "rotation_verify"


@dataclass
class Sample:
    seq: int
    source_ts: float
    host_wall: float
    host_mono_ns: int
    yaw_deg: float
    phase: str  # static / cmd / post_zero


def _connect() -> tuple[UnitreeWebRTCConnection, list[Sample], list[str]]:
    samples: list[Sample] = []
    phase_ref = ["static"]
    seq = [0]

    def on_odom(msg: dict[str, Any]) -> None:
        try:
            data = msg["data"]
            source_ts = float(to_timestamp(data["header"]["stamp"]))
            odom = Odometry.from_msg(msg)  # type: ignore[arg-type]
            yaw = math.degrees(float(odom.orientation.to_euler().z))
            seq[0] += 1
            samples.append(
                Sample(
                    seq=seq[0],
                    source_ts=source_ts,
                    host_wall=time.time(),
                    host_mono_ns=time.monotonic_ns(),
                    yaw_deg=yaw,
                    phase=phase_ref[0],
                )
            )
        except Exception:
            return

    conn = UnitreeWebRTCConnection(
        connection_method="remote",
        username=os.environ["UNITREE_USERNAME"],
        password=os.environ["UNITREE_PASSWORD"],
        serial_number=os.environ["UNITREE_SERIAL"],
        region=os.environ.get("UNITREE_REGION", "cn"),
    )
    conn.conn.datachannel.pub_sub.subscribe(RTC_TOPIC["ROBOTODOM"], on_odom)
    print("StandUp 5s + BalanceStand + SwitchJoystick")
    conn.standup()
    time.sleep(5)
    conn.balance_stand()
    time.sleep(2)
    conn.publish_request(
        RTC_TOPIC["SPORT_MOD"],
        {"api_id": SPORT_CMD["SwitchJoystick"], "parameter": {"data": True}},
    )
    time.sleep(1)
    t0 = time.time()
    while time.time() - t0 < 15 and len(samples) < 20:
        time.sleep(0.05)
    return conn, samples, phase_ref


def _clock_stats(static: list[Sample]) -> dict[str, Any]:
    deltas = [s.host_wall - s.source_ts for s in static]
    src_dt = [(static[i].source_ts - static[i - 1].source_ts) * 1000 for i in range(1, len(static))]
    host_dt = [
        (static[i].host_mono_ns - static[i - 1].host_mono_ns) / 1e6 for i in range(1, len(static))
    ]
    # source_age on host wall after offset calibration
    baseline = statistics.median(deltas)
    ages = [s.host_wall - s.source_ts - baseline for s in static]
    bursts = []
    for i in range(1, len(static)):
        si = (static[i].source_ts - static[i - 1].source_ts) * 1000
        hi = (static[i].host_mono_ns - static[i - 1].host_mono_ns) / 1e6
        if si > 25 and hi < 8:
            bursts.append(i)
    return {
        "n": len(static),
        "duration_s": round((static[-1].host_mono_ns - static[0].host_mono_ns) / 1e9, 1),
        "hz": round(
            len(static) / max((static[-1].host_mono_ns - static[0].host_mono_ns) / 1e9, 0.1), 2
        ),
        "host_source_delta_median_s": round(baseline, 4),
        "host_source_delta_min_s": round(min(deltas), 4),
        "host_source_delta_max_s": round(max(deltas), 4),
        "host_source_delta_std_s": round(statistics.pstdev(deltas), 4) if len(deltas) > 1 else 0,
        "host_source_delta_p05_s": round(sorted(deltas)[int(len(deltas) * 0.05)], 4),
        "host_source_delta_p95_s": round(sorted(deltas)[int(len(deltas) * 0.95)], 4),
        "source_interval_median_ms": round(statistics.median(src_dt), 2),
        "host_interval_median_ms": round(statistics.median(host_dt), 2),
        "calibrated_source_age_median_ms": round(statistics.median(ages) * 1000, 2),
        "calibrated_source_age_p95_ms": round(sorted(ages)[int(len(ages) * 0.95)] * 1000, 2),
        "burst_count": len(bursts),
        "source_backward": sum(
            1
            for i in range(1, len(static))
            if static[i].source_ts < static[i - 1].source_ts - 0.001
        ),
    }


def _lag_trial(
    conn: UnitreeWebRTCConnection,
    samples: list[Sample],
    phase_ref: list[str],
    omega: float,
    hold_s: float,
) -> dict[str, Any]:
    """旋转后发零, 逐样本分析 source_ts 与 yaw 是否滞后."""
    phase_ref[0] = "cmd"
    y0 = samples[-1].yaw_deg if samples else 0.0
    src0 = samples[-1].source_ts if samples else 0.0
    mono_cmd_start = time.monotonic_ns()
    twist = Twist(linear=Vector3(0, 0, 0), angular=Vector3(0, 0, omega))
    t0 = time.monotonic()
    while time.monotonic() - t0 < hold_s:
        conn.move(twist, duration=0.0)
        time.sleep(0.05)

    mono_zero = time.monotonic_ns()
    conn.move(Twist(linear=Vector3(0, 0, 0), angular=Vector3(0, 0, 0)), duration=0.0)
    phase_ref[0] = "post_zero"
    n_at_zero = len(samples)
    y_at_zero = samples[-1].yaw_deg if samples else 0.0

    # 零后继续采 3s
    time.sleep(3.0)
    time.monotonic_ns()

    post = samples[n_at_zero:]
    [s for s in samples if s.phase == "cmd" and s.host_mono_ns >= mono_cmd_start]
    if not post:
        post = samples[-10:]

    # 滞后判据1: 零后 yaw 仍大幅变化
    [
        abs(angle_diff(math.radians(s.yaw_deg), math.radians(y_at_zero))) * 180 / math.pi
        for s in post
    ]
    # 滞后判据2: 零后收到的 source_ts 仍 <= 发零前最后 source_ts (旧样本)
    stale_by_source = sum(1 for s in post if s.source_ts <= src0 + 0.001)
    # 滞后判据3: host 间隔很小但 source 推进很多 (追帧)
    catch_up = 0
    for i in range(1, len(post)):
        si = (post[i].source_ts - post[i - 1].source_ts) * 1000
        hi = (post[i].host_mono_ns - post[i - 1].host_mono_ns) / 1e6
        if si > 20 and hi < 10:
            catch_up += 1

    # 用校准 offset 算 source_age
    static = [s for s in samples if s.phase == "static"]
    baseline = statistics.median([s.host_wall - s.source_ts for s in static]) if static else 10.0

    def max_age_ms(window_s: float) -> float:
        cutoff = mono_zero + int(window_s * 1e9)
        ages = [
            (s.host_wall - s.source_ts - baseline) * 1000 for s in post if s.host_mono_ns <= cutoff
        ]
        return round(max(ages), 2) if ages else 0.0

    return {
        "omega": omega,
        "hold_s": hold_s,
        "cmd_delta_yaw_deg": round(
            math.degrees(angle_diff(math.radians(y_at_zero), math.radians(y0))), 2
        ),
        "post_zero_samples": len(post),
        "yaw_drift_0_5s_deg": round(
            math.degrees(
                angle_diff(
                    math.radians(
                        post[min(len(post) - 1, 8)].yaw_deg if len(post) > 8 else y_at_zero
                    ),
                    math.radians(y_at_zero),
                )
            ),
            2,
        )
        if len(post) > 8
        else 0,
        "yaw_drift_1_0s_deg": round(
            math.degrees(
                angle_diff(
                    math.radians(
                        post[min(len(post) - 1, 17)].yaw_deg if len(post) > 17 else y_at_zero
                    ),
                    math.radians(y_at_zero),
                )
            ),
            2,
        )
        if len(post) > 17
        else 0,
        "yaw_drift_3_0s_deg": round(
            math.degrees(angle_diff(math.radians(post[-1].yaw_deg), math.radians(y_at_zero))), 2
        )
        if post
        else 0,
        "stale_samples_by_source_ts": stale_by_source,
        "catch_up_bursts_post_zero": catch_up,
        "max_calibrated_source_age_ms_0_5s": max_age_ms(0.5),
        "max_calibrated_source_age_ms_1_0s": max_age_ms(1.0),
        "max_calibrated_source_age_ms_3_0s": max_age_ms(3.0),
        "max_yaw_rate_post_zero_deg_s": round(
            max(
                (
                    abs(
                        math.degrees(
                            angle_diff(
                                math.radians(post[i].yaw_deg),
                                math.radians(post[i - 1].yaw_deg),
                            )
                        )
                    )
                    / max((post[i].host_mono_ns - post[i - 1].host_mono_ns) / 1e9, 0.001)
                    for i in range(1, len(post))
                ),
                default=0.0,
            ),
            2,
        ),
        "odom_lag_confirmed": bool(
            (
                post
                and abs(
                    math.degrees(
                        angle_diff(
                            math.radians(post[-1].yaw_deg),
                            math.radians(y_at_zero),
                        )
                    )
                )
                > 3
            )
            or stale_by_source > len(post) * 0.3
            or catch_up >= 2
        ),
    }


def main() -> int:
    static_s = float(os.getenv("VERIFY_STATIC_S", "180"))
    user = os.getenv("UNITREE_USERNAME")
    password = os.getenv("UNITREE_PASSWORD")
    serial = os.getenv("UNITREE_SERIAL")
    if not all([user, password, serial]):
        print("缺少凭据", file=sys.stderr)
        return 2

    print(f"=== 第二轮验证: 静止 {static_s:.0f}s + 滞后专项 ===")
    conn, samples, phase_ref = _connect()
    try:
        n0 = len(samples)
        print(f"\n[1/3] 静止采样 {static_s:.0f}s ... (已有 {n0} 样本)")
        t_start = time.time()
        last_print = 0
        while time.time() - t_start < static_s:
            time.sleep(0.5)
            elapsed = int(time.time() - t_start)
            if elapsed >= last_print + 30:
                last_print = elapsed
                print(f"  ... {elapsed}s, n={len(samples)}")

        static_samples = samples[n0:]
        clock = _clock_stats(static_samples)
        print("\n时钟统计:")
        print(json.dumps(clock, indent=2, ensure_ascii=False))

        print("\n[2/3] 滞后专项: |rx|=0.2 / 0.3 / 0.4 各 1 次 CCW + 零后 3s 逐样本")
        lag_trials = []
        for omega in [0.2, 0.3, 0.4]:
            lag_trials.append(
                _lag_trial(conn, samples, phase_ref, omega, 1.5 if omega == 0.2 else 1.0)
            )
            print(f"  |rx|={omega:.0%}: {json.dumps(lag_trials[-1], ensure_ascii=False)}")
            time.sleep(2.0)

        print("\n[3/3] 闭环小角复测 10° / 15°")
        rotates = []
        for tgt in [10.0, 15.0]:
            start_yaw = math.radians(samples[-1].yaw_deg)
            target_rad = math.radians(tgt)
            acc = 0.0
            last = start_yaw
            min_o = float(os.getenv("DIMOS_ROTATE_MIN_RAD_S", "0.2"))
            tol = math.radians(float(os.getenv("DIMOS_ROTATE_TOLERANCE_DEG", "5")))
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline:
                cur = math.radians(samples[-1].yaw_deg)
                acc += angle_diff(cur, last)
                last = cur
                rem = target_rad - acc
                if abs(rem) <= tol:
                    break
                om = max(-0.8, min(0.8, 1.2 * rem))
                if abs(om) < min_o:
                    om = min_o if rem >= 0 else -min_o
                conn.move(Twist(linear=Vector3(0, 0, 0), angular=Vector3(0, 0, om)), duration=0.0)
                time.sleep(0.05)
            conn.move(Twist(linear=Vector3(0, 0, 0), angular=Vector3(0, 0, 0)), duration=0.0)
            time.sleep(0.35)
            achieved = math.degrees(acc)
            rotates.append(
                {"target": tgt, "achieved": round(achieved, 2), "err": round(tgt - achieved, 2)}
            )
            print(f"  target={tgt}° achieved={achieved:.1f}° err={tgt - achieved:.1f}°")
            time.sleep(1.0)

        # 与第一轮对比
        round1_path = OUT / "verify_20260729-195338.json"
        round1 = json.loads(round1_path.read_text()) if round1_path.exists() else {}

        report = {
            "round": 2,
            "static_s": static_s,
            "clock": clock,
            "lag_trials": lag_trials,
            "rotate_small": rotates,
            "round1_comparison": {
                "clock_delta_median_r1": round1.get("host_source_delta_median"),
                "clock_delta_median_r2": clock.get("host_source_delta_median_s"),
                "odom_hz_r1": round1.get("odom_hz"),
                "odom_hz_r2": clock.get("hz"),
                "conclusion_consistent": abs(
                    (round1.get("host_source_delta_median") or 0)
                    - (clock.get("host_source_delta_median_s") or 0)
                )
                < 0.5,
            },
            "source_ts_gate_feasibility": {
                "clock_offset_stable": clock.get("host_source_delta_std_s", 99) < 0.1,
                "calibrated_age_at_rest_ms": clock.get("calibrated_source_age_median_ms"),
                "recommendation": None,
            },
        }

        if clock.get("host_source_delta_std_s", 99) < 0.1:
            report["source_ts_gate_feasibility"]["recommendation"] = (
                "可行但需两步: (1) 静止标定 baseline=median(host-source); "
                "(2) 运行时用 calibrated_age=host-source-baseline, 超阈值(如200ms)则发零等待."
                "注意: baseline是时钟偏差+最小链路延迟合量, age阈值需真机标定, 不能直接用host-source绝对值."
            )
        else:
            report["source_ts_gate_feasibility"]["recommendation"] = (
                "不建议单独用source_ts; 时钟偏差不稳定, 改用host_monotonic+seq判断新样本"
            )

        OUT.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y%m%d-%H%M%S")
        out = OUT / f"verify_r2_{stamp}.json"
        out.write_text(json.dumps(report, indent=2, ensure_ascii=False))
        # 保存逐样本用于审计
        raw_out = OUT / f"samples_r2_{stamp}.jsonl"
        with raw_out.open("w") as f:
            for s in samples:
                f.write(json.dumps(asdict(s)) + "\n")
        print(f"\n报告: {out}\n样本: {raw_out} ({len(samples)} lines)")
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
