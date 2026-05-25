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

from typing import Any, cast

from dimos.core.global_config import GlobalConfig
from dimos.msgs.geometry_msgs.PoseStamped import PoseStamped
from dimos.navigation.replanning_a_star.global_planner import GlobalPlanner
from dimos.navigation.replanning_a_star.local_planner import (
    _MVP_CROSS_CREEP_DISTANCE_M,
    LocalPlanner,
)
from dimos.navigation.replanning_a_star.navigation_map import NavigationMap
from dimos.navigation.replanning_a_star.position_tracker import PositionTracker


def _make_local_planner(config: GlobalConfig) -> LocalPlanner:
    return LocalPlanner(config, cast("NavigationMap", cast("Any", object())), goal_tolerance=0.2)


def test_simulation_uses_longer_stuck_window() -> None:
    gp = GlobalPlanner(GlobalConfig(simulation="mujoco", _env_file=None))
    tracker = gp._position_tracker
    assert tracker._time_window == 15.0
    assert tracker._threshold == 1.0


def test_mvp_extends_stuck_window_and_threshold() -> None:
    gp = GlobalPlanner(
        GlobalConfig(
            simulation="mujoco",
            obstacle_crossing_mvp=True,
            _env_file=None,
        )
    )
    tracker = gp._position_tracker
    assert tracker._time_window == 20.0
    assert tracker._threshold == 1.5


def test_simulation_local_planner_control_and_tolerance() -> None:
    planner = _make_local_planner(GlobalConfig(simulation="mujoco", _env_file=None))
    assert planner._control_frequency == 15.0
    assert planner._orientation_tolerance == 0.45


def test_mvp_creep_only_in_path_following() -> None:
    planner = _make_local_planner(GlobalConfig(obstacle_crossing_mvp=True, _env_file=None))
    planner._state = "initial_rotation"
    near_pose = PoseStamped(position=[-1.0, 0.0, 0.0], orientation=[0.0, 0.0, 0.0, 1.0])

    planner._update_mvp_cross_motion_limits(near_pose)

    assert planner._mvp_cross_creep is False


def test_mvp_no_creep_when_far_from_low_sill() -> None:
    planner = _make_local_planner(GlobalConfig(obstacle_crossing_mvp=True, _env_file=None))
    planner._state = "path_following"
    # North of spawn, still in low-sill capture zone but >15 cm from sill center.
    far_pose = PoseStamped(position=[-1.12, 0.5, 0.0], orientation=[0.0, 0.0, 0.0, 1.0])

    planner._update_mvp_cross_motion_limits(far_pose)

    assert planner._mvp_cross_creep is False


def test_mvp_creep_distance_is_15cm() -> None:
    assert _MVP_CROSS_CREEP_DISTANCE_M == 0.15


def test_position_tracker_stuck_threshold_respected() -> None:
    tracker = PositionTracker(time_window=20.0, threshold=1.5)
    assert not tracker.is_stuck()
