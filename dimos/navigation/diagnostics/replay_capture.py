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

"""External topic capture used by deterministic replay A/B safety gates."""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import json
from pathlib import Path as FilePath
from threading import Lock
import time
from typing import Any

import psutil

from dimos.core.coordination.process_lifecycle import DIMOS_RUN_ID_ENV
from dimos.core.run_registry import get_most_recent
from dimos.core.transport import PubSubTransport
from dimos.core.transport_factory import make_transport
from dimos.msgs.geometry_msgs.PoseStamped import PoseStamped
from dimos.msgs.geometry_msgs.Quaternion import Quaternion
from dimos.msgs.geometry_msgs.Twist import Twist
from dimos.msgs.geometry_msgs.Vector3 import Vector3
from dimos.msgs.nav_msgs.OccupancyGrid import OccupancyGrid
from dimos.msgs.nav_msgs.Path import Path
from dimos.msgs.sensor_msgs.PointCloud2 import PointCloud2


@dataclass(frozen=True, slots=True)
class ResourceSample:
    monotonic_ns: int
    process_count: int
    cpu_percent: float
    cpu_time_sec: float
    rss_bytes: int
    processes: tuple[ProcessResourceSample, ...]


@dataclass(frozen=True, slots=True)
class ProcessResourceSample:
    pid: int
    name: str
    cpu_time_sec: float
    rss_bytes: int


def capture(
    output: FilePath,
    *,
    duration_sec: float,
    goal_x: float,
    goal_y: float,
    goal_after_source_ts: float | None = None,
) -> None:
    """Capture streams and inject one fixed goal after odom and costmap arrive."""
    lock = Lock()
    started_ns = time.monotonic_ns()
    requested_duration_sec = max(1.0, duration_sec)
    startup_deadline = time.monotonic() + max(60.0, requested_duration_sec)
    capture_deadline: float | None = None
    data: dict[str, Any] = {
        "capture_started_monotonic_ns": started_ns,
        "goal": {
            "x": goal_x,
            "y": goal_y,
            "sent": False,
            "after_source_ts": goal_after_source_ts,
        },
        "odom": [],
        "paths": [],
        "nav_cmd_vel": [],
        "mux_cmd_vel": [],
        "global_map_rx_ns": [],
        "costmap_rx_ns": [],
        "resource_samples": [],
    }
    latest_odom: PoseStamped | None = None
    received_costmap = False
    goal_transport: PubSubTransport[PoseStamped] = make_transport(
        "/goal_request",
        PoseStamped,
    )

    def send_goal(message: PoseStamped) -> None:
        nonlocal capture_deadline
        goal = PoseStamped(
            position=Vector3(goal_x, goal_y, 0.0),
            orientation=Quaternion(
                message.orientation.x,
                message.orientation.y,
                message.orientation.z,
                message.orientation.w,
            ),
            frame_id=message.frame_id,
        )
        sent_ns = time.monotonic_ns()
        with lock:
            if data["goal"]["sent"]:
                return
            data["goal"]["sent"] = True
            data["goal"]["sent_monotonic_ns"] = sent_ns
            data["goal"]["sent_source_ts"] = float(message.ts)
            data["capture_duration_after_goal_sec"] = requested_duration_sec
            capture_deadline = time.monotonic() + requested_duration_sec
        goal_transport.broadcast(None, goal)

    def on_odom(message: PoseStamped) -> None:
        nonlocal latest_odom
        now_ns = time.monotonic_ns()
        latest_odom = message
        with lock:
            data["odom"].append(
                {
                    "rx_monotonic_ns": now_ns,
                    "source_ts": float(message.ts),
                    "x": float(message.position.x),
                    "y": float(message.position.y),
                    "yaw": float(message.orientation.euler[2]),
                }
            )
        if (
            goal_after_source_ts is not None
            and float(message.ts) >= goal_after_source_ts
            and received_costmap
        ):
            send_goal(message)

    def on_path(message: Path) -> None:
        with lock:
            data["paths"].append(
                {
                    "rx_monotonic_ns": time.monotonic_ns(),
                    "points": [
                        [float(pose.position.x), float(pose.position.y)] for pose in message.poses
                    ],
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

    def on_global_map(_message: PointCloud2) -> None:
        with lock:
            data["global_map_rx_ns"].append(time.monotonic_ns())

    def on_costmap(_message: OccupancyGrid) -> None:
        nonlocal received_costmap
        received_costmap = True
        with lock:
            data["costmap_rx_ns"].append(time.monotonic_ns())

    transports: list[PubSubTransport[Any]] = [
        make_transport("/odom", PoseStamped),
        make_transport("/path", Path),
        make_transport("/nav_cmd_vel", Twist),
        make_transport("/cmd_vel", Twist),
        make_transport("/global_map", PointCloud2),
        make_transport("/global_costmap", OccupancyGrid),
    ]
    callbacks = (
        on_odom,
        on_path,
        twist_callback("nav_cmd_vel"),
        twist_callback("mux_cmd_vel"),
        on_global_map,
        on_costmap,
    )
    for transport, callback in zip(transports, callbacks, strict=True):
        transport.subscribe(callback)

    next_resource_sample = time.monotonic()
    previous_resource_sample: ResourceSample | None = None
    try:
        while time.monotonic() < (
            capture_deadline if capture_deadline is not None else startup_deadline
        ):
            if (
                goal_after_source_ts is None
                and latest_odom is not None
                and received_costmap
                and not data["goal"]["sent"]
            ):
                send_goal(latest_odom)
            now = time.monotonic()
            if now >= next_resource_sample:
                sample = _resource_sample(previous_resource_sample)
                if sample is not None:
                    data["resource_samples"].append(asdict(sample))
                    previous_resource_sample = sample
                next_resource_sample = now + 1.0
            time.sleep(0.01)
    finally:
        for transport in [*transports, goal_transport]:
            transport.stop()

    data["capture_ended_monotonic_ns"] = time.monotonic_ns()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )


def _resource_sample(previous: ResourceSample | None = None) -> ResourceSample | None:
    entry = get_most_recent(alive_only=True)
    if entry is None:
        return None
    run_id = entry.run_id
    processes: list[psutil.Process] = []
    for process in psutil.process_iter(attrs=["pid"]):
        try:
            if process.environ().get(DIMOS_RUN_ID_ENV) == run_id:
                processes.append(process)
        except (
            psutil.NoSuchProcess,
            psutil.AccessDenied,
            psutil.ZombieProcess,
            OSError,
        ):
            continue

    cpu_time_sec = 0.0
    rss = 0
    live_count = 0
    process_samples: list[ProcessResourceSample] = []
    for child in sorted(processes, key=lambda item: item.pid):
        try:
            cpu_times = child.cpu_times()
            process_cpu_time_sec = cpu_times.user + cpu_times.system
            process_rss = child.memory_info().rss
            cpu_time_sec += process_cpu_time_sec
            rss += process_rss
            live_count += 1
            process_samples.append(
                ProcessResourceSample(
                    pid=child.pid,
                    name=child.name(),
                    cpu_time_sec=process_cpu_time_sec,
                    rss_bytes=process_rss,
                )
            )
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    if live_count == 0:
        return None
    monotonic_ns = time.monotonic_ns()
    cpu_percent = 0.0
    if previous is not None:
        elapsed_sec = (monotonic_ns - previous.monotonic_ns) / 1_000_000_000
        if elapsed_sec > 0:
            cpu_percent = max(
                0.0,
                (cpu_time_sec - previous.cpu_time_sec) / elapsed_sec * 100.0,
            )
    return ResourceSample(
        monotonic_ns=monotonic_ns,
        process_count=live_count,
        cpu_percent=cpu_percent,
        cpu_time_sec=cpu_time_sec,
        rss_bytes=rss,
        processes=tuple(process_samples),
    )


def _twist_fields(message: Twist) -> dict[str, float]:
    return {
        "linear_x": float(message.linear.x),
        "linear_y": float(message.linear.y),
        "linear_z": float(message.linear.z),
        "angular_x": float(message.angular.x),
        "angular_y": float(message.angular.y),
        "angular_z": float(message.angular.z),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("output", type=FilePath)
    parser.add_argument("--duration-sec", type=float, default=75.0)
    parser.add_argument("--goal-x", type=float, default=-1.5)
    parser.add_argument("--goal-y", type=float, default=4.5)
    parser.add_argument("--goal-after-source-ts", type=float)
    args = parser.parse_args()
    capture(
        args.output,
        duration_sec=args.duration_sec,
        goal_x=args.goal_x,
        goal_y=args.goal_y,
        goal_after_source_ts=args.goal_after_source_ts,
    )


if __name__ == "__main__":
    main()
