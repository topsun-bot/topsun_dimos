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

"""Safely characterize low-amplitude Go2 yaw joystick commands.

The script sends raw WIRELESS_CONTROLLER packets with lx=ly=0, measures yaw
from the body IMU, and uses lidar odometry only as a translation safety guard.
It intentionally does not import the older rotate-calibration script.
"""

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

OUT_DIR = Path(__file__).resolve().parent.parent / "cache" / "rotate_calibration"
ZERO_JOYSTICK = {"lx": 0.0, "ly": 0.0, "rx": 0.0, "ry": 0.0}


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
class Trial:
    rx: float
    hold_s: float
    delta_yaw_deg: float
    command_rate_deg_s: float
    median_moving_rate_deg_s: float
    translation_m: float
    imu_age_before_ms: float
    imu_samples: int


class Telemetry:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.imu: deque[ImuSample] = deque(maxlen=4000)
        self.odom: deque[OdomSample] = deque(maxlen=2000)
        self.battery_soc: int | None = None

    def on_lowstate(self, msg: Any) -> None:
        try:
            data = msg["data"]
            roll, pitch, yaw = (float(v) for v in data["imu_state"]["rpy"])
            battery = data.get("bms_state", {}).get("soc")
            sample = ImuSample(time.monotonic(), yaw, roll, pitch)
            with self.lock:
                self.imu.append(sample)
                if battery is not None:
                    self.battery_soc = int(battery)
        except (KeyError, TypeError, ValueError):
            return

    def on_odom(self, pose: Any) -> None:
        try:
            sample = OdomSample(time.monotonic(), float(pose.position.x), float(pose.position.y))
            with self.lock:
                self.odom.append(sample)
        except (AttributeError, TypeError, ValueError):
            return

    def snapshots(self) -> tuple[list[ImuSample], list[OdomSample]]:
        with self.lock:
            return list(self.imu), list(self.odom)

    def require_fresh(self, maximum_age_s: float = 0.20) -> float:
        imu, odom = self.snapshots()
        if not imu or not odom:
            raise RuntimeError("IMU or odometry telemetry is missing")
        imu_age = time.monotonic() - imu[-1].host_monotonic
        odom_age = time.monotonic() - odom[-1].host_monotonic
        if imu_age > maximum_age_s or odom_age > 0.50:
            raise RuntimeError(f"stale telemetry: imu={imu_age:.3f}s odom={odom_age:.3f}s")
        if abs(imu[-1].roll_rad) > 0.35 or abs(imu[-1].pitch_rad) > 0.35:
            raise RuntimeError("body tilt exceeds 20 degrees")
        return imu_age


def _parse_floats(value: str) -> list[float]:
    return [float(item.strip()) for item in value.split(",") if item.strip()]


def _send_raw(conn: UnitreeWebRTCConnection, rx: float) -> None:
    if not -1.0 <= rx <= 1.0:
        raise ValueError(f"rx out of range: {rx}")

    async def send() -> None:
        conn.conn.datachannel.pub_sub.publish_without_callback(
            RTC_TOPIC["WIRELESS_CONTROLLER"],
            data={"lx": 0.0, "ly": 0.0, "rx": float(rx), "ry": 0.0},
        )

    asyncio.run_coroutine_threadsafe(send(), conn.loop).result(timeout=1.0)


def _send_zero_burst(conn: UnitreeWebRTCConnection, duration_s: float = 0.6) -> None:
    deadline = time.monotonic() + duration_s
    while time.monotonic() < deadline:
        _send_raw(conn, 0.0)
        time.sleep(0.05)


def _unwrapped_delta(samples: list[ImuSample]) -> float:
    return sum(
        angle_diff(current.yaw_rad, previous.yaw_rad) for previous, current in pairwise(samples)
    )


def _median_moving_rate(samples: list[ImuSample]) -> float:
    rates: list[float] = []
    for previous, current in pairwise(samples):
        dt = current.host_monotonic - previous.host_monotonic
        if dt <= 0.005 or dt > 0.25:
            continue
        rate = math.degrees(angle_diff(current.yaw_rad, previous.yaw_rad)) / dt
        if abs(rate) >= 1.0:
            rates.append(rate)
    return statistics.median(rates) if rates else 0.0


def _translation(samples: list[OdomSample]) -> float:
    if len(samples) < 2:
        return 0.0
    origin = samples[0]
    return max(math.hypot(sample.x_m - origin.x_m, sample.y_m - origin.y_m) for sample in samples)


def run_trial(
    conn: UnitreeWebRTCConnection,
    telemetry: Telemetry,
    *,
    rx: float,
    hold_s: float,
    translation_limit_m: float,
) -> Trial:
    _send_zero_burst(conn, 0.5)
    time.sleep(0.5)
    imu_age = telemetry.require_fresh()
    start = time.monotonic()
    deadline = start + hold_s

    while time.monotonic() < deadline:
        _send_raw(conn, rx)
        time.sleep(0.05)
        _, odom = telemetry.snapshots()
        active_odom = [sample for sample in odom if sample.host_monotonic >= start]
        if _translation(active_odom) > translation_limit_m:
            _send_zero_burst(conn)
            raise RuntimeError("translation safety limit exceeded during pulse")

    _send_zero_burst(conn)
    time.sleep(0.7)
    end = time.monotonic()
    imu, odom = telemetry.snapshots()
    active_imu = [sample for sample in imu if start - 0.10 <= sample.host_monotonic <= end]
    active_odom = [sample for sample in odom if start - 0.10 <= sample.host_monotonic <= end]
    if len(active_imu) < 5:
        raise RuntimeError("not enough IMU samples during trial")
    translation = _translation(active_odom)
    if translation > translation_limit_m:
        raise RuntimeError(f"translation safety limit exceeded after pulse: {translation:.3f}m")

    delta_deg = math.degrees(_unwrapped_delta(active_imu))
    return Trial(
        rx=rx,
        hold_s=hold_s,
        delta_yaw_deg=delta_deg,
        command_rate_deg_s=delta_deg / hold_s,
        median_moving_rate_deg_s=_median_moving_rate(active_imu),
        translation_m=translation,
        imu_age_before_ms=imu_age * 1000.0,
        imu_samples=len(active_imu),
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--rx", default="0.03,0.05,0.08,0.10,0.15,0.20")
    parser.add_argument("--hold", default="1.0")
    parser.add_argument("--translation-limit-m", type=float, default=0.05)
    args = parser.parse_args()
    magnitudes = _parse_floats(args.rx)
    holds = _parse_floats(args.hold)
    if not magnitudes or any(value <= 0.0 or value > 0.50 for value in magnitudes):
        print("rx magnitudes must be in (0, 0.50]", file=sys.stderr)
        return 2
    if not holds or any(value < 0.10 or value > 2.0 for value in holds):
        print("hold values must be in [0.10, 2.0] seconds", file=sys.stderr)
        return 2

    username = os.getenv("UNITREE_USERNAME")
    password = os.getenv("UNITREE_PASSWORD")
    serial = os.getenv("UNITREE_SERIAL")
    if not username or not password or not serial:
        print("missing UNITREE_USERNAME / PASSWORD / SERIAL", file=sys.stderr)
        return 2

    telemetry = Telemetry()
    conn = UnitreeWebRTCConnection(
        connection_method=os.getenv("UNITREE_WEBRTC_METHOD", "remote"),
        username=username,
        password=password,
        serial_number=serial,
        region=os.getenv("UNITREE_REGION", "cn"),
    )
    subscriptions: list[Any] = []
    trials: list[Trial] = []
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

        for hold_s in holds:
            for magnitude in magnitudes:
                for rx in (magnitude, -magnitude):
                    print(f"trial rx={rx:+.2f} hold={hold_s:.2f}s")
                    trial = run_trial(
                        conn,
                        telemetry,
                        rx=rx,
                        hold_s=hold_s,
                        translation_limit_m=args.translation_limit_m,
                    )
                    trials.append(trial)
                    print(
                        f"  yaw={trial.delta_yaw_deg:+.2f}° "
                        f"command_rate={trial.command_rate_deg_s:+.2f}°/s "
                        f"moving_rate={trial.median_moving_rate_deg_s:+.2f}°/s "
                        f"translation={trial.translation_m:.3f}m "
                        f"imu_age={trial.imu_age_before_ms:.1f}ms"
                    )

        OUT_DIR.mkdir(parents=True, exist_ok=True)
        output = OUT_DIR / f"rotate_safe_{time.strftime('%Y%m%d-%H%M%S')}.json"
        output.write_text(
            json.dumps(
                {
                    "serial": serial,
                    "hold_s": holds,
                    "translation_limit_m": args.translation_limit_m,
                    "zero_joystick": ZERO_JOYSTICK,
                    "trials": [asdict(trial) for trial in trials],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        print(f"result={output}")
        return 0
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
