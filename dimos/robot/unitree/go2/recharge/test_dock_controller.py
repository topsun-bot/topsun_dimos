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

"""Trajectory-level contracts for the sampled visual docking controller."""

from __future__ import annotations

import math

import numpy as np
import pytest

from dimos.robot.unitree.go2.recharge.config import RechargeConfig
from dimos.robot.unitree.go2.recharge.dock_controller import DockController
from dimos.robot.unitree.go2.recharge.types import (
    AutoRechargeState,
    DockObservation,
    StableDockObservation,
)


def _stable(
    now: float,
    *,
    z_m: float = 0.75,
    bearing_rad: float = 0.0,
    z_mad_m: float = 0.005,
) -> StableDockObservation:
    x_m = math.tan(bearing_rad) * z_m
    observation = DockObservation(
        corners_px=np.array([[100, 100], [140, 100], [140, 140], [100, 140]], dtype=np.float64),
        x_m=x_m,
        y_m=0.0,
        z_m=z_m,
        yaw_rad=bearing_rad,
        reprojection_error_px=0.2,
        observed_at=now,
        marker_id=0,
        image_width=640,
        image_height=360,
        rvec=np.zeros(3, dtype=np.float64),
        tvec=np.array([x_m, 0.0, z_m], dtype=np.float64),
        min_corner_margin_px=50.0,
        marker_side_px=40.0,
    )
    return StableDockObservation(
        observation=observation,
        valid_frames=5,
        window_frames=7,
        z_mad_m=z_mad_m,
        x_mad_m=0.005,
        bearing_mad_rad=0.01,
        normal_yaw_mad_rad=0.01,
    )


def _tick(
    controller: DockController,
    now: float,
    stable: StableDockObservation | None,
    *,
    image_received_at: float | None = None,
    recovery_distance_m: float = 0.0,
    charge_confirmed: bool | None = None,
):
    return controller.tick(
        now,
        stable,
        image_received_at=now if image_received_at is None else image_received_at,
        image_age_s=0.0,
        odom_age_s=0.0,
        lowstate_age_s=0.0,
        recovery_distance_m=recovery_distance_m,
        rear_corridor_safe=True,
        charge_confirmed=charge_confirmed,
    )


def test_bearing_error_outputs_yaw_only_and_never_lateral_motion() -> None:
    controller = DockController(RechargeConfig())
    controller.start_servo(0.0)

    command = _tick(controller, 0.30, _stable(0.30, bearing_rad=math.radians(20)))

    assert controller.state == AutoRechargeState.VISUAL_SERVO
    assert command.yaw_rad_s == pytest.approx(-0.20)
    assert command.forward_mps == 0.0
    assert command.lateral_mps == 0.0


def test_small_bearing_moves_forward_with_one_axis_pulse() -> None:
    controller = DockController(RechargeConfig())
    controller.start_servo(0.0)

    command = _tick(controller, 0.30, _stable(0.30, z_m=0.75, bearing_rad=math.radians(3)))

    assert command.forward_mps == pytest.approx(0.10)
    assert command.yaw_rad_s == 0.0
    assert command.pulse_duration_s == pytest.approx(0.20)


def test_forward_motion_is_blocked_when_costmap_corridor_is_unsafe() -> None:
    controller = DockController(RechargeConfig())
    controller.start_servo(0.0)

    command = controller.tick(
        0.30,
        _stable(0.30, z_m=0.75),
        image_received_at=0.30,
        image_age_s=0.0,
        odom_age_s=0.0,
        lowstate_age_s=0.0,
        forward_corridor_safe=False,
    )

    assert command.forward_mps == 0.0
    assert controller.state == AutoRechargeState.FAILED
    assert controller.failure is not None
    assert controller.failure.message == "visual_forward_corridor_blocked"


def test_pulse_requires_zero_and_new_post_motion_image_before_next_command() -> None:
    controller = DockController(RechargeConfig())
    controller.start_servo(0.0)
    stable = _stable(0.30, z_m=0.75)
    first = _tick(controller, 0.30, stable)
    assert first.forward_mps != 0.0

    during = _tick(controller, 0.40, stable, image_received_at=0.40)
    ended = _tick(controller, 0.51, stable, image_received_at=0.51)
    stale_after = _tick(controller, 0.80, stable, image_received_at=0.40)

    assert during.forward_mps != 0.0
    assert ended.forward_mps == 0.0
    assert stale_after.forward_mps == 0.0
    fresh = _tick(controller, 0.90, _stable(0.90, z_m=0.70), image_received_at=0.90)
    assert fresh.forward_mps != 0.0


def test_critical_overshoot_calculates_053m_straight_backoff_goal() -> None:
    controller = DockController(RechargeConfig())
    controller.start_servo(0.0)

    stop = _tick(controller, 0.30, _stable(0.30, z_m=0.22))

    assert controller.state == AutoRechargeState.RECOVERY_STOP
    assert controller.recovery_goal_m == pytest.approx(0.53)
    assert stop.forward_mps == 0.0
    _tick(controller, 0.40, _stable(0.40, z_m=0.22))
    backoff = _tick(controller, 0.50, _stable(0.50, z_m=0.22))
    assert backoff.forward_mps == pytest.approx(-0.10)
    assert backoff.yaw_rad_s == 0.0


def test_near_field_marker_loss_stops_then_enters_backoff() -> None:
    controller = DockController(RechargeConfig())
    controller.start_servo(0.0)
    _tick(controller, 0.30, _stable(0.30, z_m=0.50, bearing_rad=math.radians(16)))
    assert controller.state == AutoRechargeState.RECOVERY_STOP

    zero = _tick(controller, 0.40, None)
    backoff = _tick(controller, 0.50, None)

    assert zero.forward_mps == 0.0
    assert backoff.forward_mps == pytest.approx(-0.10)
    assert backoff.yaw_rad_s == 0.0


def test_reacquired_marker_below_window_continues_backoff() -> None:
    controller = DockController(RechargeConfig())
    controller.start_servo(0.0)
    _tick(controller, 0.30, _stable(0.30, z_m=0.22))
    _tick(controller, 0.40, _stable(0.40, z_m=0.22))
    first_backoff = _tick(controller, 0.50, _stable(0.50, z_m=0.22))
    assert first_backoff.forward_mps < 0.0
    _tick(controller, 0.71, _stable(0.71, z_m=0.58))
    _tick(controller, 1.00, _stable(1.00, z_m=0.58), image_received_at=1.00)

    next_backoff = _tick(controller, 1.10, _stable(1.10, z_m=0.58))

    assert controller.state == AutoRechargeState.RECOVERY_BACKOFF
    assert next_backoff.forward_mps < 0.0


def test_final_pose_must_hold_before_liedown_request() -> None:
    controller = DockController(RechargeConfig(final_stable_time_s=0.50))
    controller.start_servo(0.0)
    final = _stable(0.30, z_m=0.35, bearing_rad=math.radians(10))

    entered = _tick(controller, 0.30, final)
    holding = _tick(controller, 0.60, _stable(0.60, z_m=0.35, bearing_rad=math.radians(10)))
    ready = _tick(controller, 0.90, _stable(0.90, z_m=0.35, bearing_rad=math.radians(10)))
    request = _tick(controller, 1.00, _stable(1.00, z_m=0.35, bearing_rad=math.radians(10)))

    assert entered.request_liedown is False
    assert holding.request_liedown is False
    assert ready.request_liedown is False
    assert controller.state == AutoRechargeState.LIE_DOWN
    assert request.request_liedown is True
