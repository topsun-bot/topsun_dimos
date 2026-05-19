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

from dimos.msgs.geometry_msgs.Vector3 import Vector3
from dimos.msgs.nav_msgs.OccupancyGrid import CostValues, OccupancyGrid
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


def project_point_to_centerline(
    x: float,
    y: float,
    centerline: list[tuple[float, float]],
) -> tuple[float, float, float]:
    """Return (proj_x, proj_y, distance_along_polyline) for the closest point on the centerline."""
    if len(centerline) < 2:
        if centerline:
            return centerline[0][0], centerline[0][1], 0.0
        return x, y, 0.0

    best_dist = float("inf")
    best_xy = centerline[0]
    best_along = 0.0
    accumulated = 0.0

    for i in range(len(centerline) - 1):
        ax, ay = centerline[i]
        bx, by = centerline[i + 1]
        abx = bx - ax
        aby = by - ay
        seg_len = math.hypot(abx, aby)
        if seg_len < 1e-9:
            continue
        t = max(0.0, min(1.0, ((x - ax) * abx + (y - ay) * aby) / (seg_len * seg_len)))
        px = ax + t * abx
        py = ay + t * aby
        dist = math.hypot(x - px, y - py)
        along = accumulated + t * seg_len
        if dist < best_dist:
            best_dist = dist
            best_xy = (px, py)
            best_along = along
        accumulated += seg_len

    return best_xy[0], best_xy[1], best_along


def snap_goal_to_corridor(goal: Vector3, corridor: StairCorridor) -> Vector3:
    """Project a navigation goal onto the stair centerline (keeps z)."""
    px, py, _ = project_point_to_centerline(goal.x, goal.y, corridor.centerline)
    return Vector3(px, py, goal.z)


def _point_in_polygon(x: float, y: float, polygon: list[tuple[float, float]]) -> bool:
    inside = False
    n = len(polygon)
    if n < 3:
        return False
    j = n - 1
    for i in range(n):
        xi, yi = polygon[i]
        xj, yj = polygon[j]
        if ((yi > y) != (yj > y)) and (x < (xj - xi) * (y - yi) / (yj - yi + 1e-12) + xi):
            inside = not inside
        j = i
    return inside


def point_in_stair_corridor(
    point: Vector3,
    corridor: StairCorridor,
    *,
    margin_m: float = 0.35,
) -> bool:
    """True when a world point lies inside the navigable stair corridor."""
    if len(corridor.polygon) >= 3:
        if _point_in_polygon(point.x, point.y, corridor.polygon):
            return True
    px, py, along = project_point_to_centerline(point.x, point.y, corridor.centerline)
    lateral = math.hypot(point.x - px, point.y - py)
    if lateral > corridor.safe_half_width + margin_m:
        return False
    total = 0.0
    for i in range(len(corridor.centerline) - 1):
        ax, ay = corridor.centerline[i]
        bx, by = corridor.centerline[i + 1]
        total += math.hypot(bx - ax, by - ay)
    return -margin_m <= along <= total + margin_m


def clear_corridor_in_costmap(
    costmap: OccupancyGrid,
    corridor: StairCorridor,
    *,
    margin_m: float = 0.2,
) -> OccupancyGrid:
    """Mark stair corridor cells as free so goals and local clearance treat stairs as traversable."""
    cleared = costmap.copy()
    if len(corridor.centerline) < 2:
        return cleared

    xs = [p[0] for p in corridor.polygon] if corridor.polygon else [p[0] for p in corridor.centerline]
    ys = [p[1] for p in corridor.polygon] if corridor.polygon else [p[1] for p in corridor.centerline]
    pad = margin_m + corridor.safe_half_width + 0.5
    min_x, max_x = min(xs) - pad, max(xs) + pad
    min_y, max_y = min(ys) - pad, max(ys) + pad

    g0 = costmap.world_to_grid(Vector3(min_x, min_y, 0.0))
    g1 = costmap.world_to_grid(Vector3(max_x, max_y, 0.0))
    ix0 = max(0, int(min(g0.x, g1.x)))
    ix1 = min(costmap.width, int(max(g0.x, g1.x)) + 1)
    iy0 = max(0, int(min(g0.y, g1.y)))
    iy1 = min(costmap.height, int(max(g0.y, g1.y)) + 1)

    for iy in range(iy0, iy1):
        for ix in range(ix0, ix1):
            world = costmap.grid_to_world(Vector3(float(ix), float(iy), 0.0))
            if point_in_stair_corridor(world, corridor, margin_m=margin_m):
                cleared.grid[iy, ix] = CostValues.FREE

    return cleared


def goal_requests_stair_corridor(
    goal: Vector3,
    corridor: StairCorridor,
    *,
    max_lateral_m: float = 3.0,
) -> bool:
    """True when the user goal is close enough to the stair corridor to prefer centerline planning."""
    if point_in_stair_corridor(goal, corridor):
        return True
    _, _, along = project_point_to_centerline(goal.x, goal.y, corridor.centerline)
    px, py, _ = project_point_to_centerline(goal.x, goal.y, corridor.centerline)
    lateral = math.hypot(goal.x - px, goal.y - py)
    if lateral > max_lateral_m:
        return False
    total = 0.0
    for i in range(len(corridor.centerline) - 1):
        ax, ay = corridor.centerline[i]
        bx, by = corridor.centerline[i + 1]
        total += math.hypot(bx - ax, by - ay)
    return -0.5 <= along <= total + 0.5


def extend_centerline_approach(
    centerline: list[tuple[float, float]],
    margin_m: float,
) -> list[tuple[float, float]]:
    """Prepend a point ``margin_m`` before the first centerline vertex (approach segment)."""
    if len(centerline) < 2 or margin_m <= 0.0:
        return centerline
    x0, y0 = centerline[0]
    x1, y1 = centerline[1]
    dx = x0 - x1
    dy = y0 - y1
    length = math.hypot(dx, dy)
    if length < 1e-9:
        return centerline
    approach = (x0 + dx / length * margin_m, y0 + dy / length * margin_m)
    if math.hypot(approach[0] - x0, approach[1] - y0) < 1e-6:
        return centerline
    return [approach, *centerline]


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


def _segment_on_centerline(
    p0x: float,
    p0y: float,
    p1x: float,
    p1y: float,
    centerline: list[tuple[float, float]],
    tol: float,
) -> bool:
    mid_x = (p0x + p1x) / 2.0
    mid_y = (p0y + p1y) / 2.0
    px, py, _ = project_point_to_centerline(mid_x, mid_y, centerline)
    return math.hypot(mid_x - px, mid_y - py) <= tol


def path_crosses_riser(path: Path, corridor: StairCorridor) -> bool:
    """Return True if a path segment leaves the corridor and crosses a riser line."""
    if not path.poses or len(path.poses) < 2 or not corridor.riser_lines:
        return False

    for i in range(len(path.poses) - 1):
        p0 = path.poses[i].position
        p1 = path.poses[i + 1].position
        if _segment_on_centerline(
            p0.x, p0.y, p1.x, p1.y, corridor.centerline, corridor.safe_half_width + 0.05
        ):
            continue
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
