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

from __future__ import annotations

import numpy as np

from dimos.experimental.navigation_obstacle_mvp.forward_obstacle_height import MVP_SILLS
from dimos.experimental.navigation_obstacle_mvp.mvp_costmap import (
    apply_mvp_sill_lethal_zones,
    find_blocking_sill,
    mvp_detour_waypoints,
    path_crosses_sill_center,
)
from dimos.msgs.geometry_msgs.Pose import Pose
from dimos.msgs.geometry_msgs.Vector3 import Vector3
from dimos.msgs.nav_msgs.OccupancyGrid import CostValues, OccupancyGrid


def _empty_costmap(*, resolution: float = 0.08) -> OccupancyGrid:
    return OccupancyGrid(
        grid=np.zeros((200, 200), dtype=np.int8),
        resolution=resolution,
        origin=Pose(position=Vector3(-5.0, -5.0, 0.0)),
        frame_id="world",
    )


def test_apply_mvp_sill_lethal_marks_medium_and_high_full_width() -> None:
    costmap = _empty_costmap()
    inflated = apply_mvp_sill_lethal_zones(
        costmap,
        obstacle_crossing_mvp=True,
        simulation="mujoco",
    )

    medium = MVP_SILLS[1]
    for dx in (-0.7, 0.0, 0.7):
        value = inflated.cell_value(Vector3(medium.x + dx, medium.y, 0.0))
        assert value == CostValues.OCCUPIED

    low = MVP_SILLS[0]
    assert inflated.cell_value(Vector3(low.x, low.y, 0.0)) == 0


def test_apply_mvp_sill_lethal_noop_when_mvp_disabled() -> None:
    costmap = _empty_costmap()
    result = apply_mvp_sill_lethal_zones(
        costmap,
        obstacle_crossing_mvp=False,
        simulation="mujoco",
    )
    assert result is costmap


def test_find_blocking_sill_between_robot_and_goal() -> None:
    robot = Vector3(-1.12, 2.5, 0.0)
    goal = Vector3(-1.12, 4.0, 0.0)
    blocking = find_blocking_sill(robot, goal)
    assert blocking is not None
    assert blocking.name == "mvp_threshold_medium"


def test_mvp_detour_waypoints_offset_lateral_from_sill() -> None:
    medium = MVP_SILLS[1]
    robot = Vector3(-1.12, 2.8, 0.0)
    waypoints = mvp_detour_waypoints(medium, robot, lateral_offset_m=0.8)
    assert len(waypoints) == 2
    assert abs(waypoints[0].x - (-1.92)) < 0.01
    assert waypoints[0].y < medium.y
    assert waypoints[1].y > medium.y


def test_path_crosses_sill_center_detects_centerline() -> None:
    medium = MVP_SILLS[1]
    centerline = [Vector3(-1.12, medium.y, 0.0)]
    detour = [Vector3(-1.92, medium.y, 0.0)]
    assert path_crosses_sill_center(centerline, medium)
    assert not path_crosses_sill_center(detour, medium)


def test_apply_mvp_sill_lethal_respects_simulation_flag() -> None:
    costmap = _empty_costmap()
    result = apply_mvp_sill_lethal_zones(
        costmap,
        obstacle_crossing_mvp=True,
        simulation=False,
    )
    assert result is costmap
    assert result.cell_value(Vector3(-1.12, 3.15, 0.0)) == 0
