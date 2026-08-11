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

"""Navigation-integrated local 4G WebRTC automatic recharge module."""

from __future__ import annotations

from dataclasses import replace
import math
import threading
import time
from typing import Any, Literal
import uuid

from dimos_lcm.std_msgs import Bool, String  # type: ignore[import-untyped]
from reactivex.disposable import Disposable

from dimos.agents.annotation import skill
from dimos.core.core import rpc
from dimos.core.module import Module, ModuleConfig
from dimos.core.stream import In, Out
from dimos.msgs.geometry_msgs.PointStamped import PointStamped
from dimos.msgs.geometry_msgs.PoseStamped import PoseStamped
from dimos.msgs.geometry_msgs.Quaternion import Quaternion
from dimos.msgs.geometry_msgs.Twist import Twist
from dimos.msgs.geometry_msgs.Vector3 import Vector3
from dimos.msgs.nav_msgs.OccupancyGrid import OccupancyGrid
from dimos.msgs.sensor_msgs.Image import Image
from dimos.navigation.diagnostics.sink import TraceSink, isolate_trace_failure
from dimos.navigation.replanning_a_star.module_spec import ReplanningAStarPlannerSpec
from dimos.robot.unitree.go2.connection_spec import GO2ConnectionSpec
from dimos.robot.unitree.go2.recharge.charge_verify import (
    ChargeCurrentRule,
    ChargeVerifier,
    calibrated_go2_4g_charge_rules,
)
from dimos.robot.unitree.go2.recharge.config import RechargeConfig
from dimos.robot.unitree.go2.recharge.dock_controller import DockController
from dimos.robot.unitree.go2.recharge.dock_geometry import (
    build_dock_target,
    direct_servo_reachable,
    distance_between,
)
from dimos.robot.unitree.go2.recharge.reachability import (
    corridor_is_clear,
    validate_dock_target,
)
from dimos.robot.unitree.go2.recharge.stability import PoseStabilityWindow
from dimos.robot.unitree.go2.recharge.types import (
    AutoRechargeState,
    DockObservation,
    DockTarget,
    PlanarPose,
    RechargeCommand,
    StableDockObservation,
)
from dimos.robot.unitree.go2.recharge.vision import ArucoRechargeVision


class AutoRechargeModuleConfig(ModuleConfig):
    """Runtime IO and safety switches around the pure docking controller."""

    control_hz: float = 10.0
    auto_takeover: bool = True
    allow_liedown: bool = False
    velocity_api: bool = False
    charge_current_threshold: float | None = None
    charge_current_direction: Literal["below", "above"] = "below"
    charge_stable_duration_s: float = 4.0


class AutoRechargeModule(Module):
    """Monitor navigation, stage on the dock centreline, then visually dock."""

    dedicated_worker = True
    config: AutoRechargeModuleConfig

    color_image: In[Image]
    odom: In[PoseStamped]
    global_costmap: In[OccupancyGrid]
    recharge_task_granted: In[Bool]
    recharge_servo_granted: In[Bool]
    recharge_cancel: In[Bool]

    recharge_task_active: Out[Bool]
    recharge_servo_active: Out[Bool]
    recharge_staging_goal: Out[PointStamped]
    recharge_cmd_vel: Out[Twist]
    recharge_state: Out[String]

    _go2: GO2ConnectionSpec
    _planner: ReplanningAStarPlannerSpec

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._recharge_config = RechargeConfig()
        self._vision = ArucoRechargeVision(self._recharge_config)
        self._stability = PoseStabilityWindow(self._recharge_config)
        self._controller = DockController(self._recharge_config)
        self._charge_verifier = ChargeVerifier(
            calibrated_go2_4g_charge_rules()
            if self.config.charge_current_threshold is None
            else ChargeCurrentRule(
                threshold=self.config.charge_current_threshold,
                direction=self.config.charge_current_direction,
                stable_duration_s=self.config.charge_stable_duration_s,
            )
        )
        self._lock = threading.RLock()
        self._loop_stop = threading.Event()
        self._loop_thread: threading.Thread | None = None
        self._state = AutoRechargeState.MONITOR
        self._state_started_at = time.monotonic()
        self._task_id: str | None = None
        self._failure_code: str | None = None
        self._auto_enabled = self.config.auto_takeover

        self._latest_observation: DockObservation | None = None
        self._stable_observation: StableDockObservation | None = None
        self._latest_image_received_at: float | None = None
        self._robot_pose: PlanarPose | None = None
        self._odom_received_at: float | None = None
        self._odom_linear_speed_mps = float("inf")
        self._previous_odom: PlanarPose | None = None
        self._costmap: OccupancyGrid | None = None
        self._costmap_received_at: float | None = None
        self._dock_target: DockTarget | None = None
        self._task_granted = False
        self._servo_granted = False
        self._task_requested = False
        self._servo_requested = False
        self._staging_goal_sent = False
        self._stopped_since: float | None = None
        self._recovery_origin: PlanarPose | None = None
        self._last_controller_state = AutoRechargeState.MONITOR
        self._last_published_state: AutoRechargeState | None = None
        self._last_command = RechargeCommand()
        self._trace = TraceSink("recharge", config=self.config.g)

    @rpc
    def start(self) -> None:
        super().start()
        self.register_disposable(Disposable(self.color_image.subscribe(self._on_image)))
        self.register_disposable(Disposable(self.odom.subscribe(self._on_odom)))
        self.register_disposable(Disposable(self.global_costmap.subscribe(self._on_costmap)))
        self.register_disposable(
            Disposable(self.recharge_task_granted.subscribe(self._on_task_granted))
        )
        self.register_disposable(
            Disposable(self.recharge_servo_granted.subscribe(self._on_servo_granted))
        )
        self.register_disposable(Disposable(self.recharge_cancel.subscribe(self._on_cancel)))
        self._loop_stop.clear()
        self._loop_thread = threading.Thread(target=self._control_loop, daemon=True)
        self._loop_thread.start()
        self._publish_state()

    @rpc
    def stop(self) -> None:
        self._loop_stop.set()
        thread = self._loop_thread
        if thread is not None:
            thread.join(timeout=1.0)
        self._publish_command(RechargeCommand(reason="module_stop"))
        self._release_ownership()
        self._trace.close()
        super().stop()

    @skill
    def start_recharge(self) -> str:
        """Enable automatic recharge monitoring and report the current state."""
        with self._lock:
            self._auto_enabled = True
            state = self._state.value
            reset_terminal = self._state in {
                AutoRechargeState.FAILED,
                AutoRechargeState.CANCELLED,
                AutoRechargeState.SUCCEEDED,
            }
        if reset_terminal:
            self._transition(AutoRechargeState.MONITOR, "operator_restart")
            state = AutoRechargeState.MONITOR.value
        return f"Automatic recharge enabled; state={state}"

    @skill
    def cancel_recharge(self) -> str:
        """Cancel automatic recharge, publish zero velocity, and release navigation ownership."""
        self._cancel_task("operator_cancelled")
        return "Automatic recharge cancelled"

    @skill
    def leave_charger(self) -> str:
        """Release charging-hold ownership without commanding the robot to move."""
        with self._lock:
            self._auto_enabled = False
        self._publish_command(RechargeCommand(reason="leave_charger"))
        self._release_ownership()
        self._transition(AutoRechargeState.MONITOR, "leave_charger")
        return "Charging hold released; no movement command was sent"

    @skill
    def recharge_status(self) -> str:
        """Return automatic recharge state, target, last tag pose, and failure reason."""
        with self._lock:
            state = self._state.value
            observation = self._latest_observation
            target = self._dock_target
            failure = self._failure_code
        fields = [f"state={state}", f"failure={failure}"]
        if observation is not None:
            fields.append(
                f"tag=(x={observation.x_m:.3f},z={observation.z_m:.3f},"
                f"bearing={math.degrees(observation.bearing_rad):.1f}deg)"
            )
        if target is not None:
            fields.append(f"staging=({target.staging_pose.x:.3f},{target.staging_pose.y:.3f})")
        return "; ".join(fields)

    def _on_image(self, image: Image) -> None:
        received_at = time.monotonic()
        observation = self._vision.observe(image)
        if observation is not None:
            observation = replace(observation, observed_at=received_at)
        with self._lock:
            stable = self._stability.push(observation)
            self._latest_image_received_at = received_at
            self._latest_observation = observation
            self._stable_observation = stable

    def _on_odom(self, msg: PoseStamped) -> None:
        received_at = time.monotonic()
        pose = PlanarPose(msg.x, msg.y, msg.yaw, msg.frame_id, received_at)
        with self._lock:
            previous = self._previous_odom
            if previous is not None:
                elapsed = max(1e-6, received_at - previous.observed_at)
                self._odom_linear_speed_mps = distance_between(previous, pose) / elapsed
            self._previous_odom = pose
            self._robot_pose = pose
            self._odom_received_at = received_at

    def _on_costmap(self, msg: OccupancyGrid) -> None:
        with self._lock:
            self._costmap = msg
            self._costmap_received_at = time.monotonic()

    def _on_task_granted(self, msg: Bool) -> None:
        with self._lock:
            self._task_granted = bool(msg.data)

    def _on_servo_granted(self, msg: Bool) -> None:
        with self._lock:
            self._servo_granted = bool(msg.data)

    def _on_cancel(self, msg: Bool) -> None:
        if bool(msg.data):
            self._cancel_task("movement_manager_cancel")

    def _control_loop(self) -> None:
        interval_s = 1.0 / max(1.0, self.config.control_hz)
        while not self._loop_stop.wait(interval_s):
            try:
                self._control_tick(time.monotonic())
            except Exception as exc:
                self._trace_event("recharge_control_exception", {"error": repr(exc)})
                self._finish_failure("control_loop_exception")

    def _control_tick(self, now: float) -> None:
        with self._lock:
            state = self._state
            auto_enabled = self._auto_enabled
            stable = self._stable_observation
            robot_pose = self._robot_pose
            costmap = self._costmap
            task_granted = self._task_granted
            servo_granted = self._servo_granted

        if state == AutoRechargeState.MONITOR:
            if auto_enabled and stable is not None:
                self._transition(AutoRechargeState.VALIDATE_DOCK, "stable_tag_candidate")
            return

        if state == AutoRechargeState.VALIDATE_DOCK:
            if stable is None or robot_pose is None or costmap is None:
                self._transition(AutoRechargeState.MONITOR, "candidate_inputs_missing")
                return
            if self._age(now, self._odom_received_at) > self._recharge_config.odom_max_age_s:
                self._transition(AutoRechargeState.MONITOR, "odom_stale")
                return
            if self._age(now, self._costmap_received_at) > self._recharge_config.costmap_max_age_s:
                self._transition(AutoRechargeState.MONITOR, "costmap_stale")
                return
            target = build_dock_target(stable.observation, robot_pose, self._recharge_config)
            if target is None:
                self._transition(AutoRechargeState.MONITOR, "dock_geometry_invalid")
                return
            result = validate_dock_target(
                stable.observation,
                robot_pose,
                target,
                costmap,
                self._recharge_config,
            )
            if not result.accepted:
                self._trace_event(
                    "dock_candidate_rejected",
                    {"reason": result.reason, "z_m": stable.observation.z_m},
                )
                self._transition(AutoRechargeState.MONITOR, result.reason or "unreachable")
                return
            with self._lock:
                self._dock_target = target
                self._task_id = uuid.uuid4().hex
                self._failure_code = None
            self._transition(AutoRechargeState.CLAIM_TASK, "dock_target_reachable")
            return

        if state == AutoRechargeState.CLAIM_TASK:
            if not self._task_requested:
                self._task_requested = True
                self.recharge_task_active.publish(Bool(data=True))
                return
            if not task_granted:
                if self._state_elapsed(now) > self._recharge_config.task_ownership_timeout_s:
                    self._finish_failure("task_ownership_timeout")
                return
            assert self._dock_target is not None
            assert robot_pose is not None
            if stable is not None and direct_servo_reachable(
                stable.observation,
                robot_pose,
                self._dock_target,
                self._recharge_config,
            ):
                self._transition(AutoRechargeState.ACQUIRE_FOR_SERVO, "direct_servo_reachable")
            else:
                self._transition(AutoRechargeState.STAGING_NAV, "navigate_to_staging")
            return

        if state == AutoRechargeState.STAGING_NAV:
            if robot_pose is None or self._dock_target is None:
                self._finish_failure("staging_pose_missing")
                return
            if not self._staging_goal_sent:
                staging_pose = self._dock_target.staging_pose
                planner_goal = PoseStamped(
                    ts=time.time(),
                    frame_id=staging_pose.frame_id,
                    position=Vector3(staging_pose.x, staging_pose.y, 0.0),
                    orientation=Quaternion.from_euler(Vector3(0.0, 0.0, staging_pose.yaw)),
                )
                if not self._planner.set_goal(planner_goal):
                    self._finish_failure("staging_goal_rejected")
                    return
                # Retain the stream publication for diagnostics and older navigation
                # consumers; ReplanningAStarPlanner is driven by the typed RPC above.
                self.recharge_staging_goal.publish(
                    PointStamped(
                        ts=time.time(),
                        frame_id=staging_pose.frame_id,
                        x=staging_pose.x,
                        y=staging_pose.y,
                        z=0.0,
                    )
                )
                self._staging_goal_sent = True
                return
            distance = distance_between(robot_pose, self._dock_target.staging_pose)
            if (
                costmap is None
                or self._age(now, self._costmap_received_at)
                > self._recharge_config.costmap_max_age_s
                or not corridor_is_clear(
                    costmap,
                    robot_pose,
                    self._dock_target.staging_pose,
                    self._recharge_config,
                )
            ):
                self._finish_failure("staging_path_blocked")
                return
            stopped = self._odom_linear_speed_mps <= self._recharge_config.stopped_linear_speed_mps
            if distance <= self._recharge_config.staging_goal_tolerance_m and stopped:
                if self._stopped_since is None:
                    self._stopped_since = now
                elif now - self._stopped_since >= self._recharge_config.stopped_hold_s:
                    self._transition(AutoRechargeState.ACQUIRE_FOR_SERVO, "staging_arrived")
            else:
                self._stopped_since = None
            if self._state_elapsed(now) > self._recharge_config.staging_timeout_s:
                self._finish_failure("staging_timeout")
            return

        if state == AutoRechargeState.ACQUIRE_FOR_SERVO:
            if stable is not None:
                self._transition(AutoRechargeState.CLAIM_SERVO, "tag_stable_for_servo")
            elif self._state_elapsed(now) > self._recharge_config.acquire_for_servo_timeout_s:
                self._finish_failure("marker_not_found_at_stage")
            return

        if state == AutoRechargeState.CLAIM_SERVO:
            if not self._servo_requested:
                self._servo_requested = True
                self.recharge_servo_active.publish(Bool(data=True))
                return
            if not servo_granted:
                if self._state_elapsed(now) > self._recharge_config.servo_ownership_timeout_s:
                    self._finish_failure("servo_ownership_timeout")
                return
            self._controller.start_servo(now)
            self._last_controller_state = self._controller.state
            self._transition(self._controller.state, "servo_granted")
            return

        if state in {
            AutoRechargeState.STOP_AND_OBSERVE,
            AutoRechargeState.VISUAL_SERVO,
            AutoRechargeState.RECOVERY_STOP,
            AutoRechargeState.RECOVERY_BACKOFF,
            AutoRechargeState.RECOVERY_REACQUIRE,
            AutoRechargeState.FINAL_SETTLE,
            AutoRechargeState.LIE_DOWN,
            AutoRechargeState.VERIFY_CHARGE,
            AutoRechargeState.STAND_UP_RETRY,
        }:
            self._tick_controller(now, stable, robot_pose, costmap)

    def _tick_controller(
        self,
        now: float,
        stable: StableDockObservation | None,
        robot_pose: PlanarPose | None,
        costmap: OccupancyGrid | None,
    ) -> None:
        lowstate_age, bms_current = self._poll_lowstate()
        previous_controller_state = self._controller.state
        recovery_distance = (
            0.0
            if robot_pose is None or self._recovery_origin is None
            else distance_between(robot_pose, self._recovery_origin)
        )
        rear_safe = self._rear_corridor_safe(robot_pose, costmap)
        forward_safe = self._forward_corridor_safe(robot_pose, costmap)
        command = self._controller.tick(
            now,
            stable,
            image_received_at=self._latest_image_received_at,
            image_age_s=self._age(now, self._latest_image_received_at),
            odom_age_s=self._age(now, self._odom_received_at),
            lowstate_age_s=lowstate_age,
            recovery_distance_m=recovery_distance,
            rear_corridor_safe=rear_safe,
            forward_corridor_safe=forward_safe,
            charge_confirmed=self._charge_verifier.observe_current(bms_current, now),
        )
        if (
            self._controller.state == AutoRechargeState.RECOVERY_STOP
            and previous_controller_state != AutoRechargeState.RECOVERY_STOP
        ):
            self._recovery_origin = robot_pose
        self._publish_command(command)
        if command.request_liedown:
            if self.config.allow_liedown:
                try:
                    success = self._go2.liedown()
                except Exception:
                    success = False
                self._controller.notify_liedown_result(success, now)
            else:
                self._controller.complete_visual_validation(now)
        if command.request_standup:
            try:
                success = self._go2.standup() and self._go2.balance_stand()
            except Exception:
                success = False
            self._controller.notify_standup_result(success, now)

        controller_state = self._controller.state
        self._last_controller_state = controller_state
        if controller_state != self._state:
            self._transition(controller_state, command.reason or "controller_transition")
        if controller_state == AutoRechargeState.CHARGING_HOLD:
            self.recharge_servo_active.publish(Bool(data=False))
            self._servo_requested = False
            self._trace_event("recharge_success", {"task_id": self._task_id})
        elif controller_state == AutoRechargeState.SUCCEEDED:
            self.recharge_servo_active.publish(Bool(data=False))
            self._release_ownership()
        elif controller_state in {AutoRechargeState.FAILED, AutoRechargeState.CANCELLED}:
            failure = self._controller.failure
            self._finish_failure(
                "controller_failed" if failure is None else failure.code.value,
                keep_state=True,
            )

    def _rear_corridor_safe(
        self,
        robot_pose: PlanarPose | None,
        costmap: OccupancyGrid | None,
    ) -> bool:
        if robot_pose is None or costmap is None or robot_pose.frame_id != costmap.frame_id:
            return False
        distance = max(
            self._controller.recovery_goal_m, self._recharge_config.recovery_min_backoff_m
        )
        end = PlanarPose(
            robot_pose.x - math.cos(robot_pose.yaw) * distance,
            robot_pose.y - math.sin(robot_pose.yaw) * distance,
            robot_pose.yaw,
            robot_pose.frame_id,
            robot_pose.observed_at,
        )
        return corridor_is_clear(costmap, robot_pose, end, self._recharge_config)

    def _forward_corridor_safe(
        self,
        robot_pose: PlanarPose | None,
        costmap: OccupancyGrid | None,
    ) -> bool:
        if (
            robot_pose is None
            or costmap is None
            or self._dock_target is None
            or robot_pose.frame_id != costmap.frame_id
        ):
            return False
        return corridor_is_clear(
            costmap,
            robot_pose,
            self._dock_target.final_pose,
            self._recharge_config,
        )

    def _poll_lowstate(self) -> tuple[float, float | None]:
        try:
            return self._go2.lowstate_age_s(), self._go2.bms_current()
        except Exception:
            return float("inf"), None

    def _publish_command(self, command: RechargeCommand) -> None:
        self._last_command = command
        self.recharge_cmd_vel.publish(
            Twist(
                Vector3(command.forward_mps, 0.0, 0.0),
                Vector3(0.0, 0.0, command.yaw_rad_s),
            )
        )
        self._trace_event(
            "recharge_cmd",
            {
                "task_id": self._task_id,
                "state": self._state.value,
                "forward_axis": command.forward_mps,
                "lateral_axis": 0.0,
                "yaw_axis": command.yaw_rad_s,
                "pulse_duration_s": command.pulse_duration_s,
                "reason": command.reason,
                "recovery_goal_m": self._controller.recovery_goal_m,
            },
        )

    def _cancel_task(self, reason: str) -> None:
        now = time.monotonic()
        with self._lock:
            self._auto_enabled = False
            self._controller.cancel(now)
        self._publish_command(RechargeCommand(reason=reason))
        self._release_ownership()
        self._transition(AutoRechargeState.CANCELLED, reason)

    def _finish_failure(self, code: str, *, keep_state: bool = False) -> None:
        with self._lock:
            self._failure_code = code
        self._publish_command(RechargeCommand(reason=code))
        self._release_ownership()
        if not keep_state:
            self._transition(AutoRechargeState.FAILED, code)
        self._trace_event("recharge_failed", {"task_id": self._task_id, "failure_code": code})

    def _release_ownership(self) -> None:
        try:
            self._planner.cancel_goal()
        except Exception:
            pass
        self.recharge_servo_active.publish(Bool(data=False))
        self.recharge_task_active.publish(Bool(data=False))
        with self._lock:
            self._servo_requested = False
            self._task_requested = False
            self._servo_granted = False
            self._task_granted = False

    def _transition(self, state: AutoRechargeState, reason: str) -> None:
        with self._lock:
            previous = self._state
            if state == previous:
                return
            self._state = state
            self._state_started_at = time.monotonic()
            if state == AutoRechargeState.MONITOR:
                self._dock_target = None
                self._task_id = None
                self._staging_goal_sent = False
                self._stopped_since = None
                self._stability.clear()
            elif state == AutoRechargeState.STAGING_NAV:
                self._staging_goal_sent = False
                self._stopped_since = None
        self._trace_event(
            "recharge_state_transition",
            {
                "task_id": self._task_id,
                "from_state": previous.value,
                "to_state": state.value,
                "reason": reason,
            },
        )
        self._publish_state()

    def _publish_state(self) -> None:
        if self._last_published_state == self._state:
            return
        self._last_published_state = self._state
        self.recharge_state.publish(String(data=self._state.value))

    def _state_elapsed(self, now: float) -> float:
        return max(0.0, now - self._state_started_at)

    def _trace_event(self, event: str, fields: dict[str, object]) -> None:
        if not self._trace.accepts("full"):
            return
        try:
            self._trace.record(event, fields, estimated_bytes=1536)
        except Exception as exc:
            isolate_trace_failure(self._trace, exc)

    @staticmethod
    def _age(now: float, received_at: float | None) -> float:
        return float("inf") if received_at is None else max(0.0, now - received_at)
