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

import math

import numpy as np
import pytest

from dimos.navigation.visual_servoing.vlx_waypoint_following import (
    Pose2D,
    TargetObservation,
    VlxPurePursuitController,
    VlxWaypointPlanner,
    reference_points_to_body,
)


def _observe(planner: VlxWaypointPlanner, timestamp_s: float, x: float, y: float) -> None:
    planner.observe(TargetObservation(timestamp_s=timestamp_s, x=x, y=y))


def test_plans_trackvla_sized_straight_horizon() -> None:
    planner = VlxWaypointPlanner()
    _observe(planner, 0.0, 3.0, 0.0)

    plan = planner.plan(Pose2D(0.0, 0.0, 0.0))

    assert plan is not None
    assert plan.waypoints_body.shape == (10, 2)
    assert plan.waypoints_body[-1, 0] == pytest.approx(1.0)
    assert plan.waypoints_body[-1, 1] == pytest.approx(0.0)
    assert not plan.blocked


def test_stops_at_follow_distance_but_keeps_facing_target() -> None:
    planner = VlxWaypointPlanner()
    controller = VlxPurePursuitController()
    _observe(planner, 0.0, 1.5, 0.2)

    plan = planner.plan(Pose2D(0.0, 0.0, 0.0))

    assert plan is not None
    command = controller.compute(plan)
    assert np.allclose(plan.waypoints_body, 0.0)
    assert command.linear_x_mps == 0.0
    assert command.angular_z_rps > 0.0


def test_temporal_history_leads_a_laterally_moving_target() -> None:
    planner = VlxWaypointPlanner()
    static_planner = VlxWaypointPlanner()
    for index in range(8):
        _observe(planner, index * 0.1, 3.0, index * 0.05)
    _observe(static_planner, 0.7, 3.0, 0.35)

    plan = planner.plan(Pose2D(0.0, 0.0, 0.0))
    static_plan = static_planner.plan(Pose2D(0.0, 0.0, 0.0))

    assert plan is not None
    assert static_plan is not None
    assert plan.target_velocity_reference_mps[1] == pytest.approx(0.5, abs=1e-6)
    assert plan.waypoints_body[-1, 1] > static_plan.waypoints_body[-1, 1]


def test_target_velocity_is_clamped_to_physical_limit() -> None:
    planner = VlxWaypointPlanner()
    _observe(planner, 0.0, 3.0, 0.0)
    _observe(planner, 0.1, 3.0, 10.0)

    plan = planner.plan(Pose2D(0.0, 0.0, 0.0))

    assert plan is not None
    assert np.linalg.norm(plan.target_velocity_reference_mps) == pytest.approx(1.8)


def test_wall_across_path_triggers_hard_stop() -> None:
    planner = VlxWaypointPlanner()
    controller = VlxPurePursuitController()
    _observe(planner, 0.0, 3.0, 0.0)
    wall = np.column_stack(
        [
            np.full(41, 0.35),
            np.linspace(-1.0, 1.0, 41),
        ]
    )

    plan = planner.plan(Pose2D(0.0, 0.0, 0.0), wall)

    assert plan is not None
    command = controller.compute(plan, wall)
    assert plan.blocked
    assert np.allclose(plan.waypoints_body, 0.0)
    assert command.linear_x_mps == 0.0
    assert command.safety_limited


def test_side_obstacle_does_not_stop_forward_motion() -> None:
    planner = VlxWaypointPlanner()
    controller = VlxPurePursuitController()
    _observe(planner, 0.0, 3.0, 0.0)
    obstacles = np.array([[0.4, 1.2], [0.8, 1.1]], dtype=np.float64)

    plan = planner.plan(Pose2D(0.0, 0.0, 0.0), obstacles)

    assert plan is not None
    command = controller.compute(plan, obstacles)
    assert not plan.blocked
    assert command.linear_x_mps > 0.0
    assert not command.safety_limited


def test_reference_points_transform_with_robot_yaw() -> None:
    points = np.array([[1.0, 2.0], [0.0, 1.0]], dtype=np.float64)
    robot_pose = Pose2D(1.0, 1.0, math.pi / 2.0)

    body_points = reference_points_to_body(points, robot_pose)

    assert body_points[0] == pytest.approx([1.0, 0.0])
    assert body_points[1] == pytest.approx([0.0, 1.0])


def test_rejects_out_of_order_observations() -> None:
    planner = VlxWaypointPlanner()
    _observe(planner, 1.0, 2.0, 0.0)

    with pytest.raises(ValueError, match="timestamp ordered"):
        _observe(planner, 0.9, 2.0, 0.0)
