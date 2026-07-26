# Copyright 2025-2026 Dimensional Inc.
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

"""Read-only external capture for the stationary real-robot safety gate."""

from __future__ import annotations

import argparse
from collections.abc import Mapping
from dataclasses import asdict
import json
from pathlib import Path
from threading import Lock
import time
from typing import Any

from dimos.core.transport import PubSubTransport
from dimos.core.transport_factory import make_transport
from dimos.msgs.geometry_msgs.PoseStamped import PoseStamped
from dimos.msgs.geometry_msgs.Twist import Twist
from dimos.msgs.nav_msgs.OccupancyGrid import OccupancyGrid
from dimos.msgs.sensor_msgs.PointCloud2 import PointCloud2
from dimos.navigation.diagnostics.replay_capture import (
    ResourceSample,
    _resource_sample,
    _twist_fields,
)


def evaluate_stationary_capture(
    data: Mapping[str, Any],
    *,
    minimum_duration_sec: float = 600.0,
    maximum_xy_span_m: float = 0.05,
) -> dict[str, Any]:
    """Evaluate a read-only stationary capture without touching transports."""
    started = data.get("capture_started_monotonic_ns")
    ended = data.get("capture_ended_monotonic_ns")
    duration_sec = (
        max(0.0, (float(ended) - float(started)) / 1_000_000_000)
        if isinstance(started, (int, float)) and isinstance(ended, (int, float))
        else 0.0
    )
    odom = data.get("odom")
    odom_rows = odom if isinstance(odom, list) else []
    xs = [float(row["x"]) for row in odom_rows if isinstance(row, Mapping) and "x" in row]
    ys = [float(row["y"]) for row in odom_rows if isinstance(row, Mapping) and "y" in row]
    x_span = max(xs) - min(xs) if xs else float("inf")
    y_span = max(ys) - min(ys) if ys else float("inf")

    def command_magnitude(rows: Any) -> float:
        if not isinstance(rows, list):
            return 0.0
        return max(
            (
                max(
                    abs(float(row.get(component, 0.0)))
                    for component in (
                        "linear_x",
                        "linear_y",
                        "linear_z",
                        "angular_x",
                        "angular_y",
                        "angular_z",
                    )
                )
                for row in rows
                if isinstance(row, Mapping)
            ),
            default=0.0,
        )

    max_command = max(
        command_magnitude(data.get("nav_cmd_vel")),
        command_magnitude(data.get("mux_cmd_vel")),
    )
    checks = {
        "duration": duration_sec >= max(0.0, minimum_duration_sec),
        "not_interrupted": data.get("interrupted") is False,
        "odom_present": bool(odom_rows),
        "global_map_present": bool(data.get("global_map_rx_ns")),
        "costmap_present": bool(data.get("costmap_rx_ns")),
        "no_motion_command": max_command <= 1e-6,
        "odom_within_xy_envelope": (
            x_span <= max(0.0, maximum_xy_span_m) and y_span <= max(0.0, maximum_xy_span_m)
        ),
    }
    return {
        "passed": all(checks.values()),
        "checks": checks,
        "duration_sec": duration_sec,
        "odom_count": len(odom_rows),
        "global_map_count": len(data.get("global_map_rx_ns", [])),
        "costmap_count": len(data.get("costmap_rx_ns", [])),
        "x_span_m": x_span,
        "y_span_m": y_span,
        "max_command_magnitude": max_command,
        "minimum_duration_sec": minimum_duration_sec,
        "maximum_xy_span_m": maximum_xy_span_m,
    }


def capture_stationary(output: Path, *, duration_sec: float) -> None:
    """Capture health evidence without creating any publishing transport."""
    duration = max(1.0, duration_sec)
    started_ns = time.monotonic_ns()
    lock = Lock()
    data: dict[str, Any] = {
        "capture_started_monotonic_ns": started_ns,
        "requested_duration_sec": duration,
        "read_only": True,
        "odom": [],
        "nav_cmd_vel": [],
        "mux_cmd_vel": [],
        "global_map_rx_ns": [],
        "costmap_rx_ns": [],
        "resource_samples": [],
    }

    def on_odom(message: PoseStamped) -> None:
        with lock:
            data["odom"].append(
                {
                    "rx_monotonic_ns": time.monotonic_ns(),
                    "source_ts": float(message.ts),
                    "x": float(message.position.x),
                    "y": float(message.position.y),
                    "z": float(message.position.z),
                    "yaw": float(message.orientation.euler[2]),
                }
            )

    def twist_callback(key: str) -> Any:
        def callback(message: Twist) -> None:
            with lock:
                data[key].append(
                    {
                        "rx_monotonic_ns": time.monotonic_ns(),
                        **_twist_fields(message),
                    }
                )

        return callback

    def receive_timestamp(key: str) -> Any:
        def callback(_message: Any) -> None:
            with lock:
                data[key].append(time.monotonic_ns())

        return callback

    transports: list[PubSubTransport[Any]] = [
        make_transport("/odom", PoseStamped),
        make_transport("/nav_cmd_vel", Twist),
        make_transport("/cmd_vel", Twist),
        make_transport("/global_map", PointCloud2),
        make_transport("/global_costmap", OccupancyGrid),
    ]
    callbacks = (
        on_odom,
        twist_callback("nav_cmd_vel"),
        twist_callback("mux_cmd_vel"),
        receive_timestamp("global_map_rx_ns"),
        receive_timestamp("costmap_rx_ns"),
    )
    for transport, callback in zip(transports, callbacks, strict=True):
        transport.subscribe(callback)

    deadline = time.monotonic() + duration
    next_resource_sample = time.monotonic()
    previous_resource_sample: ResourceSample | None = None
    interrupted = False
    try:
        while time.monotonic() < deadline:
            now = time.monotonic()
            if now >= next_resource_sample:
                sample = _resource_sample(previous_resource_sample)
                if sample is not None:
                    data["resource_samples"].append(asdict(sample))
                    previous_resource_sample = sample
                next_resource_sample = now + 1.0
            time.sleep(0.01)
    except KeyboardInterrupt:
        interrupted = True
        raise
    finally:
        for transport in transports:
            transport.stop()
        data["capture_ended_monotonic_ns"] = time.monotonic_ns()
        data["interrupted"] = interrupted
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(
            json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
            encoding="utf-8",
        )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("output", type=Path)
    parser.add_argument("--duration-sec", type=float, default=600.0)
    parser.add_argument(
        "--check",
        action="store_true",
        help="print the stationary-gate result and return non-zero on failure",
    )
    args = parser.parse_args()
    interrupted = False
    try:
        capture_stationary(args.output, duration_sec=args.duration_sec)
    except KeyboardInterrupt:
        interrupted = True
        if not args.check:
            raise
    if args.check:
        result = evaluate_stationary_capture(json.loads(args.output.read_text(encoding="utf-8")))
        result["cli_interrupted"] = interrupted
        print(json.dumps(result, indent=2, ensure_ascii=False))
        if not result["passed"]:
            raise SystemExit(2)


if __name__ == "__main__":
    main()
