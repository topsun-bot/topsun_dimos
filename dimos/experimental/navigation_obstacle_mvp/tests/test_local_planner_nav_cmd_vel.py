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
from unittest.mock import patch

from dimos.core.global_config import GlobalConfig
from dimos.msgs.geometry_msgs.PoseStamped import PoseStamped
from dimos.msgs.geometry_msgs.Twist import Twist
from dimos.msgs.geometry_msgs.Vector3 import Vector3
from dimos.navigation.replanning_a_star.local_planner import LocalPlanner
from dimos.navigation.replanning_a_star.navigation_map import NavigationMap


def _make_local_planner(config: GlobalConfig) -> LocalPlanner:
    return LocalPlanner(config, cast("NavigationMap", cast("Any", object())), goal_tolerance=0.2)


def test_mvp_motion_limits_clear_when_leaving_sill_zone() -> None:
    planner = _make_local_planner(GlobalConfig(obstacle_crossing_mvp=True))
    planner._mvp_cross_creep = True

    far_pose = PoseStamped(position=[0.0, 0.0, 0.0], orientation=[0.0, 0.0, 0.0, 1.0])
    planner._update_mvp_cross_motion_limits(far_pose)

    assert planner._mvp_cross_creep is False


def test_log_nav_cmd_vel_skips_zeros() -> None:
    planner = _make_local_planner(GlobalConfig())
    with patch.object(planner, "_nav_cmd_vel_log_last", 0.0):
        with patch("dimos.navigation.replanning_a_star.local_planner.logger") as mock_logger:
            planner._log_nav_cmd_vel(Twist.zero())
            mock_logger.info.assert_not_called()


def test_log_nav_cmd_vel_emits_for_nonzero_twist() -> None:
    planner = _make_local_planner(GlobalConfig())
    twist = Twist(linear=Vector3(0.0, 0.0, 0.0), angular=Vector3(0.0, 0.0, 0.8))
    with patch.object(planner, "_nav_cmd_vel_log_last", 0.0):
        with patch("dimos.navigation.replanning_a_star.local_planner.logger") as mock_logger:
            planner._log_nav_cmd_vel(twist)
            mock_logger.info.assert_called_once()
            assert mock_logger.info.call_args.args[0] == "Publishing nav_cmd_vel"
