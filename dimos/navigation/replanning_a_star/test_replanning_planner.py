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

"""Unit tests for the replanning A* navigation planner components:
PositionTracker, ReplanLimiter, and GlobalPlanner."""

from unittest.mock import MagicMock, patch

from dimos_lcm.std_msgs import Bool
import pytest

from dimos.msgs.geometry_msgs.PoseStamped import PoseStamped
from dimos.msgs.geometry_msgs.Quaternion import Quaternion
from dimos.msgs.geometry_msgs.Vector3 import Vector3
from dimos.navigation.replanning_a_star.module import ReplanningAStarPlanner
from dimos.navigation.replanning_a_star.position_tracker import PositionTracker
from dimos.navigation.replanning_a_star.replan_limiter import ReplanLimiter


def _make_pose(x: float, y: float, z: float = 0.0) -> PoseStamped:
    return PoseStamped(
        position=Vector3(x, y, z),
        orientation=Quaternion(0.0, 0.0, 0.0, 1.0),
    )


# ---------------------------------------------------------------------------
# PositionTracker
# ---------------------------------------------------------------------------


class TestPositionTracker:
    def test_empty_tracker_not_stuck(self) -> None:
        tracker = PositionTracker(time_window=5.0, threshold=0.4)
        assert tracker.is_stuck() is False

    def test_single_position_is_stuck(self) -> None:
        """Single position point: all distances are 0, below threshold, should be stuck."""
        tracker = PositionTracker(time_window=5.0, threshold=0.4)
        tracker.add_position(_make_pose(1.0, 2.0))
        assert tracker.is_stuck() is True

    def test_stationary_robot_is_stuck(self) -> None:
        """Robot stationary at the same position -- should be stuck."""
        tracker = PositionTracker(time_window=5.0, threshold=0.4)
        for _ in range(10):
            tracker.add_position(_make_pose(3.0, 4.0))
        assert tracker.is_stuck() is True

    def test_moving_robot_not_stuck(self) -> None:
        """Robot moving over a large range -- should NOT be stuck."""
        tracker = PositionTracker(time_window=5.0, threshold=0.4)
        for i in range(10):
            tracker.add_position(_make_pose(float(i), float(i)))
        assert tracker.is_stuck() is False

    def test_reset_clears_data(self) -> None:
        tracker = PositionTracker(time_window=5.0, threshold=0.4)
        for _ in range(10):
            tracker.add_position(_make_pose(1.0, 1.0))
        assert tracker.is_stuck() is True

        tracker.reset_data()
        assert tracker.is_stuck() is False

    def test_small_oscillation_within_threshold_is_stuck(self) -> None:
        """Robot oscillating within a small range -- should be stuck."""
        tracker = PositionTracker(time_window=5.0, threshold=0.4)
        for i in range(20):
            offset = 0.05 * (i % 2)
            tracker.add_position(_make_pose(1.0 + offset, 1.0 - offset))
        assert tracker.is_stuck() is True


# ---------------------------------------------------------------------------
# ReplanLimiter
# ---------------------------------------------------------------------------


class TestReplanLimiter:
    def test_initial_can_retry(self) -> None:
        limiter = ReplanLimiter()
        assert limiter.can_retry(Vector3(0.0, 0.0)) is True

    def test_exhausts_max_attempts(self) -> None:
        """Retrying at the same position should be rejected after max attempts."""
        limiter = ReplanLimiter()
        pos = Vector3(1.0, 1.0)
        for _ in range(6):
            assert limiter.can_retry(pos) is True
            limiter.will_retry()
        assert limiter.can_retry(pos) is False

    def test_moving_away_resets_attempts(self) -> None:
        """Moving far enough should reset the retry counter."""
        limiter = ReplanLimiter()
        pos_a = Vector3(1.0, 1.0)
        for _ in range(5):
            limiter.can_retry(pos_a)
            limiter.will_retry()

        pos_b = Vector3(10.0, 10.0)
        assert limiter.can_retry(pos_b) is True
        assert limiter.get_attempt() == 0

    def test_reset_clears_attempts(self) -> None:
        limiter = ReplanLimiter()
        pos = Vector3(0.0, 0.0)
        for _ in range(5):
            limiter.can_retry(pos)
            limiter.will_retry()
        assert limiter.get_attempt() == 5

        limiter.reset()
        assert limiter.get_attempt() == 0

    def test_get_attempt_tracks_count(self) -> None:
        limiter = ReplanLimiter()
        assert limiter.get_attempt() == 0
        limiter.will_retry()
        assert limiter.get_attempt() == 1
        limiter.will_retry()
        assert limiter.get_attempt() == 2


def test_required_navigation_source_health_gates_and_latches_planner_goals() -> None:
    module = ReplanningAStarPlanner(require_navigation_source_health=True)
    try:
        module._planner = MagicMock()
        goal = _make_pose(1.0, 0.0)

        assert module.set_goal(goal) is False
        module._planner.handle_goal_request.assert_not_called()

        module._on_navigation_source_health(Bool(data=True))
        assert module.set_goal(goal) is True
        module._planner.handle_goal_request.assert_called_once_with(
            goal, entry_source="rpc_set_goal"
        )

        module._on_navigation_source_health(Bool(data=False))
        module._planner.cancel_goal.assert_called_once_with(
            failure_reason="navigation_source_fault"
        )

        module._on_navigation_source_health(Bool(data=True))
        assert module.set_goal(goal) is False
    finally:
        module._close_module()


# ---------------------------------------------------------------------------
# GlobalPlanner (goal handling and state management)
# ---------------------------------------------------------------------------


class TestGlobalPlannerGoalHandling:
    @pytest.fixture
    def planner(self) -> "MagicMock":
        """Create a GlobalPlanner with external dependencies mocked out."""
        from dimos.navigation.replanning_a_star.global_planner import GlobalPlanner

        mock_config = MagicMock()
        mock_config.simulation = False
        mock_config.robot_rotation_diameter = 0.6

        with (
            patch("dimos.navigation.replanning_a_star.global_planner.NavigationMap"),
            patch("dimos.navigation.replanning_a_star.global_planner.LocalPlanner"),
        ):
            gp = GlobalPlanner(mock_config)

        return gp

    def test_initial_state_no_goal(self, planner: "MagicMock") -> None:
        assert planner._current_goal is None
        assert planner._current_odom is None
        assert planner.is_goal_reached() is False

    def test_handle_odom_updates_position(self, planner: "MagicMock") -> None:
        pose = _make_pose(5.0, 3.0)
        planner.handle_odom(pose)
        assert planner._current_odom is pose

    def test_handle_goal_sets_goal(self, planner: "MagicMock") -> None:
        planner.handle_odom(_make_pose(0.0, 0.0))
        planner._navigation_map = MagicMock()
        planner._navigation_map.binary_costmap = MagicMock()

        with patch.object(planner, "_plan_path"):
            goal = _make_pose(5.0, 5.0)
            planner.handle_goal_request(goal)
            assert planner._current_goal is goal
            assert planner._goal_reached is False

    def test_cancel_goal_clears_state(self, planner: "MagicMock") -> None:
        planner._current_goal = _make_pose(5.0, 5.0)
        planner.cancel_goal()
        assert planner._current_goal is None

    def test_cancel_goal_arrived_sets_reached(self, planner: "MagicMock") -> None:
        planner._current_goal = _make_pose(5.0, 5.0)
        planner.cancel_goal(arrived=True)
        assert planner.is_goal_reached() is True

    def test_cancel_with_retry_keeps_goal(self, planner: "MagicMock") -> None:
        goal = _make_pose(5.0, 5.0)
        planner._current_goal = goal
        planner.cancel_goal(but_will_try_again=True)
        assert planner._current_goal is goal

    def test_set_safe_goal_clearance(self, planner: "MagicMock") -> None:
        planner.set_safe_goal_clearance(1.5)
        assert planner._safe_goal_clearance == 1.5

    def test_set_replanning_enabled(self, planner: "MagicMock") -> None:
        planner.set_replanning_enabled(False)
        assert planner._replanning_enabled is False
        planner.set_replanning_enabled(True)
        assert planner._replanning_enabled is True

    def test_goal_arriving_during_shutdown_is_ignored(self, planner: "MagicMock") -> None:
        planner._stop_planner.set()
        goal = _make_pose(1.0, 0.0)

        with patch.object(planner, "_plan_path") as plan_path:
            planner.handle_goal_request(goal)

        assert planner._current_goal is None
        plan_path.assert_not_called()

    def test_goal_cancelled_during_plan_does_not_assert(self, planner: "MagicMock") -> None:
        planner._current_odom = _make_pose(0.0, 0.0)
        planner._current_goal = _make_pose(1.0, 0.0)

        def cancel_during_shutdown(**_kwargs: object) -> None:
            planner._current_goal = None
            planner._stop_planner.set()

        planner.cancel_goal = cancel_during_shutdown

        planner._plan_path("shutdown_race")

        planner._local_planner.start_planning.assert_not_called()
