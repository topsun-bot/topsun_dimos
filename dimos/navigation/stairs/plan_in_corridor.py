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

from __future__ import annotations

import math
import time

from dimos.msgs.geometry_msgs.PoseStamped import PoseStamped
from dimos.msgs.geometry_msgs.Quaternion import Quaternion
from dimos.msgs.geometry_msgs.Vector3 import Vector3
from dimos.msgs.nav_msgs.Path import Path
from dimos.navigation.stairs.contracts import StairCorridor


def _project_on_polyline(
    x: float,
    y: float,
    centerline: list[tuple[float, float]],
) -> tuple[float, int]:
    """Return (distance_along_polyline, segment_index)."""
    if len(centerline) < 2:
        return 0.0, 0

    best_d = 0.0
    best_idx = 0
    accumulated = 0.0

    for i in range(len(centerline) - 1):
        ax, ay = centerline[i]
        bx, by = centerline[i + 1]
        seg_len = math.hypot(bx - ax, by - ay)
        if seg_len < 1e-9:
            continue
        t = max(
            0.0,
            min(1.0, ((x - ax) * (bx - ax) + (y - ay) * (by - ay)) / (seg_len * seg_len)),
        )
        px = ax + t * (bx - ax)
        py = ay + t * (by - ay)
        dist_to_pt = math.hypot(x - px, y - py)
        if i == 0 or dist_to_pt < 1e6:
            along = accumulated + t * seg_len
            if i == 0 or along >= 0:
                best_d = along
                best_idx = i
        accumulated += seg_len

    return best_d, best_idx


def _point_at_distance(
    centerline: list[tuple[float, float]],
    distance: float,
) -> tuple[float, float]:
    if len(centerline) < 2:
        return centerline[0] if centerline else (0.0, 0.0)

    remaining = distance
    for i in range(len(centerline) - 1):
        ax, ay = centerline[i]
        bx, by = centerline[i + 1]
        seg_len = math.hypot(bx - ax, by - ay)
        if seg_len < 1e-9:
            continue
        if remaining <= seg_len:
            t = remaining / seg_len
            return ax + t * (bx - ax), ay + t * (by - ay)
        remaining -= seg_len
    return centerline[-1]


def _polyline_length(centerline: list[tuple[float, float]]) -> float:
    total = 0.0
    for i in range(len(centerline) - 1):
        ax, ay = centerline[i]
        bx, by = centerline[i + 1]
        total += math.hypot(bx - ax, by - ay)
    return total


def plan_in_corridor(
    start: Vector3,
    goal: Vector3,
    corridor: StairCorridor,
    step_m: float = 0.1,
    frame_id: str = "map",
) -> Path | None:
    """Plan a path along the stair centerline between start and goal projections."""
    if len(corridor.centerline) < 2:
        return None

    total_len = _polyline_length(corridor.centerline)
    if total_len < 1e-6:
        return None

    d_start, _ = _project_on_polyline(start.x, start.y, corridor.centerline)
    d_goal, _ = _project_on_polyline(goal.x, goal.y, corridor.centerline)

    if not corridor.ascending:
        d_start, d_goal = d_goal, d_start

    d_lo = min(d_start, d_goal)
    d_hi = max(d_start, d_goal)
    d_lo = max(0.0, d_lo)
    d_hi = min(total_len, d_hi)

    if d_hi <= d_lo:
        return None

    n_steps = max(2, int(math.ceil((d_hi - d_lo) / step_m)))
    poses: list[PoseStamped] = []
    ts = time.time()
    yaw = corridor.axis_yaw

    for i in range(n_steps + 1):
        t = i / n_steps
        d = d_lo + t * (d_hi - d_lo)
        x, y = _point_at_distance(corridor.centerline, d)
        poses.append(
            PoseStamped(
                ts=ts,
                frame_id=frame_id,
                position=Vector3(x, y, 0.0),
                orientation=Quaternion.from_euler(Vector3(0.0, 0.0, yaw)),
            )
        )

    return Path(ts=ts, frame_id=frame_id, poses=poses)
