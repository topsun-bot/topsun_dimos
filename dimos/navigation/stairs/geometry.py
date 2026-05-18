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

from dimos.msgs.nav_msgs.Path import Path
from dimos.navigation.stairs.contracts import StairCorridor


def _point_to_segment_distance(
    px: float,
    py: float,
    ax: float,
    ay: float,
    bx: float,
    by: float,
) -> float:
    abx = bx - ax
    aby = by - ay
    apx = px - ax
    apy = py - ay
    denom = abx * abx + aby * aby
    if denom < 1e-12:
        return math.hypot(apx, apy)
    t = max(0.0, min(1.0, (apx * abx + apy * aby) / denom))
    cx = ax + t * abx
    cy = ay + t * aby
    return math.hypot(px - cx, py - cy)


def max_lateral_offset(path: Path, centerline: list[tuple[float, float]]) -> float:
    if not path.poses or len(centerline) < 2:
        return 0.0
    max_off = 0.0
    for pose in path.poses:
        px, py = pose.position.x, pose.position.y
        min_seg_dist = float("inf")
        for i in range(len(centerline) - 1):
            ax, ay = centerline[i]
            bx, by = centerline[i + 1]
            min_seg_dist = min(
                min_seg_dist, _point_to_segment_distance(px, py, ax, ay, bx, by)
            )
        max_off = max(max_off, min_seg_dist)
    return max_off


def path_crosses_riser(path: Path, corridor: StairCorridor) -> bool:
    """Return True if any path segment crosses a riser line (normal to stair axis)."""
    if not path.poses or len(path.poses) < 2 or not corridor.riser_lines:
        return False

    for i in range(len(path.poses) - 1):
        p0 = path.poses[i].position
        p1 = path.poses[i + 1].position
        for (a, b) in corridor.riser_lines:
            if _segments_intersect(p0.x, p0.y, p1.x, p1.y, a[0], a[1], b[0], b[1]):
                return True
    return False


def _segments_intersect(
    x1: float,
    y1: float,
    x2: float,
    y2: float,
    x3: float,
    y3: float,
    x4: float,
    y4: float,
) -> bool:
    def orient(ax: float, ay: float, bx: float, by: float, cx: float, cy: float) -> float:
        return (bx - ax) * (cy - ay) - (by - ay) * (cx - ax)

    o1 = orient(x1, y1, x2, y2, x3, y3)
    o2 = orient(x1, y1, x2, y2, x4, y4)
    o3 = orient(x3, y3, x4, y4, x1, y1)
    o4 = orient(x3, y3, x4, y4, x2, y2)

    if o1 * o2 < 0 and o3 * o4 < 0:
        return True
    return False
