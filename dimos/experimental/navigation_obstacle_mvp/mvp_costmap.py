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

"""MVP sill lethal zones on navigation costmaps (sim / height_cost)."""

from __future__ import annotations

import numpy as np

from dimos.experimental.navigation_obstacle_mvp.forward_obstacle_height import (
    MVP_SILLS,
    MvpSill,
)
from dimos.experimental.navigation_obstacle_mvp.obstacle_decision import decide_obstacle_action
from dimos.msgs.geometry_msgs.Vector3 import Vector3
from dimos.msgs.nav_msgs.OccupancyGrid import CostValues, OccupancyGrid

# Must match `dimos/simulation/mujoco/model.py` sill half-width / thickness.
_MVP_SILL_HALF_WIDTH_M = 0.75
_MVP_SILL_HALF_THICKNESS_M = 0.025
_MVP_SILL_LETHAL_MARGIN_M = 0.05
_MVP_DETOUR_LATERAL_OFFSET_M = 0.8
_MVP_DETOUR_ALONG_MARGIN_M = 0.5
_MVP_LETHAL_COST = CostValues.OCCUPIED


def apply_mvp_sill_lethal_zones(
    costmap: OccupancyGrid,
    *,
    obstacle_crossing_mvp: bool,
    simulation: bool | str,
) -> OccupancyGrid:
    """Mark non-cross MVP sills as fully blocked on the costmap."""
    if not obstacle_crossing_mvp or not simulation:
        return costmap

    inflated = costmap.copy()
    grid = inflated.grid
    res = inflated.resolution

    for sill in MVP_SILLS:
        if decide_obstacle_action(obstacle_height_cm=sill.height_cm) == "cross":
            continue

        min_x = sill.x - _MVP_SILL_HALF_WIDTH_M - _MVP_SILL_LETHAL_MARGIN_M
        max_x = sill.x + _MVP_SILL_HALF_WIDTH_M + _MVP_SILL_LETHAL_MARGIN_M
        min_y = sill.y - _MVP_SILL_HALF_THICKNESS_M - _MVP_SILL_LETHAL_MARGIN_M
        max_y = sill.y + _MVP_SILL_HALF_THICKNESS_M + _MVP_SILL_LETHAL_MARGIN_M

        gx0 = int(np.floor((min_x - inflated.origin.position.x) / res))
        gx1 = int(np.ceil((max_x - inflated.origin.position.x) / res))
        gy0 = int(np.floor((min_y - inflated.origin.position.y) / res))
        gy1 = int(np.ceil((max_y - inflated.origin.position.y) / res))

        gx0 = max(0, gx0)
        gy0 = max(0, gy0)
        gx1 = min(inflated.width, gx1)
        gy1 = min(inflated.height, gy1)

        if gx0 >= gx1 or gy0 >= gy1:
            continue

        grid[gy0:gy1, gx0:gx1] = _MVP_LETHAL_COST

    inflated.grid = grid
    return inflated


def find_blocking_sill(robot_pos: Vector3, goal: Vector3) -> MvpSill | None:
    """Return the first non-cross sill between robot and goal on the MVP corridor."""
    for sill in MVP_SILLS:
        if decide_obstacle_action(obstacle_height_cm=sill.height_cm) == "cross":
            continue

        y_min = sill.y - _MVP_SILL_HALF_THICKNESS_M
        y_max = sill.y + _MVP_SILL_HALF_THICKNESS_M
        crosses_y_band = robot_pos.y < y_max and goal.y > y_min
        if not crosses_y_band:
            continue

        corridor_half = _MVP_SILL_HALF_WIDTH_M + _MVP_DETOUR_LATERAL_OFFSET_M
        if abs(robot_pos.x - sill.x) > corridor_half and abs(goal.x - sill.x) > corridor_half:
            continue

        return sill

    return None


def mvp_detour_waypoints(
    sill: MvpSill,
    robot_pos: Vector3,
    *,
    lateral_offset_m: float = _MVP_DETOUR_LATERAL_OFFSET_M,
) -> list[Vector3]:
    """Lateral offset waypoints to pass around a sill endpoint (±offset from center x)."""
    detour_x = sill.x - lateral_offset_m if robot_pos.x <= sill.x else sill.x + lateral_offset_m
    before_y = sill.y - _MVP_DETOUR_ALONG_MARGIN_M
    after_y = sill.y + _MVP_DETOUR_ALONG_MARGIN_M
    return [
        Vector3(detour_x, before_y, 0.0),
        Vector3(detour_x, after_y, 0.0),
    ]


def path_crosses_sill_center(path_poses: list[Vector3], sill: MvpSill) -> bool:
    """True when any path pose lies on the sill center band (would drive through the sill)."""
    y_min = sill.y - _MVP_SILL_HALF_THICKNESS_M - 0.1
    y_max = sill.y + _MVP_SILL_HALF_THICKNESS_M + 0.1
    x_min = sill.x - _MVP_SILL_HALF_WIDTH_M + 0.15
    x_max = sill.x + _MVP_SILL_HALF_WIDTH_M - 0.15

    for pos in path_poses:
        if y_min <= pos.y <= y_max and x_min <= pos.x <= x_max:
            return True
    return False
