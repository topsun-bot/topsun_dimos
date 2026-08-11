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

"""Reachability gates must reject obstacles, unknown cells, and frame mismatch."""

from __future__ import annotations

import numpy as np

from dimos.msgs.geometry_msgs.Pose import Pose
from dimos.msgs.nav_msgs.OccupancyGrid import OccupancyGrid
from dimos.robot.unitree.go2.recharge.config import RechargeConfig
from dimos.robot.unitree.go2.recharge.dock_geometry import build_dock_target
from dimos.robot.unitree.go2.recharge.reachability import validate_dock_target
from dimos.robot.unitree.go2.recharge.types import DockObservation, PlanarPose


def _observation() -> DockObservation:
    return DockObservation(
        corners_px=np.zeros((4, 2), dtype=np.float64),
        x_m=0.0,
        y_m=0.0,
        z_m=1.0,
        yaw_rad=0.0,
        reprojection_error_px=0.2,
        observed_at=1.0,
        marker_id=0,
        image_width=640,
        image_height=360,
        rvec=np.zeros(3, dtype=np.float64),
        tvec=np.array([0.0, 0.0, 1.0], dtype=np.float64),
        min_corner_margin_px=50.0,
        marker_side_px=30.0,
    )


def _free_costmap(frame_id: str = "world") -> OccupancyGrid:
    return OccupancyGrid(
        grid=np.zeros((200, 200), dtype=np.int8),
        resolution=0.05,
        origin=Pose(-5.0, -5.0, 0.0),
        frame_id=frame_id,
    )


def test_reachability_accepts_free_staging_and_final_corridor() -> None:
    config = RechargeConfig(corridor_clearance_m=0.10)
    robot = PlanarPose(0.0, 0.0, 0.0, "world", 1.0)
    observation = _observation()
    target = build_dock_target(observation, robot, config)
    assert target is not None

    result = validate_dock_target(observation, robot, target, _free_costmap(), config)

    assert result.accepted is True
    assert result.reason is None


def test_reachability_rejects_unknown_final_target() -> None:
    config = RechargeConfig(corridor_clearance_m=0.10)
    robot = PlanarPose(0.0, 0.0, 0.0, "world", 1.0)
    observation = _observation()
    target = build_dock_target(observation, robot, config)
    assert target is not None
    costmap = _free_costmap()
    final_grid = costmap.world_to_grid((target.final_pose.x, target.final_pose.y, 0.0))
    costmap.grid[round(final_grid.y), round(final_grid.x)] = -1

    result = validate_dock_target(observation, robot, target, costmap, config)

    assert result.accepted is False
    assert result.reason == "dock_target_blocked"


def test_reachability_rejects_frame_mismatch() -> None:
    config = RechargeConfig(corridor_clearance_m=0.10)
    robot = PlanarPose(0.0, 0.0, 0.0, "odom", 1.0)
    observation = _observation()
    target = build_dock_target(observation, robot, config)
    assert target is not None

    result = validate_dock_target(observation, robot, target, _free_costmap("world"), config)

    assert result.accepted is False
    assert result.reason == "frame_transform_unavailable"
