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

"""Behavior tests for robust recharge-tag pose filtering."""

from __future__ import annotations

import numpy as np

from dimos.robot.unitree.go2.recharge.config import RechargeConfig
from dimos.robot.unitree.go2.recharge.stability import PoseStabilityWindow
from dimos.robot.unitree.go2.recharge.types import DockObservation


def _observation(now: float, *, x_m: float = 0.0, z_m: float = 0.75) -> DockObservation:
    return DockObservation(
        corners_px=np.array([[100, 100], [140, 100], [140, 140], [100, 140]], dtype=np.float64),
        x_m=x_m,
        y_m=0.0,
        z_m=z_m,
        yaw_rad=float(np.arctan2(x_m, z_m)),
        reprojection_error_px=0.3,
        observed_at=now,
        marker_id=0,
        image_width=640,
        image_height=360,
        rvec=np.zeros(3, dtype=np.float64),
        tvec=np.array([x_m, 0.0, z_m], dtype=np.float64),
        min_corner_margin_px=100.0,
        marker_side_px=40.0,
    )


def test_stability_requires_five_valid_frames_from_seven_frame_window() -> None:
    window = PoseStabilityWindow(RechargeConfig())

    for index in range(4):
        result = window.push(_observation(float(index)))

    assert result is None
    stable = window.push(_observation(4.0))
    assert stable is not None
    assert stable.valid_frames == 5


def test_stability_uses_median_instead_of_latest_pose() -> None:
    window = PoseStabilityWindow(RechargeConfig(stable_z_mad_m=0.10, stable_x_mad_m=0.10))
    for index, z_m in enumerate([0.74, 0.75, 0.76, 0.75, 1.20]):
        stable = window.push(_observation(float(index), z_m=z_m))

    assert stable is not None
    assert stable.observation.z_m == 0.75


def test_stability_rejects_inconsistent_depth_window() -> None:
    window = PoseStabilityWindow(RechargeConfig(stable_z_mad_m=0.01))

    for index, z_m in enumerate([0.60, 0.70, 0.80, 0.90, 1.00]):
        stable = window.push(_observation(float(index), z_m=z_m))

    assert stable is None
