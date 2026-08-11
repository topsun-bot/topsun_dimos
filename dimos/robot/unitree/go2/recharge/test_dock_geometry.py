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

"""Geometry contracts for centreline staging and final charger placement."""

from __future__ import annotations

import math

import numpy as np
import pytest

from dimos.robot.unitree.go2.recharge.config import RechargeConfig
from dimos.robot.unitree.go2.recharge.dock_geometry import (
    build_dock_target,
    centerline_error_m,
    distance_between,
)
from dimos.robot.unitree.go2.recharge.types import DockObservation, PlanarPose


def _observation(*, x_m: float = 0.0, z_m: float = 1.0) -> DockObservation:
    return DockObservation(
        corners_px=np.zeros((4, 2), dtype=np.float64),
        x_m=x_m,
        y_m=0.0,
        z_m=z_m,
        yaw_rad=math.atan2(x_m, z_m),
        reprojection_error_px=0.2,
        observed_at=1.0,
        marker_id=0,
        image_width=640,
        image_height=360,
        rvec=np.zeros(3, dtype=np.float64),
        tvec=np.array([x_m, 0.0, z_m], dtype=np.float64),
        min_corner_margin_px=50.0,
        marker_side_px=30.0,
    )


def test_dock_target_places_staging_and_final_robot_centres_on_marker_normal() -> None:
    robot = PlanarPose(0.0, 0.0, 0.0, "world", 1.0)

    target = build_dock_target(_observation(), robot, RechargeConfig())

    assert target is not None
    assert distance_between(target.marker_pose, target.staging_pose) == pytest.approx(1.10)
    assert distance_between(target.marker_pose, target.final_pose) == pytest.approx(0.70)
    assert centerline_error_m(robot, target) == pytest.approx(0.0)
    assert target.final_pose.x == pytest.approx(0.65)


def test_side_observation_generates_staging_point_on_dock_centreline() -> None:
    robot = PlanarPose(0.0, 0.0, 0.0, "world", 1.0)

    target = build_dock_target(_observation(x_m=0.40), robot, RechargeConfig())

    assert target is not None
    assert target.staging_pose.y == pytest.approx(target.marker_pose.y)
    assert centerline_error_m(robot, target) == pytest.approx(0.40)
