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

from __future__ import annotations

from typing import Any, Protocol

from dimos.msgs.geometry_msgs.PoseStamped import PoseStamped
from dimos.msgs.geometry_msgs.Twist import Twist
from dimos.msgs.geometry_msgs.Vector3 import Vector3
from dimos.msgs.nav_msgs.Path import Path
from dimos.navigation.stairs.contracts import StairCorridor, StairPhase
from dimos.robot.unitree.go2.stair_locomotion.config import StairLocomotionConfig
from dimos.robot.unitree.go2.stair_locomotion.twist_limiter import apply_stair_twist_limit
from dimos.utils.trigonometry import angle_diff


class Go2SportConnection(Protocol):
    def move(self, twist: Twist, duration: float = 0.0) -> bool: ...

    def publish_request(self, topic: str, data: dict[str, Any]) -> dict[Any, Any]: ...


# Unitree sport API ids from unitree_skill_container
_FOOT_RAISE_HEIGHT_API = 1014
_BODY_HEIGHT_API = 1013
_SWITCH_GAIT_API = 1011


class StairLocomotionPolicy:
    """State machine for traversing a single stair corridor on Go2."""

    def __init__(
        self,
        connection: Go2SportConnection,
        config: StairLocomotionConfig | None = None,
    ) -> None:
        self._connection = connection
        self._config = config or StairLocomotionConfig()
        self._phase = StairPhase.IDLE
        self._corridor: StairCorridor | None = None
        self._path: Path | None = None
        self._path_index = 0
        self._sport_configured = False

    @property
    def phase(self) -> StairPhase:
        return self._phase

    def reset(self) -> None:
        self._phase = StairPhase.IDLE
        self._corridor = None
        self._path = None
        self._path_index = 0
        self._sport_configured = False

    def start(self, corridor: StairCorridor, path: Path) -> None:
        self._corridor = corridor
        self._path = path
        self._path_index = 0
        self._phase = StairPhase.APPROACH
        self._sport_configured = False

    def _configure_sport_for_stairs(self) -> None:
        if self._sport_configured:
            return
        self._connection.publish_request(
            "rt/api/sport/request",
            {"api_id": _FOOT_RAISE_HEIGHT_API, "parameter": {"data": self._config.foot_raise_height_m}},
        )
        self._connection.publish_request(
            "rt/api/sport/request",
            {"api_id": _BODY_HEIGHT_API, "parameter": {"data": self._config.body_height_delta_m}},
        )
        self._connection.publish_request(
            "rt/api/sport/request",
            {"api_id": _SWITCH_GAIT_API, "parameter": {"data": 0}},
        )
        self._sport_configured = True

    def _distance_to_path_start(self, odom: PoseStamped) -> float:
        if not self._path or not self._path.poses:
            return float("inf")
        p = self._path.poses[0].position
        return odom.position.distance(p)

    def _yaw_error_to_corridor(self, odom: PoseStamped) -> float:
        if self._corridor is None:
            return 0.0
        robot_yaw = odom.orientation.euler[2]
        return angle_diff(self._corridor.axis_yaw, robot_yaw)

    def _advance_path_index(self, odom: PoseStamped) -> None:
        if not self._path or self._path_index >= len(self._path.poses) - 1:
            return
        target = self._path.poses[self._path_index]
        if odom.position.distance(target.position) < 0.12:
            self._path_index += 1

    def _target_linear_x(self) -> float:
        cfg = self._config
        base = cfg.max_linear_x
        if self._corridor and self._path and self._path_index < len(self._path.poses):
            # slow near riser crossings (every ~tread)
            if self._path_index > 0 and self._path_index % 2 == 0:
                base *= cfg.riser_slowdown_factor
        if not self._corridor or self._corridor.ascending:
            return max(cfg.min_linear_x, min(cfg.max_linear_x, base))
        return -max(cfg.min_linear_x, min(cfg.max_linear_x, base))

    def step(self, odom: PoseStamped) -> Twist:
        """Compute the next cmd_vel for the current phase."""
        if self._phase == StairPhase.IDLE or self._corridor is None or self._path is None:
            return Twist()

        pitch = odom.orientation.euler[1]
        if abs(pitch) > self._config.max_pitch_rad:
            self._connection.move(Twist(), duration=0.0)
            return Twist()

        if self._phase == StairPhase.APPROACH:
            dist = self._distance_to_path_start(odom)
            if dist <= self._config.approach_distance_m:
                self._phase = StairPhase.ALIGN
            yaw_err = self._yaw_error_to_corridor(odom)
            return apply_stair_twist_limit(
                Twist(
                    linear=Vector3(self._config.min_linear_x * 0.5, 0.0, 0.0),
                    angular=Vector3(0.0, 0.0, 0.5 * yaw_err),
                ),
                self._config,
                on_stair=False,
            )

        if self._phase == StairPhase.ALIGN:
            yaw_err = self._yaw_error_to_corridor(odom)
            if abs(yaw_err) < self._config.align_yaw_tolerance_rad:
                self._phase = StairPhase.ON_STAIR
                self._configure_sport_for_stairs()
            return apply_stair_twist_limit(
                Twist(linear=Vector3(0.0, 0.0, 0.0), angular=Vector3(0.0, 0.0, 0.8 * yaw_err)),
                self._config,
                on_stair=False,
            )

        if self._phase == StairPhase.ON_STAIR:
            self._advance_path_index(odom)
            if self._path_index >= len(self._path.poses) - 1:
                self._phase = StairPhase.EXIT
            lin_x = self._target_linear_x()
            yaw_err = self._yaw_error_to_corridor(odom)
            twist = Twist(
                linear=Vector3(lin_x, 0.0, 0.0),
                angular=Vector3(0.0, 0.0, 0.3 * yaw_err),
            )
            cmd = apply_stair_twist_limit(twist, self._config, on_stair=True)
            self._connection.move(cmd, duration=0.0)
            return cmd

        if self._phase == StairPhase.EXIT:
            self.reset()
            return Twist()

        return Twist()
