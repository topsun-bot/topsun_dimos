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

"""Behavior tests for the deterministic visual docking state machine."""

from __future__ import annotations

import numpy as np
import pytest

from dimos.robot.unitree.go2.recharge.config import RechargeConfig
from dimos.robot.unitree.go2.recharge.controller import ArucoRechargeController
from dimos.robot.unitree.go2.recharge.types import (
    MarkerObservation,
    RechargeErrorCode,
    RechargeState,
)


def _observation(
    *, now: float, x_m: float = 0.0, z_m: float = 0.35, yaw_rad: float = 0.0
) -> MarkerObservation:
    return MarkerObservation(
        corners_px=np.zeros((4, 2), dtype=np.float64),
        x_m=x_m,
        y_m=0.0,
        z_m=z_m,
        yaw_rad=yaw_rad,
        reprojection_error_px=0.1,
        observed_at=now,
    )


def _tick(controller: ArucoRechargeController, now: float, observation: MarkerObservation | None):
    return controller.tick(now, observation, image_age_s=0.0, lowstate_age_s=0.0)


def test_controller_moves_forward_by_camera_marker_distance_error() -> None:
    controller = ArucoRechargeController(
        RechargeConfig(min_stable_frames=1, align_yaw_exit_rad=0.05)
    )
    controller.start(0.0)

    _tick(controller, 0.0, _observation(now=0.0, z_m=1.0))
    _tick(controller, 0.1, _observation(now=0.1, z_m=1.0))
    _tick(controller, 0.2, _observation(now=0.2, z_m=1.0))
    command = _tick(controller, 0.3, _observation(now=0.3, z_m=1.0))

    assert controller.state == RechargeState.APPROACH
    assert command.forward_mps == pytest.approx(0.18)  # max_forward_mps
    assert command.yaw_rad_s == 0.0
    assert command.lateral_mps == 0.0


def test_approach_realigns_yaw_without_forward_when_bearing_drifts() -> None:
    """Forward phase must not combine vx and wz; far-field drift returns to ALIGN_YAW."""
    controller = ArucoRechargeController(
        RechargeConfig(
            min_stable_frames=1,
            align_yaw_exit_rad=0.05,
            align_lateral_exit_m=0.05,
            near_field_z_m=0.40,
        )
    )
    controller.start(0.0)
    aligned = _observation(now=0.0, x_m=0.0, z_m=1.0, yaw_rad=0.0)
    _tick(controller, 0.0, aligned)
    _tick(controller, 0.1, aligned)
    _tick(controller, 0.2, aligned)
    assert controller.state == RechargeState.APPROACH

    drifted = _observation(now=0.3, x_m=0.0, z_m=0.9, yaw_rad=0.12)
    stop_command = _tick(controller, 0.3, drifted)

    assert controller.state == RechargeState.ALIGN_YAW
    assert stop_command.forward_mps == 0.0
    assert stop_command.yaw_rad_s == 0.0

    yaw_command = _tick(controller, 0.4, drifted)
    assert yaw_command.forward_mps == 0.0
    assert yaw_command.yaw_rad_s != 0.0


def test_approach_realigns_yaw_in_near_field_when_drifted() -> None:
    """近场 yaw 偏大时仍退回 ALIGN_YAW (脉冲转向)."""
    controller = ArucoRechargeController(
        RechargeConfig(
            min_stable_frames=1,
            align_yaw_exit_rad=0.05,
            align_lateral_exit_m=0.05,
            near_field_z_m=0.60,
            approach_forward_exit_m=0.05,
            target_camera_marker_distance_m=0.32,
        )
    )
    controller.start(0.0)
    far = _observation(now=0.0, x_m=0.0, z_m=1.0, yaw_rad=0.0)
    _tick(controller, 0.0, far)
    _tick(controller, 0.1, far)
    _tick(controller, 0.2, far)
    assert controller.state == RechargeState.APPROACH

    near_drift = _observation(now=0.3, x_m=0.0, z_m=0.50, yaw_rad=0.20)
    stop_command = _tick(controller, 0.3, near_drift)
    assert controller.state == RechargeState.ALIGN_YAW
    assert stop_command.yaw_rad_s == 0.0

    yaw_command = _tick(controller, 0.4, near_drift)
    assert yaw_command.forward_mps == 0.0
    assert yaw_command.yaw_rad_s != 0.0


def test_align_yaw_pulse_duration_scales_with_error() -> None:
    """15° 误差只应脉冲 ~1s, 不是一直转到丢码."""
    controller = ArucoRechargeController(
        RechargeConfig(
            min_stable_frames=1,
            align_yaw_exit_rad=0.05,
            estimated_yaw_rate_rad_s=0.21,
            yaw_pulse_fraction=0.85,
        )
    )
    controller.start(0.0)
    # 15° ≈ 0.262 rad → budget ≈ 0.262/0.21*0.85 ≈ 1.06s
    obs = _observation(now=0.0, x_m=0.10, z_m=1.0, yaw_rad=0.262)
    _tick(controller, 0.0, obs)
    cmd_start = _tick(controller, 0.1, obs)
    assert cmd_start.yaw_rad_s != 0.0
    assert controller._yaw_pulse_until == pytest.approx(0.1 + 0.262 / 0.21 * 0.85, abs=0.05)
    cmd_mid = _tick(controller, 0.5, obs)
    assert cmd_mid.yaw_rad_s != 0.0
    cmd_stop = _tick(controller, 1.2, obs)
    assert cmd_stop.yaw_rad_s == 0.0


def test_controller_uses_marker_center_angle_for_first_version_yaw() -> None:
    controller = ArucoRechargeController(
        RechargeConfig(min_stable_frames=1, align_yaw_exit_rad=0.05)
    )
    controller.start(0.0)
    observation = _observation(now=0.0, x_m=0.10, z_m=1.0, yaw_rad=0.10)

    _tick(controller, 0.0, observation)
    command = _tick(controller, 0.1, observation)

    assert controller.state == RechargeState.ALIGN_YAW
    # 码在右侧 (bearing>0) → 负 angular.z (顺时针). yaw_sign=1.0, 2026-08-05 真机验证.
    assert command.yaw_rad_s == pytest.approx(-0.20)
    assert command.forward_mps == 0.0


def test_align_yaw_reverses_same_angle_budget_on_marker_loss() -> None:
    """丢码后按偏差角等量反转, 不是固定转很久."""
    controller = ArucoRechargeController(
        RechargeConfig(
            min_stable_frames=1,
            align_yaw_exit_rad=0.05,
            estimated_yaw_rate_rad_s=0.20,
            marker_lost_abort_s=0.5,
            image_max_age_s=1.0,
        )
    )
    controller.start(0.0)
    # 18° ≈ 0.314 rad → 等量反转约 1.57 s (at 0.20 rad/s)
    offset = _observation(now=0.0, x_m=0.20, z_m=1.2, yaw_rad=0.314)
    _tick(controller, 0.0, offset)
    yaw_cmd = _tick(controller, 0.5, offset)
    assert controller.state == RechargeState.ALIGN_YAW
    assert yaw_cmd.yaw_rad_s != 0.0
    last_yaw = yaw_cmd.yaw_rad_s

    reverse = _tick(controller, 0.6, None)
    assert controller.state == RechargeState.ALIGN_YAW
    assert reverse.yaw_rad_s * last_yaw < 0.0
    # 预算应约等于 |yaw|/rate ≈ 1.57, 且被实际已转时长 (~0.5s) 卡住
    assert controller.undo_yaw_s == pytest.approx(0.5 * 1.15, abs=0.05)

    # 等量反转窗口内不 fail
    _tick(controller, 1.0, None)
    assert controller.state == RechargeState.ALIGN_YAW

    # 反转预算 + abort 之后才 fail
    _tick(controller, 1.3, None)
    failed = _tick(controller, 1.8, None)
    assert failed.yaw_rad_s == 0.0
    assert controller.state == RechargeState.FAILED
    assert controller.failure is not None
    assert controller.failure.code == RechargeErrorCode.MARKER_LOST


def test_controller_stops_and_latches_image_stale_failure() -> None:
    controller = ArucoRechargeController(RechargeConfig(min_stable_frames=1))
    controller.start(0.0)

    command = controller.tick(0.3, None, image_age_s=0.3, lowstate_age_s=0.0)

    assert command.forward_mps == 0.0
    assert controller.state == RechargeState.FAILED
    assert controller.failure is not None
    assert controller.failure.code == RechargeErrorCode.IMAGE_STALE


def test_controller_requires_explicit_charge_confirmation() -> None:
    config = RechargeConfig(min_stable_frames=1, settle_time_s=0.0)
    controller = ArucoRechargeController(config)
    controller.start(0.0)
    centered = _observation(now=0.0)

    _tick(controller, 0.0, centered)
    _tick(controller, 0.1, centered)
    _tick(controller, 0.2, centered)
    _tick(controller, 0.3, centered)
    _tick(controller, 0.4, centered)
    liedown = _tick(controller, 0.5, centered)

    assert controller.state == RechargeState.LIE_DOWN
    assert liedown.request_liedown is True

    controller.notify_liedown_result(True, 0.6)
    _tick(controller, 0.7, centered)
    assert controller.state == RechargeState.VERIFY_CHARGE

    _tick(controller, 0.8, centered)
    assert controller.state == RechargeState.VERIFY_CHARGE

    _tick(controller, 0.9, centered)
    controller.tick(1.0, centered, image_age_s=0.0, lowstate_age_s=0.0, charge_confirmed=True)
    assert controller.state == RechargeState.SUCCEEDED


def test_controller_can_finish_visual_only_validation_without_claiming_charge() -> None:
    controller = ArucoRechargeController(RechargeConfig(min_stable_frames=1, settle_time_s=0.0))
    controller.start(0.0)
    centered = _observation(now=0.0)

    for now in (0.0, 0.1, 0.2, 0.3, 0.4):
        _tick(controller, now, centered)
    _tick(controller, 0.5, centered)
    controller.complete_visual_dock(0.6)

    assert controller.state == RechargeState.VISUAL_DOCKED
    assert controller.failure is None
