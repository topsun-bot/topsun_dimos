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

"""Sampled visual-servo controller with explicit near-field backoff recovery."""

from __future__ import annotations

import math

from dimos.robot.unitree.go2.recharge.config import RechargeConfig
from dimos.robot.unitree.go2.recharge.types import (
    AutoRechargeErrorCode,
    AutoRechargeFailure,
    AutoRechargeState,
    DockObservation,
    RechargeCommand,
    StableDockObservation,
)


def _clamp(value: float, minimum: float, maximum: float) -> float:
    return max(minimum, min(maximum, value))


_DOCKING_STATES = {
    AutoRechargeState.STOP_AND_OBSERVE,
    AutoRechargeState.VISUAL_SERVO,
    AutoRechargeState.RECOVERY_STOP,
    AutoRechargeState.RECOVERY_BACKOFF,
    AutoRechargeState.RECOVERY_REACQUIRE,
    AutoRechargeState.FINAL_SETTLE,
    AutoRechargeState.LIE_DOWN,
}


class DockController:
    """Pure final-docking controller; the module owns streams and physical RPCs."""

    def __init__(self, config: RechargeConfig) -> None:
        self.config = config
        self.state = AutoRechargeState.MONITOR
        self.failure: AutoRechargeFailure | None = None
        self._started_at: float | None = None
        self._state_started_at: float | None = None
        self._last_observation: DockObservation | None = None
        self._pulse_until: float | None = None
        self._pulse_command = RechargeCommand()
        self._await_image_after: float | None = None
        self._settled_since: float | None = None
        self._liedown_requested = False
        self._standup_requested = False
        self._dock_attempt = 1
        self._recovery_attempt = 0
        self._recovery_goal_m = 0.0

    @property
    def last_observation(self) -> DockObservation | None:
        return self._last_observation

    @property
    def recovery_goal_m(self) -> float:
        return self._recovery_goal_m

    @property
    def dock_attempt(self) -> int:
        return self._dock_attempt

    @property
    def active(self) -> bool:
        return self.state not in {
            AutoRechargeState.MONITOR,
            AutoRechargeState.CHARGING_HOLD,
            AutoRechargeState.SUCCEEDED,
            AutoRechargeState.FAILED,
            AutoRechargeState.CANCELLED,
        }

    def start_servo(self, now: float) -> None:
        """Start final docking only after MovementManager grants servo ownership."""
        self.state = AutoRechargeState.STOP_AND_OBSERVE
        self.failure = None
        self._started_at = now
        self._state_started_at = now
        self._last_observation = None
        self._pulse_until = None
        self._pulse_command = RechargeCommand()
        self._await_image_after = now
        self._settled_since = None
        self._liedown_requested = False
        self._standup_requested = False
        self._dock_attempt = 1
        self._recovery_attempt = 0
        self._recovery_goal_m = 0.0

    def cancel(self, now: float) -> RechargeCommand:
        """Latch operator cancellation and return an immediate zero command."""
        if self.state not in {
            AutoRechargeState.MONITOR,
            AutoRechargeState.FAILED,
            AutoRechargeState.CANCELLED,
        }:
            self._fail(
                AutoRechargeErrorCode.OPERATOR_CANCELLED,
                "operator_cancelled",
                now,
            )
            self.state = AutoRechargeState.CANCELLED
        return RechargeCommand(reason="operator_cancelled")

    def notify_liedown_result(self, success: bool, now: float) -> None:
        """Advance to charge verification only after the robot RPC succeeds."""
        if self.state != AutoRechargeState.LIE_DOWN:
            return
        if not success:
            self._fail(AutoRechargeErrorCode.LIE_DOWN_FAILED, "liedown_rpc_failed", now)
            return
        self._transition(AutoRechargeState.VERIFY_CHARGE, now)

    def notify_standup_result(self, success: bool, now: float) -> None:
        """After a failed charge check, stand up and retreat to the recovery window."""
        if self.state != AutoRechargeState.STAND_UP_RETRY:
            return
        if not success:
            self._fail(AutoRechargeErrorCode.STAND_UP_FAILED, "standup_rpc_failed", now)
            return
        self._dock_attempt += 1
        self._standup_requested = False
        self._begin_recovery(now, "charge_unverified_retry")

    def complete_visual_validation(self, now: float) -> None:
        """End a no-lie-down field phase without claiming that charging succeeded."""
        if self.state == AutoRechargeState.LIE_DOWN:
            self._transition(AutoRechargeState.SUCCEEDED, now)

    def tick(
        self,
        now: float,
        stable: StableDockObservation | None,
        *,
        image_received_at: float | None,
        image_age_s: float,
        odom_age_s: float,
        lowstate_age_s: float,
        recovery_distance_m: float = 0.0,
        rear_corridor_safe: bool = True,
        forward_corridor_safe: bool = True,
        charge_confirmed: bool | None = None,
    ) -> RechargeCommand:
        """Advance one deterministic control tick and return at most one-axis motion."""
        if self.state in {
            AutoRechargeState.MONITOR,
            AutoRechargeState.CHARGING_HOLD,
            AutoRechargeState.SUCCEEDED,
            AutoRechargeState.FAILED,
            AutoRechargeState.CANCELLED,
        }:
            return RechargeCommand()

        if (
            self.config.total_timeout_s > 0.0
            and self._started_at is not None
            and now - self._started_at > self.config.total_timeout_s
        ):
            return self._fail(
                AutoRechargeErrorCode.TOTAL_TIMEOUT,
                "automatic_recharge_total_timeout",
                now,
            )

        if self.state in _DOCKING_STATES:
            if image_age_s > self.config.image_max_age_s:
                return self._fail(AutoRechargeErrorCode.IMAGE_STALE, "image_stale", now)
            if odom_age_s > self.config.odom_max_age_s:
                return self._fail(AutoRechargeErrorCode.ODOM_STALE, "odom_stale", now)
        if lowstate_age_s > self.config.lowstate_max_age_s:
            return self._fail(AutoRechargeErrorCode.LOWSTATE_STALE, "lowstate_stale", now)

        observation = stable.observation if stable is not None else None
        if observation is not None and now - observation.observed_at > self.config.image_max_age_s:
            stable = None
            observation = None
        if observation is not None:
            self._last_observation = observation

        pulse_command = self._tick_pulse(now, image_received_at)
        if pulse_command is not None:
            return pulse_command

        if self.state == AutoRechargeState.STOP_AND_OBSERVE:
            if observation is None:
                return RechargeCommand(reason="waiting_for_stable_observation")
            self._transition(AutoRechargeState.VISUAL_SERVO, now)

        if self.state == AutoRechargeState.VISUAL_SERVO:
            if self._state_elapsed(now) > self.config.visual_servo_timeout_s:
                return self._fail(
                    AutoRechargeErrorCode.VISUAL_SERVO_TIMEOUT,
                    "visual_servo_timeout",
                    now,
                )
            return self._visual_servo(now, stable, forward_corridor_safe)

        if self.state == AutoRechargeState.RECOVERY_STOP:
            if not rear_corridor_safe:
                return self._fail(
                    AutoRechargeErrorCode.RECOVERY_CORRIDOR_BLOCKED,
                    "rear_corridor_blocked",
                    now,
                )
            self._transition(AutoRechargeState.RECOVERY_BACKOFF, now)
            return RechargeCommand(reason="recovery_zero_before_backoff")

        if self.state == AutoRechargeState.RECOVERY_BACKOFF:
            if self._state_elapsed(now) > self.config.recovery_timeout_s:
                return self._fail(
                    AutoRechargeErrorCode.NEAR_FIELD_REACQUIRE_FAILED,
                    "recovery_backoff_timeout",
                    now,
                )
            if not rear_corridor_safe:
                return self._fail(
                    AutoRechargeErrorCode.RECOVERY_CORRIDOR_BLOCKED,
                    "rear_corridor_became_blocked",
                    now,
                )
            if recovery_distance_m > self.config.recovery_max_total_backoff_m:
                return self._fail(
                    AutoRechargeErrorCode.RECOVERY_DISTANCE_EXCEEDED,
                    "recovery_total_backoff_exceeded",
                    now,
                )
            if observation is not None and observation.z_m >= self.config.recovery_z_min_m:
                self._transition(AutoRechargeState.RECOVERY_REACQUIRE, now)
                return RechargeCommand(reason="recovery_window_observed")
            if recovery_distance_m >= self._recovery_goal_m:
                self._transition(AutoRechargeState.RECOVERY_REACQUIRE, now)
                return RechargeCommand(reason="recovery_odom_goal_reached")
            return self._start_pulse(
                now,
                RechargeCommand(
                    forward_mps=self.config.backoff_axis,
                    pulse_duration_s=self.config.backoff_pulse_s,
                    reason="recovery_straight_backoff",
                ),
            )

        if self.state == AutoRechargeState.RECOVERY_REACQUIRE:
            if observation is not None:
                if observation.z_m < self.config.recovery_z_min_m:
                    if recovery_distance_m >= self.config.recovery_max_total_backoff_m:
                        return self._fail(
                            AutoRechargeErrorCode.RECOVERY_DISTANCE_EXCEEDED,
                            "marker_reacquired_but_still_too_close",
                            now,
                        )
                    self._transition(AutoRechargeState.RECOVERY_BACKOFF, now)
                    return RechargeCommand(reason="reacquired_but_continue_backoff")
                self._transition(AutoRechargeState.VISUAL_SERVO, now)
                return RechargeCommand(reason="marker_reacquired")
            if self._state_elapsed(now) > self.config.recovery_timeout_s:
                return self._fail(
                    AutoRechargeErrorCode.NEAR_FIELD_REACQUIRE_FAILED,
                    "marker_not_reacquired_after_backoff",
                    now,
                )
            return RechargeCommand(reason="waiting_recovery_reacquire")

        if self.state == AutoRechargeState.FINAL_SETTLE:
            return self._final_settle(now, stable)

        if self.state == AutoRechargeState.LIE_DOWN:
            if not self._liedown_requested:
                self._liedown_requested = True
                return RechargeCommand(request_liedown=True, reason="final_pose_stable")
            return RechargeCommand(reason="waiting_liedown_result")

        if self.state == AutoRechargeState.VERIFY_CHARGE:
            if charge_confirmed is True:
                self._transition(AutoRechargeState.CHARGING_HOLD, now)
                return RechargeCommand(reason="charge_confirmed")
            if self._state_elapsed(now) > self.config.verify_charge_timeout_s:
                if self._dock_attempt >= self.config.max_dock_attempts:
                    return self._fail(
                        AutoRechargeErrorCode.CHARGE_UNVERIFIED,
                        "charge_unverified_attempts_exhausted",
                        now,
                    )
                self._transition(AutoRechargeState.STAND_UP_RETRY, now)
                self._standup_requested = False
            return RechargeCommand(reason="waiting_charge_confirmation")

        if self.state == AutoRechargeState.STAND_UP_RETRY:
            if not self._standup_requested:
                self._standup_requested = True
                return RechargeCommand(request_standup=True, reason="charge_retry_standup")
            return RechargeCommand(reason="waiting_standup_result")

        return RechargeCommand()

    def _visual_servo(
        self,
        now: float,
        stable: StableDockObservation | None,
        forward_corridor_safe: bool,
    ) -> RechargeCommand:
        if stable is None:
            if (
                self._last_observation is not None
                and self._last_observation.z_m <= self.config.recovery_z_max_m
            ):
                return self._begin_recovery(now, "near_field_marker_lost")
            if self._state_elapsed(now) > self.config.recovery_reacquire_grace_s:
                return self._fail(
                    AutoRechargeErrorCode.MARKER_POSE_UNSTABLE,
                    "far_field_marker_not_stable",
                    now,
                )
            return RechargeCommand(reason="far_field_marker_grace")

        observation = stable.observation
        if observation.min_corner_margin_px < self.config.min_corner_margin_px:
            if observation.z_m <= self.config.near_field_start_z_m:
                return self._begin_recovery(now, "near_field_marker_edge_clipped")
            return RechargeCommand(reason="far_field_marker_edge_clipped")
        if observation.z_m < self.config.critical_close_z_m:
            return self._begin_recovery(now, "critical_close_overshoot")
        if observation.z_m <= self.config.near_field_start_z_m and (
            abs(observation.bearing_rad) > self.config.final_bearing_hard_rad
            or stable.z_mad_m > self.config.near_stable_z_mad_m
        ):
            return self._begin_recovery(now, "near_field_not_aligned_or_unstable")
        if (
            self.config.final_z_min_m <= observation.z_m <= self.config.final_z_max_m
            and abs(observation.bearing_rad) <= self.config.final_bearing_hard_rad
        ):
            self._settled_since = now
            self._transition(AutoRechargeState.FINAL_SETTLE, now)
            return RechargeCommand(reason="entered_final_envelope")
        if abs(observation.bearing_rad) > self.config.final_bearing_soft_rad:
            duration = _clamp(
                abs(observation.bearing_rad)
                / self.config.estimated_yaw_rate_rad_s
                * self.config.pulse_yaw_fraction,
                self.config.yaw_pulse_min_s,
                self.config.max_yaw_pulse_s,
            )
            yaw = -self.config.yaw_sign * math.copysign(
                self.config.pulse_yaw_axis, observation.bearing_rad
            )
            return self._start_pulse(
                now,
                RechargeCommand(
                    yaw_rad_s=yaw,
                    pulse_duration_s=duration,
                    reason="center_bearing_yaw_pulse",
                ),
            )
        if observation.z_m > self.config.final_z_max_m:
            if not forward_corridor_safe:
                return self._fail(
                    AutoRechargeErrorCode.DOCK_TARGET_BLOCKED,
                    "visual_forward_corridor_blocked",
                    now,
                )
            if observation.z_m > 1.20:
                axis = self.config.coarse_forward_axis
                duration = self.config.coarse_forward_pulse_s
            elif observation.z_m > self.config.recovery_z_target_m:
                axis = self.config.medium_forward_axis
                duration = self.config.medium_forward_pulse_s
            else:
                axis = self.config.fine_forward_axis
                duration = self.config.fine_forward_pulse_s
            return self._start_pulse(
                now,
                RechargeCommand(
                    forward_mps=axis,
                    pulse_duration_s=duration,
                    reason="distance_forward_pulse",
                ),
            )
        return self._begin_recovery(now, "final_distance_overshoot")

    def _final_settle(
        self,
        now: float,
        stable: StableDockObservation | None,
    ) -> RechargeCommand:
        if stable is None:
            self._settled_since = None
            return self._begin_recovery(now, "final_pose_lost")
        observation = stable.observation
        inside = (
            self.config.final_z_min_m <= observation.z_m <= self.config.final_z_max_m
            and abs(observation.bearing_rad) <= self.config.final_bearing_hard_rad
            and stable.z_mad_m <= self.config.near_stable_z_mad_m
            and observation.min_corner_margin_px >= self.config.min_corner_margin_px
        )
        if not inside:
            self._settled_since = None
            if observation.z_m <= self.config.near_field_start_z_m:
                return self._begin_recovery(now, "final_pose_broke_near_field")
            self._transition(AutoRechargeState.VISUAL_SERVO, now)
            return RechargeCommand(reason="final_pose_broke")
        if self._settled_since is None:
            self._settled_since = now
        if now - self._settled_since >= self.config.final_stable_time_s:
            self._transition(AutoRechargeState.LIE_DOWN, now)
        return RechargeCommand(reason="holding_final_pose")

    def _begin_recovery(self, now: float, reason: str) -> RechargeCommand:
        self._recovery_attempt += 1
        if self._recovery_attempt > self.config.max_recovery_attempts:
            return self._fail(
                AutoRechargeErrorCode.NEAR_FIELD_REACQUIRE_FAILED,
                "recovery_attempts_exhausted",
                now,
            )
        last_z = (
            self._last_observation.z_m
            if self._last_observation is not None
            else self.config.near_field_start_z_m
        )
        self._recovery_goal_m = _clamp(
            self.config.recovery_z_target_m - last_z,
            self.config.recovery_min_backoff_m,
            self.config.recovery_max_single_backoff_m,
        )
        self._settled_since = None
        self._transition(AutoRechargeState.RECOVERY_STOP, now)
        return RechargeCommand(reason=reason)

    def _start_pulse(self, now: float, command: RechargeCommand) -> RechargeCommand:
        if command.forward_mps != 0.0 and command.yaw_rad_s != 0.0:
            raise ValueError("A recharge pulse may use only one motion axis")
        self._pulse_command = command
        self._pulse_until = now + command.pulse_duration_s
        self._await_image_after = None
        return command

    def _tick_pulse(
        self,
        now: float,
        image_received_at: float | None,
    ) -> RechargeCommand | None:
        if self._pulse_until is not None:
            if now < self._pulse_until:
                return self._pulse_command
            ended_at = self._pulse_until
            self._pulse_until = None
            self._pulse_command = RechargeCommand()
            self._await_image_after = ended_at
            return RechargeCommand(reason="pulse_finished_zero")
        if self._await_image_after is not None:
            if (
                now - self._await_image_after < self.config.zero_settle_s
                or image_received_at is None
                or image_received_at <= self._await_image_after
            ):
                return RechargeCommand(reason="waiting_fresh_image_after_pulse")
            self._await_image_after = None
        return None

    def _transition(self, state: AutoRechargeState, now: float) -> None:
        self.state = state
        self._state_started_at = now

    def _state_elapsed(self, now: float) -> float:
        return 0.0 if self._state_started_at is None else now - self._state_started_at

    def _fail(
        self,
        code: AutoRechargeErrorCode,
        message: str,
        now: float,
    ) -> RechargeCommand:
        if self.failure is None:
            elapsed = 0.0 if self._started_at is None else now - self._started_at
            self.failure = AutoRechargeFailure(
                code=code,
                state=self.state,
                message=message,
                elapsed_s=elapsed,
                last_observation=self._last_observation,
            )
        self._transition(AutoRechargeState.FAILED, now)
        self._pulse_until = None
        self._pulse_command = RechargeCommand()
        return RechargeCommand(reason=message)
