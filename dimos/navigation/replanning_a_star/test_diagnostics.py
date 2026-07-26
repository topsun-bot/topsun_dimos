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

from typing import Any
from unittest.mock import MagicMock

import numpy as np

from dimos.core.global_config import GlobalConfig
from dimos.msgs.geometry_msgs.PoseStamped import PoseStamped
from dimos.msgs.geometry_msgs.Quaternion import Quaternion
from dimos.msgs.geometry_msgs.Twist import Twist
from dimos.msgs.geometry_msgs.Vector3 import Vector3
from dimos.msgs.nav_msgs.OccupancyGrid import OccupancyGrid
from dimos.msgs.nav_msgs.Path import Path
from dimos.navigation.replanning_a_star.local_planner import LocalPlanner
from dimos.navigation.replanning_a_star.path_clearance import PathClearance


def _pose(x: float, y: float) -> PoseStamped:
    return PoseStamped(
        position=Vector3(x, y, 0.0),
        orientation=Quaternion(0.0, 0.0, 0.0, 1.0),
    )


class _ExplodingTrace:
    enabled = True
    effective_level = "full"

    def __init__(self, order: list[str]) -> None:
        self._order = order

    def accepts(self, minimum: str) -> bool:
        return True

    def record(self, event: str, fields: Any, *, estimated_bytes: int) -> bool:
        self._order.append("trace")
        raise OSError("injected trace failure")

    def disable(self, exc: Exception) -> None:
        self._order.append(f"disable:{type(exc).__name__}")


def test_control_is_published_before_trace_failure() -> None:
    order: list[str] = []
    navigation_map = MagicMock()
    trace = _ExplodingTrace(order)
    planner = LocalPlanner(
        GlobalConfig(),
        navigation_map,
        0.2,
        trace_sink=trace,  # type: ignore[arg-type]
    )
    path = Path(poses=[_pose(1.0, 0.0), _pose(2.0, 0.0)])
    current = _pose(0.0, 0.0)
    clearance = MagicMock()
    clearance.is_obstacle_ahead.return_value = False
    distancer = MagicMock()
    distancer.distance_to_goal.return_value = 2.0
    distancer.find_closest_point_index.return_value = 0
    distancer.find_lookahead_point.return_value = np.array([0.5, 0.0])
    command = Twist(
        linear=Vector3(0.4, 0.0, 0.0),
        angular=Vector3(0.0, 0.0, 0.2),
    )
    planner._controller = MagicMock()
    planner._controller.advance.return_value = command
    planner._path = path
    planner._path_clearance = clearance
    planner._path_distancer = distancer
    planner._current_odom = current
    stop_event = MagicMock()
    stop_event.is_set.side_effect = [False, True, True]
    planner._stop_planning_event = stop_event
    planner.cmd_vel.subscribe(lambda _: order.append("publish"))

    planner._loop()

    assert order[:3] == ["publish", "trace", "disable:OSError"]


def test_path_clearance_diagnostic_preserves_boolean_result(monkeypatch) -> None:
    path = Path(poses=[_pose(0.0, 0.0), _pose(1.0, 0.0)])
    clearance = PathClearance(GlobalConfig(robot_width=0.3), path)
    grid_values = np.zeros((3, 4), dtype=np.int8)
    grid_values[0, 1] = 100
    grid_values[2, 3] = 100
    grid = OccupancyGrid(grid=grid_values, resolution=0.1, ts=55.0)
    mask = np.zeros_like(grid_values, dtype=np.bool_)
    mask[0, 1] = True
    mask[2, 3] = True
    monkeypatch.setattr(
        "dimos.navigation.replanning_a_star.path_clearance.make_path_mask",
        lambda **_: mask,
    )
    clearance.update_costmap(grid)

    before = grid.grid.copy()
    obstacle = clearance.is_obstacle_ahead()
    diagnostic = clearance.diagnostic(max_hit_cells=1)

    assert obstacle is True
    assert diagnostic.obstacle_ahead is True
    assert diagnostic.occupied_hit_count == 2
    assert diagnostic.free_cell_count == 0
    assert diagnostic.unknown_cell_count == 0
    assert diagnostic.path_lookup_distance_m == 3.0
    assert diagnostic.path_start_index == 0
    assert diagnostic.path_end_index == 1
    assert diagnostic.effective_lookahead_m == 1.0
    assert diagnostic.mask_reused_for_decision is False
    assert diagnostic.first_occupied_cells == ((0, 1),)
    assert diagnostic.hits_truncated is True
    assert np.array_equal(grid.grid, before)

    assert clearance.is_obstacle_ahead() is True
    assert clearance.diagnostic().mask_reused_for_decision is True
