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

"""Go2 视觉回充纯函数状态机 (无 IO, 便于单测).

状态链: acquire → align_yaw → align_lateral → approach → settle → lie_down.
每 tick 最多输出一条 RechargeCommand; 图像过期或 lowstate 过期则 fail.

align_yaw 用脉冲转向: 固定 |rx| 下限 0.20, 时长按 |bearing|/0.21*0.85 估算,
避免持续最小角速度转过头. 丢码时先按偏差角等量反向转, 再 fail 交给外层整圈搜索.
"""

from __future__ import annotations

from dimos.robot.unitree.go2.recharge.config import RechargeConfig
from dimos.robot.unitree.go2.recharge.types import (
    DockError,
    MarkerObservation,
    RechargeCommand,
    RechargeErrorCode,
    RechargeState,
    TerminalFailure,
)


def _clamp(value: float, limit: float) -> float:
    return max(-limit, min(limit, value))


def _joystick_axis(value: float, *, max_limit: float, min_magnitude: float) -> float:
    """限幅并抬到 4G 摇杆硬下限 (低于下限设备不动, 见 jiangtao/run.md)."""
    scaled = _clamp(value, max_limit)
    if scaled == 0.0:
        return 0.0
    if abs(scaled) < min_magnitude:
        return min_magnitude if scaled > 0.0 else -min_magnitude
    return scaled


class ArucoRechargeController:
    """State machine that never commands non-zero motion from stale observations."""

    def __init__(self, config: RechargeConfig) -> None:
        self.config = config
        self.state = RechargeState.IDLE
        self.failure: TerminalFailure | None = None
        # These timestamps are monotonic host time, not robot clock. Keeping them in
        # the pure controller makes timeout tests deterministic and keeps IO outside.
        self._started_at: float | None = None
        self._state_started_at: float | None = None
        # ACQUIRE requires several distinct observed frames so a single false positive
        # never starts motion.
        self._stable_frames = 0
        self._last_counted_observation_at: float | None = None
        self._last_seen_at: float | None = None
        self._settled_since: float | None = None
        self._liedown_requested = False
        self._last_command = RechargeCommand()
        # Yaw bookkeeping is used only for pulse control and marker-loss undo.
        self._last_yaw_cmd = 0.0
        self._tick_at: float | None = None
        self._align_yaw_run_s = 0.0
        self._last_seen_yaw_rad = 0.0
        self._undo_budget_s = 0.0
        self._lost_reverse_started_at: float | None = None
        self._yaw_pulse_until: float | None = None
        self._yaw_pulse_obs_at: float | None = None

    @property
    def last_yaw_cmd(self) -> float:
        return self._last_yaw_cmd

    @property
    def undo_yaw_s(self) -> float:
        """丢码后应按上次偏差角等量反转的时长 (秒)."""
        return self._undo_budget_s

    @property
    def active(self) -> bool:
        """True while this attempt may still output motion or a lie-down request."""
        return self.state not in {
            RechargeState.IDLE,
            RechargeState.VISUAL_DOCKED,
            RechargeState.SUCCEEDED,
            RechargeState.FAILED,
            RechargeState.CANCELLED,
        }

    def start(self, now: float) -> None:
        """Start an attempt after navigation has reached the visual docking area."""
        if self.active:
            raise RuntimeError("Recharge task is already active")
        self.state = RechargeState.ACQUIRE
        self.failure = None
        self._started_at = now
        self._state_started_at = now
        self._stable_frames = 0
        self._last_counted_observation_at = None
        self._last_seen_at = None
        self._settled_since = None
        self._liedown_requested = False
        self._last_command = RechargeCommand()
        self._last_yaw_cmd = 0.0
        self._tick_at = None
        self._align_yaw_run_s = 0.0
        self._last_seen_yaw_rad = 0.0
        self._undo_budget_s = 0.0
        self._lost_reverse_started_at = None
        self._yaw_pulse_until = None
        self._yaw_pulse_obs_at = None

    def cancel(self, now: float) -> RechargeCommand:
        """Cancel immediately and preserve a terminal reason for the trace."""
        if self.active:
            self._fail(RechargeErrorCode.CANCELLED, "operator_cancel", now, None)
            self.state = RechargeState.CANCELLED
        return RechargeCommand()

    def notify_liedown_result(self, success: bool, now: float) -> None:
        """Advance only after the connection RPC reports its lie-down result."""
        if self.state != RechargeState.LIE_DOWN:
            return
        if not success:
            self._fail(RechargeErrorCode.LIE_DOWN_FAILED, "liedown_rpc_failed", now, None)
            return
        self._transition(RechargeState.VERIFY_CHARGE, now)

    def complete_visual_dock(self, now: float) -> None:
        """Finish a no-contact validation run without claiming charge success."""
        if self.state == RechargeState.LIE_DOWN:
            self._transition(RechargeState.VISUAL_DOCKED, now)

    def tick(
        self,
        now: float,
        observation: MarkerObservation | None,
        *,
        image_age_s: float,
        lowstate_age_s: float,
        charge_confirmed: bool | None = None,
    ) -> RechargeCommand:
        """Advance the controller once and return the only permitted command."""
        if not self.active:
            return RechargeCommand()
        # Safety gates come before every state-specific action. If camera or lowstate
        # freshness is not trustworthy, the controller latches FAILED and emits zero.
        if (
            self.config.total_timeout_s > 0.0
            and self._started_at is not None
            and now - self._started_at > self.config.total_timeout_s
        ):
            self._fail(RechargeErrorCode.TOTAL_TIMEOUT, "total_timeout", now, observation)
            return RechargeCommand()
        if lowstate_age_s > self.config.lowstate_max_age_s:
            self._fail(RechargeErrorCode.LOWSTATE_STALE, "lowstate_stale", now, observation)
            return RechargeCommand()
        if (
            self.state
            in {
                RechargeState.ACQUIRE,
                RechargeState.ALIGN_YAW,
                RechargeState.ALIGN_LATERAL,
                RechargeState.APPROACH,
                RechargeState.SETTLE,
            }
            and image_age_s > self.config.image_max_age_s
        ):
            self._fail(RechargeErrorCode.IMAGE_STALE, "image_stale", now, observation)
            return RechargeCommand()
        if observation is not None:
            if self._lost_reverse_started_at is not None:
                # 反转找回后重新累计本段转向.
                self._align_yaw_run_s = 0.0
            self._last_seen_at = observation.observed_at
            self._last_seen_yaw_rad = observation.yaw_rad
            self._lost_reverse_started_at = None

        if self.state == RechargeState.ACQUIRE:
            return self._emit(now, self._tick_acquire(now, observation))
        if self.state in {
            RechargeState.ALIGN_YAW,
            RechargeState.ALIGN_LATERAL,
            RechargeState.APPROACH,
            RechargeState.SETTLE,
        }:
            if observation is None:
                return self._emit(now, self._tick_marker_lost(now))
            return self._emit(now, self._tick_docking(now, observation))
        if self.state == RechargeState.LIE_DOWN:
            # Lie-down is a one-shot request. The module/script must call
            # notify_liedown_result() or complete_visual_dock() to advance.
            if self._liedown_requested:
                return RechargeCommand()
            self._liedown_requested = True
            return RechargeCommand(request_liedown=True)
        if self.state == RechargeState.VERIFY_CHARGE:
            if charge_confirmed is True:
                self._transition(RechargeState.SUCCEEDED, now)
            elif self._state_elapsed(now) > self.config.verify_charge_timeout_s:
                self._fail(
                    RechargeErrorCode.CHARGE_UNVERIFIED, "charge_not_confirmed", now, observation
                )
            return RechargeCommand()
        return RechargeCommand()

    def _estimate_undo_s(self) -> float:
        """按丢码前看到的偏差角等量反转; 没有有效角则退回「刚才实际转了多久」."""
        rate = max(1e-3, self.config.estimated_yaw_rate_rad_s)
        from_angle = abs(self._last_seen_yaw_rad) / rate
        from_cmd = self._align_yaw_run_s
        # 优先用看见的偏差角; 若几乎为正对则用实际已转时长.
        undo = from_angle if from_angle >= 0.25 else from_cmd
        # 不得超过刚才实际转过的量太多 (最多多 15%), 也不得超过半圈.
        if from_cmd > 0.0:
            undo = min(undo, from_cmd * 1.15)
        return max(0.3, min(undo, 3.1416 / rate))

    def _emit(self, now: float, command: RechargeCommand) -> RechargeCommand:
        """累计同向转角时长, 供丢码等量反转."""
        dt = 0.0 if self._tick_at is None else max(0.0, now - self._tick_at)
        self._tick_at = now
        self._last_command = command
        if command.yaw_rad_s != 0.0:
            if self._last_yaw_cmd != 0.0 and command.yaw_rad_s * self._last_yaw_cmd < 0.0:
                self._align_yaw_run_s = 0.0
            self._align_yaw_run_s += dt
            self._last_yaw_cmd = command.yaw_rad_s
        elif self.state == RechargeState.ALIGN_YAW and self._lost_reverse_started_at is None:
            # 对准态发零速: 本段转向结束, 保留 run_s 供可能的丢码反转.
            pass
        return command

    def _tick_marker_lost(self, now: float) -> RechargeCommand:
        """丢码第一层: 按丢码前偏差角等量反向转; 转完仍不见再 fail 交给外层整圈搜索."""
        if self._last_seen_at is None:
            self._fail(RechargeErrorCode.MARKER_LOST, "marker_lost", now, None)
            return RechargeCommand()
        if self._lost_reverse_started_at is None:
            self._lost_reverse_started_at = now
            self._undo_budget_s = (
                self._estimate_undo_s() if self.state == RechargeState.ALIGN_YAW else 0.0
            )
        reverse_budget_s = self._undo_budget_s if self.state == RechargeState.ALIGN_YAW else 0.0
        lost_for = now - self._lost_reverse_started_at
        if lost_for > reverse_budget_s + self.config.marker_lost_abort_s:
            self._fail(RechargeErrorCode.MARKER_LOST, "marker_lost", now, None)
            return RechargeCommand()
        if self.state == RechargeState.ALIGN_YAW and lost_for <= reverse_budget_s:
            if self._last_yaw_cmd == 0.0:
                return RechargeCommand()
            reverse_sign = -1.0 if self._last_yaw_cmd > 0.0 else 1.0
            yaw = _joystick_axis(
                reverse_sign * self.config.min_yaw_rad_s,
                max_limit=self.config.max_yaw_rad_s,
                min_magnitude=self.config.min_yaw_rad_s,
            )
            return RechargeCommand(yaw_rad_s=yaw)
        return RechargeCommand()

    def _yaw_pulse_budget_s(self, yaw_error_rad: float) -> float:
        """按当前偏差角估算脉冲时长, 用最小角速度转这么多就够了."""
        rate = max(1e-3, self.config.estimated_yaw_rate_rad_s)
        needed = abs(yaw_error_rad) / rate
        return max(self.config.yaw_pulse_min_s, needed * self.config.yaw_pulse_fraction)

    def _yaw_command_for_error(self, yaw_error_rad: float) -> RechargeCommand:
        """脉冲转向: 固定用最小 |rx|=0.20, 时长由 _yaw_pulse_budget_s 控制.

        yaw_sign=1.0 时 bearing>0 (码在右) → angular.z 为负 (顺时针).
        实测 yaw_sign=-1.0 时 bearing 14° 会越转越大到 43° 后丢码.
        """
        if yaw_error_rad == 0.0:
            return RechargeCommand()
        raw = -self.config.yaw_sign * self.config.yaw_gain * yaw_error_rad
        sign = 1.0 if raw > 0.0 else -1.0
        yaw = sign * self.config.min_yaw_rad_s
        return RechargeCommand(yaw_rad_s=yaw)

    def _tick_align_yaw(
        self, now: float, observation: MarkerObservation, error: DockError
    ) -> RechargeCommand:
        # |bearing| ≤ align_yaw_exit_rad (≈14°) 进入 lateral.
        if abs(error.yaw_rad) <= self.config.align_yaw_exit_rad:
            self._yaw_pulse_until = None
            self._transition(RechargeState.ALIGN_LATERAL, now)
            return RechargeCommand()
        if self._yaw_pulse_until is not None and now < self._yaw_pulse_until:
            # 脉冲进行中: 仍用当前 error 算方向 (已知会在大 lateral 时 flip, 待锁符号).
            return self._yaw_command_for_error(error.yaw_rad)
        if self._yaw_pulse_until is not None and now >= self._yaw_pulse_until:
            self._yaw_pulse_until = None
            # 脉冲结束先停一帧; 同一观测帧不立刻开下一脉冲.
            if observation.observed_at == self._yaw_pulse_obs_at:
                return RechargeCommand()
        budget = self._yaw_pulse_budget_s(error.yaw_rad)
        self._yaw_pulse_until = now + budget
        self._yaw_pulse_obs_at = observation.observed_at
        return self._yaw_command_for_error(error.yaw_rad)

    def _tick_acquire(self, now: float, observation: MarkerObservation | None) -> RechargeCommand:
        """Find the marker first; if absent, rotate in place at the calibrated yaw floor."""
        if observation is None:
            self._stable_frames = 0
            if self._state_elapsed(now) > self.config.acquire_timeout_s:
                self._fail(RechargeErrorCode.MARKER_NOT_FOUND, "acquire_timeout", now, None)
                return RechargeCommand()
            return RechargeCommand(yaw_rad_s=self.config.acquire_search_yaw_rad_s)
        if self._last_counted_observation_at != observation.observed_at:
            self._stable_frames += 1
            self._last_counted_observation_at = observation.observed_at
        if self._stable_frames >= self.config.min_stable_frames:
            self._transition(RechargeState.ALIGN_YAW, now)
        return RechargeCommand()

    def _tick_docking(self, now: float, observation: MarkerObservation) -> RechargeCommand:
        """Run the closed-loop docking phases once a usable marker pose exists."""
        error = self._error(observation)
        state_timeout = self._state_timeout()
        if state_timeout > 0.0 and self._state_elapsed(now) > state_timeout:
            self._fail(RechargeErrorCode.STATE_TIMEOUT, "state_timeout", now, observation)
            return RechargeCommand()
        if self.state == RechargeState.ALIGN_YAW:
            return self._tick_align_yaw(now, observation, error)
        if self.state == RechargeState.ALIGN_LATERAL:
            if abs(error.lateral_m) <= self.config.align_lateral_exit_m:
                self._transition(RechargeState.APPROACH, now)
                return RechargeCommand()
            # Side-step only after yaw is roughly acceptable. We do not mix lateral
            # and forward motion in this first version.
            lateral = _joystick_axis(
                -self.config.lateral_sign * self.config.lateral_gain * error.lateral_m,
                max_limit=self.config.max_lateral_mps,
                min_magnitude=self.config.min_lateral_mps,
            )
            return RechargeCommand(lateral_mps=lateral)
        if self.state == RechargeState.APPROACH:
            if abs(error.forward_m) <= self.config.approach_forward_exit_m:
                self._transition(RechargeState.SETTLE, now)
                return RechargeCommand()
            # While moving forward, keep the tag inside a coarse visibility corridor.
            # If bearing or lateral error grows too large, stop first and go back to
            # the corresponding alignment phase.
            if abs(error.yaw_rad) > self.config.align_yaw_exit_rad:
                self._yaw_pulse_until = None
                self._transition(RechargeState.ALIGN_YAW, now)
                return RechargeCommand()
            if abs(error.lateral_m) > self.config.align_lateral_exit_m:
                self._transition(RechargeState.ALIGN_LATERAL, now)
                return RechargeCommand()
            forward = _joystick_axis(
                self.config.max_forward_mps,
                max_limit=self.config.max_forward_mps,
                min_magnitude=self.config.min_forward_mps,
            )
            return RechargeCommand(forward_mps=forward)
        if self.state == RechargeState.SETTLE:
            # SETTLE is the only state allowed to request lie-down. It requires a
            # stable near-target pose for settle_time_s, but the yaw tolerance is
            # deliberately looser than align_yaw_exit_rad because the dock pad is wide.
            settled = (
                abs(error.forward_m) <= self.config.settle_forward_m
                and abs(error.lateral_m) <= self.config.settle_lateral_m
                and abs(error.yaw_rad) <= self.config.settle_yaw_rad
            )
            if not settled:
                self._settled_since = None
                if abs(error.forward_m) > self.config.settle_forward_m:
                    self._transition(RechargeState.APPROACH, now)
                elif abs(error.yaw_rad) > self.config.align_yaw_exit_rad:
                    self._yaw_pulse_until = None
                    self._transition(RechargeState.ALIGN_YAW, now)
                elif abs(error.lateral_m) > self.config.settle_lateral_m:
                    self._transition(RechargeState.ALIGN_LATERAL, now)
                return RechargeCommand()
            if self._settled_since is None:
                self._settled_since = now
            if now - self._settled_since >= self.config.settle_time_s:
                self._transition(RechargeState.LIE_DOWN, now)
            return RechargeCommand()
        return RechargeCommand()

    def _error(self, observation: MarkerObservation) -> DockError:
        # forward: z 比目标远则为正 (还需靠近); lateral/yaw 直接取相机系观测.
        return DockError(
            forward_m=observation.z_m - self.config.target_camera_marker_distance_m,
            lateral_m=observation.x_m,
            yaw_rad=observation.yaw_rad,
        )

    def _state_timeout(self) -> float:
        """Return the timeout for the current alignment/approach family."""
        if self.state in {RechargeState.ALIGN_YAW, RechargeState.ALIGN_LATERAL}:
            return self.config.align_timeout_s
        return self.config.approach_timeout_s

    def _state_elapsed(self, now: float) -> float:
        """Seconds elapsed since the current state began."""
        return 0.0 if self._state_started_at is None else now - self._state_started_at

    def _transition(self, state: RechargeState, now: float) -> None:
        """Switch state and reset state-local pulse timers where needed."""
        if state == RechargeState.ALIGN_YAW:
            self._yaw_pulse_until = None
            self._yaw_pulse_obs_at = None
        self.state = state
        self._state_started_at = now

    def _fail(
        self,
        code: RechargeErrorCode,
        message: str,
        now: float,
        observation: MarkerObservation | None,
    ) -> None:
        """Latch the first terminal failure, including the last known docking error."""
        if self.failure is not None:
            return
        error = self._error(observation) if observation is not None else None
        elapsed = 0.0 if self._started_at is None else now - self._started_at
        self.failure = TerminalFailure(code, self.state, message, elapsed, error)
        self._transition(RechargeState.FAILED, now)
