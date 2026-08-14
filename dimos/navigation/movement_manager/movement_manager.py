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

"""
MovementManager: click-to-goal relay + teleop/nav velocity mux.

NOTE: this should be majorly updated/reworked when mustafa's trajectory controller lands
"""

from __future__ import annotations

import math
import threading
import time
from typing import Any

from dimos_lcm.std_msgs import Bool  # type: ignore[import-untyped]
from reactivex.disposable import Disposable

from dimos.core.core import rpc
from dimos.core.module import Module, ModuleConfig
from dimos.core.stream import In, Out
from dimos.msgs.geometry_msgs.PointStamped import PointStamped
from dimos.msgs.geometry_msgs.Twist import Twist
from dimos.msgs.geometry_msgs.Vector3 import Vector3
from dimos.navigation.diagnostics.sink import TraceSink, isolate_trace_failure
from dimos.utils.logging_config import setup_logger

logger = setup_logger()

# without this you can (basically) click into infinity in rerun (not good for the planner)
MAX_CLICK_HORIZONTAL_M = 500.0
MAX_CLICK_VERTICAL_M = 50.0


class MovementManagerConfig(ModuleConfig):
    tele_cooldown_sec: float = 1.0
    tele_cmd_vel_scaling: Twist = Twist(Vector3(1, 1, 1), Vector3(1, 1, 1))
    # Existing blueprints remain permissive. External navigation-source
    # blueprints opt in and remain navigation-locked until the source is ready.
    require_navigation_source_health: bool = False


class MovementManager(Module):
    """Combine teleop/nav/recharge velocity sources and output the single robot cmd_vel."""

    config: MovementManagerConfig

    clicked_point: In[PointStamped]
    nav_cmd_vel: In[Twist]
    tele_cmd_vel: In[Twist]
    recharge_cmd_vel: In[Twist]
    # Legacy combined ownership input used by the first recharge module.
    recharge_active: In[Bool]
    # Integrated recharge keeps navigation active for staging, then separately
    # claims final visual-servo velocity ownership.
    recharge_task_active: In[Bool]
    recharge_servo_active: In[Bool]
    recharge_staging_goal: In[PointStamped]
    navigation_source_healthy: In[Bool]

    goal: Out[PointStamped]
    way_point: Out[PointStamped]
    cmd_vel: Out[Twist]
    stop_movement: Out[Bool]
    recharge_cancel: Out[Bool]
    recharge_task_granted: Out[Bool]
    recharge_servo_granted: Out[Bool]

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._lock = threading.Lock()
        self._teleop_active = False
        self._last_teleop_time = 0.0
        self._recharge_task_active = False
        self._recharge_servo_active = False
        self._navigation_source_healthy = not self.config.require_navigation_source_health
        self._navigation_source_fault_latched = False
        self._trace = TraceSink("mux", config=self.config.g)

    @rpc
    def start(self) -> None:
        super().start()
        self.register_disposable(Disposable(self.clicked_point.subscribe(self._on_click)))
        self.register_disposable(Disposable(self.nav_cmd_vel.subscribe(self._on_nav)))
        self.register_disposable(Disposable(self.tele_cmd_vel.subscribe(self._on_teleop)))
        self.register_disposable(Disposable(self.recharge_cmd_vel.subscribe(self._on_recharge_cmd)))
        self.register_disposable(
            Disposable(self.recharge_active.subscribe(self._on_recharge_active))
        )
        self.register_disposable(
            Disposable(self.recharge_task_active.subscribe(self._on_recharge_task_active))
        )
        self.register_disposable(
            Disposable(self.recharge_servo_active.subscribe(self._on_recharge_servo_active))
        )
        self.register_disposable(
            Disposable(self.recharge_staging_goal.subscribe(self._on_recharge_staging_goal))
        )
        if self.config.require_navigation_source_health:
            self.register_disposable(
                Disposable(
                    self.navigation_source_healthy.subscribe(self._on_navigation_source_health)
                )
            )

    @rpc
    def stop(self) -> None:
        with self._lock:
            self._teleop_active = False
        super().stop()
        self._trace.close()

    def _on_click(self, msg: PointStamped) -> None:
        if not all(math.isfinite(v) for v in (msg.x, msg.y, msg.z)):
            logger.warning("Ignored invalid click", x=msg.x, y=msg.y, z=msg.z)
            return

        with self._lock:
            navigation_source_healthy = self._navigation_source_healthy
        if not navigation_source_healthy:
            logger.warning("Ignored navigation click while navigation source is unhealthy")
            return
        if (
            abs(msg.x) > MAX_CLICK_HORIZONTAL_M
            or abs(msg.y) > MAX_CLICK_HORIZONTAL_M
            or abs(msg.z) > MAX_CLICK_VERTICAL_M
        ):
            logger.warning("Ignored out-of-range click", x=msg.x, y=msg.y, z=msg.z)
            return

        with self._lock:
            recharge_was_active = self._recharge_task_active
            if recharge_was_active:
                self._recharge_task_active = False
                self._recharge_servo_active = False
        if recharge_was_active:
            # A human click is an explicit operator override, just like teleop.
            self.recharge_cancel.publish(Bool(data=True))
            self.cmd_vel.publish(Twist())
            self.recharge_task_granted.publish(Bool(data=False))
            self.recharge_servo_granted.publish(Bool(data=False))

        logger.debug("Goal", x=round(msg.x, 1), y=round(msg.y, 1), z=round(msg.z, 1))
        self.way_point.publish(msg)
        self.goal.publish(msg)

    def _cancel_goal(self) -> None:
        self.stop_movement.publish(Bool(data=True))
        # NOTE: this NaN goal is more of a safety fallback.
        # It can be REALLY bad if a robot is supposed to stop moving but wont
        # we should probably think a more robust/strict requirement on planners
        cancel = PointStamped(
            ts=time.time(), frame_id="map", x=float("nan"), y=float("nan"), z=float("nan")
        )
        self.way_point.publish(cancel)
        self.goal.publish(cancel)
        logger.debug("Navigation cancelled — waiting for new goal")

    def _on_nav(self, msg: Twist) -> None:
        suppressed_elapsed: float | None = None
        recharge_servo_active = False
        navigation_source_healthy = False
        with self._lock:
            recharge_servo_active = self._recharge_servo_active
            navigation_source_healthy = self._navigation_source_healthy
            if not navigation_source_healthy:
                suppressed_elapsed = 0.0
            elif recharge_servo_active:
                # Visual recharge owns final docking motion. Planner velocity must not
                # mix with the small camera-servoing commands.
                suppressed_elapsed = 0.0
            elif self._teleop_active:
                # check if cooldown has expired
                elapsed = time.monotonic() - self._last_teleop_time
                if elapsed < self.config.tele_cooldown_sec:
                    suppressed_elapsed = elapsed
                else:
                    self._teleop_active = False
            if suppressed_elapsed is None:
                self.cmd_vel.publish(msg)

        if suppressed_elapsed is not None:
            self._trace_mux(
                "nav_command_suppressed",
                msg,
                source=(
                    "navigation_source_fault"
                    if not navigation_source_healthy
                    else "navigation_recharge"
                    if recharge_servo_active
                    else "navigation"
                ),
                cooldown_elapsed_sec=suppressed_elapsed,
            )
            return
        self._trace_mux("mux_command_published", msg, source="navigation")

    def _on_teleop(self, msg: Twist) -> None:
        recharge_was_active = False
        with self._lock:
            if not self._navigation_source_healthy:
                self._trace_mux(
                    "teleop_command_suppressed",
                    msg,
                    source="navigation_source_fault",
                )
                return
            recharge_was_active = self._recharge_task_active or self._recharge_servo_active
            self._recharge_task_active = False
            self._recharge_servo_active = False
            self._teleop_active = True
            self._last_teleop_time = time.monotonic()

        if recharge_was_active:
            # Manual input is the highest-priority operator override. Tell the recharge
            # module to cancel and publish zero before forwarding the manual command.
            self.recharge_cancel.publish(Bool(data=True))
            self.cmd_vel.publish(Twist())
            self.recharge_task_granted.publish(Bool(data=False))
            self.recharge_servo_granted.publish(Bool(data=False))
            self._trace_mux("recharge_cancelled_by_teleop", Twist(), source="teleop")
        self._cancel_goal()

        scale = self.config.tele_cmd_vel_scaling
        scaled = Twist(
            linear=Vector3(
                msg.linear.x * scale.linear.x,
                msg.linear.y * scale.linear.y,
                msg.linear.z * scale.linear.z,
            ),
            angular=Vector3(
                msg.angular.x * scale.angular.x,
                msg.angular.y * scale.angular.y,
                msg.angular.z * scale.angular.z,
            ),
        )
        self.cmd_vel.publish(scaled)
        self._trace_mux(
            "mux_command_published",
            scaled,
            source="teleop",
            input_twist=_twist_fields(msg),
        )

    def _on_recharge_active(self, msg: Bool) -> None:
        """Backward-compatible combined task+servo ownership input."""
        self._on_recharge_task_active(msg)
        self._on_recharge_servo_active(msg)

    def _on_recharge_task_active(self, msg: Bool) -> None:
        """Claim the navigation task while still allowing staging nav velocity."""
        active = bool(msg.data)
        with self._lock:
            if active and not self._navigation_source_healthy:
                self.recharge_task_granted.publish(Bool(data=False))
                self._trace_mux(
                    "recharge_task_suppressed",
                    Twist(),
                    source="navigation_source_fault",
                )
                return
            was_active = self._recharge_task_active
            self._recharge_task_active = active
            if not active:
                self._recharge_servo_active = False
        if active and not was_active:
            self.cmd_vel.publish(Twist())
            self._cancel_goal()
        self.recharge_task_granted.publish(Bool(data=active))
        if not active and was_active:
            self.cmd_vel.publish(Twist())
            self.recharge_servo_granted.publish(Bool(data=False))
            self._trace_mux("recharge_task_released", Twist(), source="recharge")

    def _on_recharge_servo_active(self, msg: Bool) -> None:
        """Grant final visual-servo velocity ownership only inside an active task."""
        requested = bool(msg.data)
        with self._lock:
            active = requested and self._navigation_source_healthy and self._recharge_task_active
            was_active = self._recharge_servo_active
            self._recharge_servo_active = active
        if active and not was_active:
            self.cmd_vel.publish(Twist())
            self._cancel_goal()
        if not active and was_active:
            self.cmd_vel.publish(Twist())
            self._trace_mux("recharge_servo_released", Twist(), source="recharge")
        self.recharge_servo_granted.publish(Bool(data=active))

    def _on_recharge_staging_goal(self, msg: PointStamped) -> None:
        """Forward the recharge-owned staging goal while navigation still owns velocity."""
        with self._lock:
            accepted = (
                self._navigation_source_healthy
                and self._recharge_task_active
                and not self._recharge_servo_active
            )
        if not accepted:
            logger.warning("Ignored recharge staging goal without task ownership")
            return
        self.way_point.publish(msg)
        self.goal.publish(msg)

    def _on_recharge_cmd(self, msg: Twist) -> None:
        """Forward recharge velocity only while recharge_active is true."""
        with self._lock:
            active = self._recharge_servo_active and self._navigation_source_healthy
        if not active:
            self._trace_mux("recharge_command_suppressed", msg, source="recharge")
            return
        self.cmd_vel.publish(msg)
        self._trace_mux("mux_command_published", msg, source="recharge")

    def _on_navigation_source_health(self, msg: Bool) -> None:
        """Gate autonomous motion when the selected lidar/odometry source fails.

        A fault is latched after the source has become ready once. Recovery then
        requires restarting the blueprint; a transient later ``True`` cannot
        silently resume an old navigation goal. Viewer teleop is also blocked;
        the independent physical remote remains the operator recovery path.
        """
        requested_healthy = bool(msg.data)
        with self._lock:
            if self._navigation_source_fault_latched:
                return
            was_healthy = self._navigation_source_healthy
            if not requested_healthy and was_healthy:
                self._navigation_source_fault_latched = True
            self._navigation_source_healthy = requested_healthy

        if requested_healthy:
            logger.info("Navigation source is ready; autonomous velocity enabled")
            return

        self.cmd_vel.publish(Twist())
        self._cancel_goal()
        logger.error("Navigation source fault; autonomous motion stopped until restart")
        self._trace_mux("navigation_source_fault_stop", Twist(), source="navigation_source")

    def _trace_mux(
        self,
        event: str,
        twist: Twist,
        *,
        source: str,
        cooldown_elapsed_sec: float | None = None,
        input_twist: dict[str, float] | None = None,
    ) -> None:
        if not self._trace.accepts("full"):
            return
        try:
            fields: dict[str, object] = {
                "source": source,
                "command_muxed_ts": time.time(),
                "command_muxed_monotonic_ns": time.monotonic_ns(),
                "twist": _twist_fields(twist),
            }
            if cooldown_elapsed_sec is not None:
                fields["cooldown_elapsed_sec"] = cooldown_elapsed_sec
                fields["tele_cooldown_sec"] = self.config.tele_cooldown_sec
            if input_twist is not None:
                fields["input_twist"] = input_twist
                fields["teleop_scaling"] = _twist_fields(self.config.tele_cmd_vel_scaling)
            self._trace.record(event, fields, estimated_bytes=896)
        except Exception as exc:
            isolate_trace_failure(self._trace, exc)


def _twist_fields(twist: Twist) -> dict[str, float]:
    return {
        "linear_x": float(twist.linear.x),
        "linear_y": float(twist.linear.y),
        "linear_z": float(twist.linear.z),
        "angular_x": float(twist.angular.x),
        "angular_y": float(twist.angular.y),
        "angular_z": float(twist.angular.z),
    }
