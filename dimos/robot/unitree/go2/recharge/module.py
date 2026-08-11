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

"""DimOS 模块: 本机 4G WebRTC 视觉回充闭环.

订阅 color_image, 经 ArucoRechargeController 算 cmd, 发布 recharge_cmd_vel
给 MovementManager. 不直连 WebRTC 发运动 (与 demo 脚本不同, demo 直连 conn.move).

真机验证目前以 ``jiangtao/scripts/demo_go2_4g_aruco_recharge.py`` 为主;
本模块供 blueprint ``unitree-go2-aruco-recharge*`` 集成用.
"""

from __future__ import annotations

from dataclasses import replace
import threading
import time
from typing import Any, Literal
import uuid

from dimos_lcm.std_msgs import Bool  # type: ignore[import-untyped]
from reactivex.disposable import Disposable

from dimos.agents.annotation import skill
from dimos.core.core import rpc
from dimos.core.module import Module, ModuleConfig
from dimos.core.stream import In, Out
from dimos.msgs.geometry_msgs.Twist import Twist
from dimos.msgs.geometry_msgs.Vector3 import Vector3
from dimos.msgs.sensor_msgs.Image import Image
from dimos.navigation.diagnostics.sink import TraceSink, isolate_trace_failure
from dimos.robot.unitree.go2.connection_spec import GO2ConnectionSpec
from dimos.robot.unitree.go2.recharge.charge_verify import (
    ChargeCurrentRule,
    ChargeVerifier,
    calibrated_go2_4g_charge_rules,
)
from dimos.robot.unitree.go2.recharge.config import RechargeConfig
from dimos.robot.unitree.go2.recharge.controller import ArucoRechargeController
from dimos.robot.unitree.go2.recharge.types import MarkerObservation, RechargeCommand, RechargeState
from dimos.robot.unitree.go2.recharge.vision import ArucoRechargeVision


class ArucoRechargeModuleConfig(ModuleConfig):
    """Runtime configuration for the local control-loop wrapper."""

    control_hz: float = 10.0
    # Field default is deliberately off; scripts may pass --allow-liedown once a
    # human has confirmed the dog is in the visual docking envelope.
    allow_liedown: bool = False
    # False means commands flow as WirelessController-style joystick ratios through
    # the existing Go2 connection path. True is reserved for a future velocity API.
    velocity_api: bool = False
    # If unset, use the measured two-band Go2 4G current rules from charge_verify.py.
    charge_current_threshold: float | None = None
    charge_current_direction: Literal["below", "above"] = "below"
    charge_stable_duration_s: float = 10.0


class ArucoRechargeModule(Module):
    """Dock a Go2 using the front video stream, then request a controlled lie-down.

    The module runs entirely on the host.  It accepts the existing Remote/4G
    WebRTC image stream and polls the connected Go2 module for low-state freshness
    and BMS current. It publishes a dedicated velocity input
    to ``MovementManager``; it never starts a second direct motion path.
    """

    dedicated_worker = True
    config: ArucoRechargeModuleConfig

    color_image: In[Image]
    recharge_cancel: In[Bool]
    recharge_cmd_vel: Out[Twist]
    recharge_active: Out[Bool]

    _go2: GO2ConnectionSpec

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._lock = threading.Lock()
        # The pure controller owns all docking decisions; the module owns IO, streams,
        # traces, and physical RPC calls.
        self._controller = ArucoRechargeController(RechargeConfig())
        self._vision = ArucoRechargeVision(self._controller.config)
        self._charge_verifier = ChargeVerifier(
            calibrated_go2_4g_charge_rules()
            if self.config.charge_current_threshold is None
            else ChargeCurrentRule(
                threshold=self.config.charge_current_threshold,
                direction=self.config.charge_current_direction,
                stable_duration_s=self.config.charge_stable_duration_s,
            )
        )
        self._latest_observation: MarkerObservation | None = None
        self._latest_observation_received_at: float | None = None
        self._latest_image_received_at: float | None = None
        self._task_id: str | None = None
        self._loop_stop = threading.Event()
        self._loop_thread: threading.Thread | None = None
        self._last_active: bool | None = None
        self._last_state = RechargeState.IDLE
        self._failure_traced = False
        self._last_marker_trace_at = 0.0
        self._trace = TraceSink("recharge", config=self.config.g)

    @rpc
    def start(self) -> None:
        super().start()
        # Image callbacks run in the stream subscription thread; the control loop
        # consumes the latest validated observation at fixed frequency.
        self.register_disposable(Disposable(self.color_image.subscribe(self._on_image)))
        self.register_disposable(Disposable(self.recharge_cancel.subscribe(self._on_cancel)))
        self._loop_stop.clear()
        self._loop_thread = threading.Thread(target=self._control_loop, daemon=True)
        self._loop_thread.start()

    @rpc
    def stop(self) -> None:
        # Stop always publishes zero and releases recharge ownership so stale motion
        # cannot survive module teardown.
        self._loop_stop.set()
        thread = self._loop_thread
        if thread is not None:
            thread.join(timeout=1.0)
        self._publish_active(False)
        self.recharge_cmd_vel.publish(Twist())
        self._trace.close()
        super().stop()

    @skill
    def start_recharge(self) -> str:
        """Start local visual docking after navigation has reached the visible charging tag area.

        Returns:
            The task identifier and current recharge state.  The first rollout keeps
            lie-down disabled until visual docking and charge verification are validated.
        """
        now = time.monotonic()
        with self._lock:
            self._controller.start(now)
            self._task_id = uuid.uuid4().hex
            self._failure_traced = False
            task_id = self._task_id
        self._trace_event(
            "recharge_task_started",
            {
                "task_id": task_id,
                "webrtc_method": self.config.g.unitree_webrtc_method,
                "velocity_api": self.config.velocity_api,
                "allow_liedown": self.config.allow_liedown,
            },
        )
        self._publish_active(True)
        return f"Recharge task {task_id} started in acquire state"

    @skill
    def cancel_recharge(self) -> str:
        """Cancel the active visual docking task and immediately publish zero velocity.

        Returns:
            The terminal state after cancellation.
        """
        with self._lock:
            self._controller.cancel(time.monotonic())
            state = self._controller.state.value
        self.recharge_cmd_vel.publish(Twist())
        self._publish_active(False)
        return f"Recharge task cancelled: {state}"

    @skill
    def recharge_status(self) -> str:
        """Return the current visual docking state and any terminal failure reason.

        Returns:
            A compact status string suitable for operator or agent monitoring.
        """
        with self._lock:
            state = self._controller.state.value
            failure = self._controller.failure
        if failure is None:
            return f"Recharge state: {state}"
        return f"Recharge state: {state}; failure={failure.code.value}; detail={failure.message}"

    @skill
    def observe_recharge_tag(self) -> str:
        """Report the latest validated charging-tag pose without commanding movement.

        Returns:
            Tag translation in camera coordinates, yaw, reprojection error, and image age;
            or a clear not-detected result. This is the required no-motion preflight check.
        """
        now = time.monotonic()
        with self._lock:
            observation = self._latest_observation
            image_age_s = self._age(now, self._latest_image_received_at)
        if observation is None:
            return f"Recharge tag not detected; image_age_s={image_age_s:.3f}"
        return (
            "Recharge tag detected; "
            f"x_m={observation.x_m:.3f}; y_m={observation.y_m:.3f}; z_m={observation.z_m:.3f}; "
            f"yaw_rad={observation.yaw_rad:.3f}; reprojection_error_px={observation.reprojection_error_px:.3f}; "
            f"image_age_s={image_age_s:.3f}; corners_px={observation.corners_px.tolist()}"
        )

    def _on_image(self, image: Image) -> None:
        """Detect the recharge tag and store only the latest quality-gated pose."""
        received_at = time.monotonic()
        observation = self._vision.observe(image)
        if observation is not None:
            # Replace camera timestamp with host receive time so freshness compares
            # against lowstate and controller ticks in the same monotonic clock.
            observation = replace(observation, observed_at=received_at)
        with self._lock:
            self._latest_image_received_at = received_at
            self._latest_observation = observation
            self._latest_observation_received_at = received_at if observation is not None else None
        if observation is not None and received_at - self._last_marker_trace_at >= 0.1:
            self._last_marker_trace_at = received_at
            self._trace_event(
                "recharge_marker_observation",
                {
                    "task_id": self._task_id,
                    "x_m": observation.x_m,
                    "y_m": observation.y_m,
                    "z_m": observation.z_m,
                    "yaw_rad": observation.yaw_rad,
                    "reprojection_error_px": observation.reprojection_error_px,
                    "corners_px": observation.corners_px.tolist(),
                },
            )

    def _on_cancel(self, msg: Bool) -> None:
        """MovementManager publishes this when teleop takes over."""
        if bool(msg.data):
            self.cancel_recharge()

    def _control_loop(self) -> None:
        """Poll lowstate, tick the pure controller, publish command, and trace outcome."""
        interval_s = 1.0 / max(self.config.control_hz, 1.0)
        while not self._loop_stop.wait(interval_s):
            now = time.monotonic()
            lowstate_age, bms_current = self._poll_lowstate()
            with self._lock:
                image_age = self._age(now, self._latest_image_received_at)
                observation = self._latest_observation
                if (
                    self._age(now, self._latest_observation_received_at)
                    > self._controller.config.image_max_age_s
                ):
                    # A cached old pose is useful for status reporting, but not for
                    # motion. Stale visual input is converted to "no observation".
                    observation = None
                command = self._controller.tick(
                    now,
                    observation,
                    image_age_s=image_age,
                    lowstate_age_s=lowstate_age,
                    charge_confirmed=self._charge_verifier.observe_current(bms_current, now),
                )
                state = self._controller.state
                failure = self._controller.failure
                task_id = self._task_id
            self._publish_control(command)
            self._publish_active(self._controller.active)
            self._trace_state_change(state, task_id)
            if command.request_liedown:
                self._handle_liedown(now)
            if failure is not None and not self._failure_traced:
                self._failure_traced = True
                self._trace_event(
                    "recharge_failure",
                    {
                        "task_id": task_id,
                        "code": failure.code.value,
                        "failed_state": failure.state.value,
                        "message": failure.message,
                        "elapsed_s": failure.elapsed_s,
                        "forward_error_m": None
                        if failure.dock_error is None
                        else failure.dock_error.forward_m,
                        "lateral_error_m": None
                        if failure.dock_error is None
                        else failure.dock_error.lateral_m,
                        "yaw_error_rad": None
                        if failure.dock_error is None
                        else failure.dock_error.yaw_rad,
                    },
                )
                self._trace_event(
                    "recharge_task_finished",
                    {
                        "task_id": task_id,
                        "outcome": "failed",
                        "final_error_code": failure.code.value,
                    },
                )

    def _poll_lowstate(self) -> tuple[float, float | None]:
        """Read only the scalar low-state values required by the controller via module RPC."""
        try:
            return self._go2.lowstate_age_s(), self._go2.bms_current()
        except Exception:
            return float("inf"), None

    def _handle_liedown(self, now: float) -> None:
        """Send the physical lie-down RPC only when config explicitly permits it."""
        if not self.config.allow_liedown:
            self._trace_event("recharge_liedown_blocked", {"task_id": self._task_id})
            with self._lock:
                self._controller.complete_visual_dock(now)
            self._trace_event(
                "recharge_task_finished",
                {"task_id": self._task_id, "outcome": "visual_docked", "final_error_code": None},
            )
            return
        try:
            success = self._go2.liedown()
        except Exception:
            success = False
        with self._lock:
            self._controller.notify_liedown_result(success, now)

    def _publish_control(self, command: RechargeCommand) -> None:
        """Publish the controller command to MovementManager's recharge-only input."""
        twist = Twist(
            Vector3(command.forward_mps, command.lateral_mps, 0.0),
            Vector3(0.0, 0.0, command.yaw_rad_s),
        )
        self.recharge_cmd_vel.publish(twist)
        self._trace_event(
            "recharge_cmd",
            {
                "task_id": self._task_id,
                "state": self._controller.state.value,
                "linear_x": command.forward_mps,
                "linear_y": command.lateral_mps,
                "angular_z": command.yaw_rad_s,
            },
        )

    def _publish_active(self, active: bool) -> None:
        """Publish ownership changes only on edges to avoid trace and mux noise."""
        if active == self._last_active:
            return
        self._last_active = active
        self.recharge_active.publish(Bool(data=active))

    def _trace_state_change(self, state: RechargeState, task_id: str | None) -> None:
        """Record state transitions once per edge."""
        if state == self._last_state:
            return
        self._trace_event(
            "recharge_state_transition",
            {"task_id": task_id, "from_state": self._last_state.value, "to_state": state.value},
        )
        self._last_state = state

    def _trace_event(self, event: str, fields: dict[str, object]) -> None:
        """Best-effort diagnostics; tracing must never break the control loop."""
        if not self._trace.accepts("full"):
            return
        try:
            self._trace.record(event, fields, estimated_bytes=1024)
        except Exception as exc:
            isolate_trace_failure(self._trace, exc)

    @staticmethod
    def _age(now: float, received_at: float | None) -> float:
        """Return +inf when a stream has not produced any sample yet."""
        return float("inf") if received_at is None else max(0.0, now - received_at)
