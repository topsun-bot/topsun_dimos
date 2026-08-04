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

"""Validate explicit-zero stopping behavior on a Remote/4G Go2."""

from __future__ import annotations

import argparse
import asyncio
from collections import deque
from dataclasses import asdict, dataclass
from itertools import pairwise
import json
import math
import os
from pathlib import Path
import statistics
import sys
import threading
import time
from typing import Any

from unitree_webrtc_connect.constants import RTC_TOPIC, SPORT_CMD

from dimos.robot.unitree.connection import UnitreeWebRTCConnection
from dimos.utils.trigonometry import angle_diff

OUT_DIR = Path(__file__).resolve().parent.parent / "cache" / "rotation_stop_validation"


@dataclass
class ImuSample:
    host_monotonic: float
    yaw_rad: float
    roll_rad: float
    pitch_rad: float


@dataclass
class OdomSample:
    host_monotonic: float
    x_m: float
    y_m: float


@dataclass
class StopTrial:
    magnitude: float
    direction: int
    repeat: int
    hold_s: float
    nonzero_delta_deg: float
    zero_requested_monotonic: float
    zero_local_send_duration_ms: float
    zero_device_acked: bool
    stop_threshold_deg_s: float
    stop_started_ms: float | None
    stop_confirmed_ms: float | None
    post_zero_delta_deg: float
    stopped: bool
    translation_m: float
    imu_age_before_ms: float
    post_zero_samples: list[dict[str, float]]


class InspectTelemetry:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.imu: deque[ImuSample] = deque(maxlen=4000)
        self.odom: deque[OdomSample] = deque(maxlen=2000)
        self.battery_soc: int | None = None
        self.lowstate_data_keys: list[str] = []
        self.lowstate_imu_keys: list[str] = []

    def on_lowstate(self, msg: Any) -> None:
        try:
            data = msg["data"]
            imu = data["imu_state"]
            roll, pitch, yaw = (float(value) for value in imu["rpy"])
            battery = data.get("bms_state", {}).get("soc")
            sample = ImuSample(time.monotonic(), yaw, roll, pitch)
            with self.lock:
                self.imu.append(sample)
                if battery is not None:
                    self.battery_soc = int(battery)
                self.lowstate_data_keys = sorted(str(key) for key in data)
                self.lowstate_imu_keys = sorted(str(key) for key in imu)
        except (KeyError, TypeError, ValueError):
            return

    def on_odom(self, pose: Any) -> None:
        try:
            sample = OdomSample(
                time.monotonic(),
                float(pose.position.x),
                float(pose.position.y),
            )
            with self.lock:
                self.odom.append(sample)
        except (AttributeError, TypeError, ValueError):
            return

    def snapshots(self) -> tuple[list[ImuSample], list[OdomSample]]:
        with self.lock:
            return list(self.imu), list(self.odom)

    def require_fresh(self, maximum_age_s: float = 0.50) -> float:
        imu, odom = self.snapshots()
        if not imu or not odom:
            raise RuntimeError("IMU or odometry telemetry is missing")
        now = time.monotonic()
        imu_age = now - imu[-1].host_monotonic
        odom_age = now - odom[-1].host_monotonic
        if imu_age > maximum_age_s or odom_age > maximum_age_s:
            raise RuntimeError(f"stale telemetry: imu={imu_age:.3f}s odom={odom_age:.3f}s")
        if abs(imu[-1].roll_rad) > 0.35 or abs(imu[-1].pitch_rad) > 0.35:
            raise RuntimeError("body tilt exceeds 20 degrees")
        return imu_age


def _send_raw(conn: UnitreeWebRTCConnection, rx: float) -> None:
    if not -1.0 <= rx <= 1.0:
        raise ValueError(f"rx out of range: {rx}")

    async def send() -> None:
        conn.conn.datachannel.pub_sub.publish_without_callback(
            RTC_TOPIC["WIRELESS_CONTROLLER"],
            data={"lx": 0.0, "ly": 0.0, "rx": float(rx), "ry": 0.0},
        )

    asyncio.run_coroutine_threadsafe(send(), conn.loop).result(timeout=1.0)


def _send_zero_burst(
    conn: UnitreeWebRTCConnection,
    duration_s: float = 0.6,
) -> None:
    deadline = time.monotonic() + duration_s
    while time.monotonic() < deadline:
        _send_raw(conn, 0.0)
        time.sleep(0.05)


def _unwrapped_delta(samples: list[ImuSample]) -> float:
    return float(
        sum(
            angle_diff(current.yaw_rad, previous.yaw_rad) for previous, current in pairwise(samples)
        )
    )


def _translation(samples: list[OdomSample]) -> float:
    if len(samples) < 2:
        return 0.0
    origin = samples[0]
    return max(math.hypot(sample.x_m - origin.x_m, sample.y_m - origin.y_m) for sample in samples)


def _parse_floats(value: str) -> list[float]:
    return [float(item.strip()) for item in value.split(",") if item.strip()]


def _yaw_rates(samples: list[ImuSample]) -> list[tuple[float, float]]:
    rates: list[tuple[float, float]] = []
    for previous, current in pairwise(samples):
        dt = current.host_monotonic - previous.host_monotonic
        if dt <= 0.005 or dt > 0.25:
            continue
        rate = math.degrees(angle_diff(current.yaw_rad, previous.yaw_rad)) / dt
        rates.append((current.host_monotonic, rate))
    return rates


def _stationary_threshold(
    conn: UnitreeWebRTCConnection,
    telemetry: InspectTelemetry,
    duration_s: float,
) -> tuple[float, dict[str, float]]:
    _send_zero_burst(conn, 1.0)
    start = time.monotonic()
    max_imu_age = 0.0
    max_odom_age = 0.0
    stale_over_200ms_checks = 0
    freshness_checks = 0
    while time.monotonic() - start < duration_s:
        imu, odom = telemetry.snapshots()
        if not imu or not odom:
            raise RuntimeError("IMU or odometry telemetry is missing")
        now = time.monotonic()
        imu_age = now - imu[-1].host_monotonic
        odom_age = now - odom[-1].host_monotonic
        max_imu_age = max(max_imu_age, imu_age)
        max_odom_age = max(max_odom_age, odom_age)
        freshness_checks += 1
        if imu_age > 0.20 or odom_age > 0.20:
            stale_over_200ms_checks += 1
        if imu_age > 1.0 or odom_age > 1.0:
            raise RuntimeError(
                f"telemetry gap exceeded 1s: imu={imu_age:.3f}s odom={odom_age:.3f}s"
            )
        if abs(imu[-1].roll_rad) > 0.35 or abs(imu[-1].pitch_rad) > 0.35:
            raise RuntimeError("body tilt exceeds 20 degrees")
        time.sleep(0.05)
    imu, _ = telemetry.snapshots()
    samples = [sample for sample in imu if sample.host_monotonic >= start]
    rates = [rate for _, rate in _yaw_rates(samples)]
    if len(rates) < 20:
        raise RuntimeError("not enough stationary IMU samples")
    mean = statistics.mean(rates)
    sigma = statistics.stdev(rates)
    threshold = max(3.0 * sigma, math.degrees(0.03))
    return threshold, {
        "duration_s": duration_s,
        "samples": float(len(samples)),
        "rate_samples": float(len(rates)),
        "mean_deg_s": mean,
        "sigma_deg_s": sigma,
        "threshold_deg_s": threshold,
        "max_abs_deg_s": max(abs(rate) for rate in rates),
        "max_imu_age_ms": max_imu_age * 1000.0,
        "max_odom_age_ms": max_odom_age * 1000.0,
        "stale_over_200ms_checks": float(stale_over_200ms_checks),
        "freshness_checks": float(freshness_checks),
    }


def _stop_window(
    samples: list[ImuSample],
    zero_time: float,
    threshold_deg_s: float,
) -> tuple[float | None, float | None]:
    rates = [(ts, rate) for ts, rate in _yaw_rates(samples) if ts >= zero_time]
    for index, (window_start, _) in enumerate(rates):
        window: list[tuple[float, float]] = []
        for item in rates[index:]:
            window.append(item)
            if item[0] - window_start >= 0.30:
                break
        if len(window) < 5 or window[-1][0] - window_start < 0.30:
            continue
        if all(abs(rate) < threshold_deg_s for _, rate in window):
            return window_start, window[-1][0]
    return None, None


def _relative_samples(
    samples: list[ImuSample],
    zero_time: float,
) -> list[dict[str, float]]:
    return [
        {
            "time_from_zero_ms": (sample.host_monotonic - zero_time) * 1000.0,
            "yaw_deg": math.degrees(sample.yaw_rad),
            "roll_deg": math.degrees(sample.roll_rad),
            "pitch_deg": math.degrees(sample.pitch_rad),
        }
        for sample in samples
    ]


def _run_trial(
    conn: UnitreeWebRTCConnection,
    telemetry: InspectTelemetry,
    *,
    magnitude: float,
    direction: int,
    repeat: int,
    hold_s: float,
    threshold_deg_s: float,
    translation_limit_m: float,
    post_zero_limit_deg: float,
) -> StopTrial:
    _send_zero_burst(conn, 0.6)
    time.sleep(0.5)
    imu_age = telemetry.require_fresh(maximum_age_s=0.50)
    trial_start = time.monotonic()
    deadline = trial_start + hold_s
    command = direction * magnitude

    while time.monotonic() < deadline:
        telemetry.require_fresh(maximum_age_s=0.50)
        _send_raw(conn, command)
        time.sleep(0.05)
        _, odom = telemetry.snapshots()
        active_odom = [sample for sample in odom if sample.host_monotonic >= trial_start]
        if _translation(active_odom) > translation_limit_m:
            _send_zero_burst(conn)
            raise RuntimeError("translation safety limit exceeded during nonzero command")

    zero_requested = time.monotonic()
    _send_raw(conn, 0.0)
    zero_send_finished = time.monotonic()
    observation_end = zero_requested + 2.0

    while time.monotonic() < observation_end:
        telemetry.require_fresh(maximum_age_s=0.50)
        imu, odom = telemetry.snapshots()
        post_imu = [sample for sample in imu if sample.host_monotonic >= zero_requested]
        active_odom = [sample for sample in odom if sample.host_monotonic >= trial_start]
        post_zero_delta_deg = abs(math.degrees(_unwrapped_delta(post_imu)))
        if post_zero_delta_deg > post_zero_limit_deg:
            _send_zero_burst(conn)
            raise RuntimeError(
                "post-zero rotation safety limit exceeded: "
                f"{post_zero_delta_deg:.2f}deg > {post_zero_limit_deg:.2f}deg"
            )
        if _translation(active_odom) > translation_limit_m:
            _send_zero_burst(conn)
            raise RuntimeError("translation safety limit exceeded after zero")
        time.sleep(0.02)

    imu, odom = telemetry.snapshots()
    nonzero_imu = [
        sample for sample in imu if trial_start - 0.10 <= sample.host_monotonic <= zero_requested
    ]
    post_imu = [
        sample
        for sample in imu
        if zero_requested - 0.10 <= sample.host_monotonic <= observation_end
    ]
    active_odom = [
        sample for sample in odom if trial_start - 0.10 <= sample.host_monotonic <= observation_end
    ]
    stop_started, stop_confirmed = _stop_window(post_imu, zero_requested, threshold_deg_s)
    stopped = stop_started is not None and stop_confirmed is not None
    post_after_zero = [sample for sample in post_imu if sample.host_monotonic >= zero_requested]
    result = StopTrial(
        magnitude=magnitude,
        direction=direction,
        repeat=repeat,
        hold_s=hold_s,
        nonzero_delta_deg=math.degrees(_unwrapped_delta(nonzero_imu)),
        zero_requested_monotonic=zero_requested,
        zero_local_send_duration_ms=(zero_send_finished - zero_requested) * 1000.0,
        zero_device_acked=False,
        stop_threshold_deg_s=threshold_deg_s,
        stop_started_ms=(
            (stop_started - zero_requested) * 1000.0 if stop_started is not None else None
        ),
        stop_confirmed_ms=(
            (stop_confirmed - zero_requested) * 1000.0 if stop_confirmed is not None else None
        ),
        post_zero_delta_deg=math.degrees(_unwrapped_delta(post_after_zero)),
        stopped=stopped,
        translation_m=_translation(active_odom),
        imu_age_before_ms=imu_age * 1000.0,
        post_zero_samples=_relative_samples(post_imu, zero_requested),
    )
    _send_zero_burst(conn, 0.6)
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--magnitudes", default="0.2,0.3,0.4")
    parser.add_argument("--repeats", type=int, default=10)
    parser.add_argument("--hold", type=float, default=1.0)
    parser.add_argument("--stationary-baseline", type=float, default=30.0)
    parser.add_argument("--translation-limit-m", type=float, default=0.05)
    parser.add_argument("--post-zero-limit-deg", type=float, default=15.0)
    args = parser.parse_args()
    magnitudes = _parse_floats(args.magnitudes)
    if not magnitudes or any(value <= 0.0 or value > 0.5 for value in magnitudes):
        print("magnitudes must be in (0, 0.5]", file=sys.stderr)
        return 2
    if args.repeats < 1 or args.repeats > 20:
        print("repeats must be in [1, 20]", file=sys.stderr)
        return 2

    username = os.getenv("UNITREE_USERNAME")
    password = os.getenv("UNITREE_PASSWORD")
    serial = os.getenv("UNITREE_SERIAL")
    if not username or not password or not serial:
        print("missing UNITREE_USERNAME / PASSWORD / SERIAL", file=sys.stderr)
        return 2

    telemetry = InspectTelemetry()
    conn = UnitreeWebRTCConnection(
        connection_method=os.getenv("UNITREE_WEBRTC_METHOD", "remote"),
        username=username,
        password=password,
        serial_number=serial,
        region=os.getenv("UNITREE_REGION", "cn"),
    )
    subscriptions: list[Any] = []
    trials: list[StopTrial] = []
    baseline: dict[str, float] = {}
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    output = OUT_DIR / f"rotation_stop_{time.strftime('%Y%m%d-%H%M%S')}.json"

    def write_result(status: str, error: str | None = None) -> None:
        output.write_text(
            json.dumps(
                {
                    "status": status,
                    "error": error,
                    "serial": serial,
                    "connection_method": os.getenv("UNITREE_WEBRTC_METHOD", "remote"),
                    "battery_soc": telemetry.battery_soc,
                    "lowstate_data_keys": telemetry.lowstate_data_keys,
                    "lowstate_imu_keys": telemetry.lowstate_imu_keys,
                    "stationary_baseline": baseline,
                    "zero_device_ack_available": False,
                    "trials": [asdict(trial) for trial in trials],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        print(f"result={output}")

    try:
        subscriptions.extend(
            [
                conn.lowstate_stream().subscribe(telemetry.on_lowstate),
                conn.odom_stream().subscribe(telemetry.on_odom),
            ]
        )
        print(f"standup={conn.standup()}")
        time.sleep(5.0)
        print(f"balance_stand={conn.balance_stand()}")
        time.sleep(2.0)
        response = conn.publish_request(
            RTC_TOPIC["SPORT_MOD"],
            {"api_id": SPORT_CMD["SwitchJoystick"], "parameter": {"data": True}},
        )
        print(f"switch_joystick={response}")
        time.sleep(1.0)
        telemetry.require_fresh()
        if telemetry.battery_soc is not None and telemetry.battery_soc < 15:
            raise RuntimeError(f"battery too low: {telemetry.battery_soc}%")
        print(f"battery={telemetry.battery_soc}%")
        print(f"lowstate_data_keys={telemetry.lowstate_data_keys}")
        print(f"lowstate_imu_keys={telemetry.lowstate_imu_keys}")

        threshold, baseline = _stationary_threshold(
            conn,
            telemetry,
            args.stationary_baseline,
        )
        print(f"stationary={baseline}")

        for magnitude in magnitudes:
            for repeat in range(1, args.repeats + 1):
                for direction in (1, -1):
                    print(
                        f"trial magnitude={magnitude:.2f} direction={direction:+d} "
                        f"repeat={repeat}/{args.repeats}"
                    )
                    trial = _run_trial(
                        conn,
                        telemetry,
                        magnitude=magnitude,
                        direction=direction,
                        repeat=repeat,
                        hold_s=args.hold,
                        threshold_deg_s=threshold,
                        translation_limit_m=args.translation_limit_m,
                        post_zero_limit_deg=args.post_zero_limit_deg,
                    )
                    trials.append(trial)
                    print(
                        f"  nonzero={trial.nonzero_delta_deg:+.2f}° "
                        f"stop_start={trial.stop_started_ms}ms "
                        f"stop_confirm={trial.stop_confirmed_ms}ms "
                        f"post_zero={trial.post_zero_delta_deg:+.2f}° "
                        f"translation={trial.translation_m:.3f}m"
                    )

        write_result("completed")
        return 0
    except Exception as error:
        write_result("aborted", str(error))
        raise
    finally:
        try:
            _send_zero_burst(conn, 1.0)
        except Exception:
            pass
        try:
            conn.liedown()
        except Exception:
            pass
        for subscription in subscriptions:
            try:
                subscription.dispose()
            except Exception:
                pass
        try:
            conn.stop()
        except Exception:
            pass


if __name__ == "__main__":
    raise SystemExit(main())
