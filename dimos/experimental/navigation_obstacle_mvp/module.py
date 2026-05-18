# Copyright 2025-2026 Dimensional Inc.
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

from __future__ import annotations

import time

from unitree_webrtc_connect.constants import RTC_TOPIC

from dimos.agents.annotation import skill
from dimos.core.core import rpc
from dimos.core.module import Module
from dimos.core.stream import Out
from dimos.experimental.navigation_obstacle_mvp.obstacle_decision import (
    CrossingAction,
    ThresholdCrossingPlanner,
    ThresholdObstacle,
)
from dimos.msgs.geometry_msgs.Twist import Twist
from dimos.msgs.geometry_msgs.Vector3 import Vector3
from dimos.robot.unitree.go2.connection_spec import GO2ConnectionSpec
from dimos.utils.logging_config import setup_logger

logger = setup_logger()


class ThresholdCrossingSkillContainer(Module):
    """Skill container for physically crossing low thresholds."""

    cmd_vel: Out[Twist]
    _connection: GO2ConnectionSpec
    _command_period_s: float = 0.05

    def __init__(self, **kwargs) -> None:  # type: ignore[no-untyped-def]
        super().__init__(**kwargs)
        self._planner = ThresholdCrossingPlanner()

    @rpc
    def start(self) -> None:
        super().start()

    @rpc
    def stop(self) -> None:
        self.cmd_vel.publish(Twist.zero())
        super().stop()

    @skill
    def cross_threshold(
        self,
        height_m: float,
        width_m: float,
        distance_m: float,
        lateral_clearance_m: float,
        heading_error_rad: float = 0.0,
        dry_run: bool = True,
    ) -> str:
        """Plan or execute a physical crossing maneuver for a low threshold.

        Use this when the obstacle ahead is a low step/threshold rather than a
        wall. Obstacles under 10 cm stay in normal navigation; obstacles above
        the safe crossing envelope are rejected for avoidance/replanning.

        Args:
            height_m: Estimated obstacle height in meters.
            width_m: Estimated obstacle depth along the travel direction in meters.
            distance_m: Distance from robot front to the obstacle in meters.
            lateral_clearance_m: Free side clearance around the obstacle in meters.
            heading_error_rad: Robot heading error relative to the obstacle normal.
            dry_run: When true, only return the plan without sending commands.
        """
        obstacle = ThresholdObstacle(
            height_m=float(height_m),
            width_m=float(width_m),
            distance_m=float(distance_m),
            lateral_clearance_m=float(lateral_clearance_m),
            heading_error_rad=float(heading_error_rad),
        )
        decision = self._planner.evaluate(obstacle)

        if dry_run or not decision.can_cross:
            return decision.summary()

        logger.info("Executing threshold crossing", summary=decision.summary())
        for action in decision.actions:
            self._execute_action(action)

        return f"Threshold crossing completed. {decision.summary()}"

    def _execute_action(self, action: CrossingAction) -> None:
        match action.kind:
            case "sport":
                self._execute_sport_action(action)
            case "velocity":
                self._execute_velocity_action(action)
            case "stop":
                self._publish_stop(action.duration_s)
            case "wait":
                time.sleep(action.duration_s)

    def _execute_sport_action(self, action: CrossingAction) -> None:
        if action.sport_api_id is None:
            return

        payload: dict[str, object] = {"api_id": action.sport_api_id}
        if action.sport_parameter is not None:
            payload["parameter"] = {"data": action.sport_parameter}

        self._connection.publish_request(RTC_TOPIC["SPORT_MOD"], payload)

    def _execute_velocity_action(self, action: CrossingAction) -> None:
        twist = Twist(
            linear=Vector3(action.linear_x_mps, action.linear_y_mps, 0.0),
            angular=Vector3(0.0, 0.0, action.angular_z_radps),
        )
        end_time = time.monotonic() + action.duration_s
        while time.monotonic() < end_time:
            self.cmd_vel.publish(twist)
            time.sleep(self._command_period_s)

    def _publish_stop(self, duration_s: float) -> None:
        self.cmd_vel.publish(Twist.zero())
        if duration_s > 0:
            time.sleep(duration_s)
