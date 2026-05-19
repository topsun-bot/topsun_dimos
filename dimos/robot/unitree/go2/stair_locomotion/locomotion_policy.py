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

"""Go2 stair climbing state machine: Sport API gait + corridor-aligned cmd_vel.

Triggered by ``LocalPlanner`` after ``StairNavigatorModule`` arms a corridor and
``GlobalPlanner`` publishes a centerline path.
"""

from __future__ import annotations

import math

from dimos.core.global_config import GlobalConfig
from dimos.msgs.geometry_msgs.PoseStamped import PoseStamped
from dimos.msgs.geometry_msgs.Twist import Twist
from dimos.msgs.nav_msgs.Path import Path
from dimos.navigation.stairs.contracts import StairCorridor, StairPhase
from dimos.navigation.stairs.geometry import point_in_stair_corridor
from dimos.robot.unitree.go2.stair_locomotion.config import StairLocomotionConfig
from dimos.robot.unitree.go2.stair_locomotion.sport_api import Go2SportConnection, Go2StairSportController
from dimos.robot.unitree.go2.stair_locomotion.twist_limiter import twist_along_corridor
from dimos.utils.logging_config import setup_logger
from dimos.utils.trigonometry import angle_diff

logger = setup_logger()


class StairLocomotionPolicy:
    """State machine for traversing a single stair corridor on Go2."""

    def __init__(
        self,
        connection: Go2SportConnection,
        config: StairLocomotionConfig | None = None,
        global_config: GlobalConfig | None = None,
    ) -> None:
        self._connection = connection
        self._global_config = global_config
        if config is not None:
            self._config = config
        elif global_config is not None and global_config.simulation:
            # MuJoCo uses cmd_vel only (no FootRaiseHeight); need stronger forward drive.
            self._config = StairLocomotionConfig(
                min_linear_x=0.28,
                max_linear_x=0.55,
                riser_slowdown_factor=0.85,
                yaw_gain_on_stair=0.2,
            )
        else:
            self._config = StairLocomotionConfig()
        self._sport = Go2StairSportController(connection, self._config, global_config=global_config)
        self._phase = StairPhase.IDLE
        self._corridor: StairCorridor | None = None
        self._path: Path | None = None
        self._path_index = 0
        self._finished = False

    @property
    def phase(self) -> StairPhase:
        return self._phase

    @property
    def finished(self) -> bool:
        return self._finished

    def reset(self) -> None:
        if self._phase == StairPhase.ON_STAIR:
            self._sport.exit_stair_mode()
        self._phase = StairPhase.IDLE
        self._corridor = None
        self._path = None
        self._path_index = 0
        self._finished = False

    def start(self, corridor: StairCorridor, path: Path) -> None:
        if self._phase in (StairPhase.ALIGN, StairPhase.ON_STAIR):
            self._corridor = corridor
            self.update_path(path)
            logger.info(
                "Stair climb continuing",
                mean_riser=round(corridor.mean_riser, 3),
                path_poses=len(path.poses),
                phase=self._phase.value,
            )
            return

        self._corridor = corridor
        self._path = path
        self._path_index = 0
        self._phase = StairPhase.APPROACH
        self._finished = False
        logger.info(
            "Stair locomotion started",
            mean_riser=round(corridor.mean_riser, 3),
            path_poses=len(path.poses),
            phase=self._phase.value,
        )

    def update_path(self, path: Path) -> None:
        """Refresh centerline waypoints without restarting the climb state machine."""
        self._path = path

    def _distance_to_nearest_path_point(self, odom: PoseStamped) -> float:
        if not self._path or not self._path.poses:
            return float("inf")
        pos = odom.position
        return min(pos.distance(pose.position) for pose in self._path.poses)

    def _set_phase(self, phase: StairPhase) -> None:
        if phase != self._phase:
            logger.info("Stair phase transition", from_phase=self._phase.value, to_phase=phase.value)
            self._phase = phase

    def _sim_speed_scale(self) -> float:
        if self._global_config is not None and self._global_config.simulation:
            return 1.0
        return 1.0

    def _advance_path_index(self, odom: PoseStamped) -> None:
        if not self._path or self._path_index >= len(self._path.poses) - 1:
            return
        target = self._path.poses[self._path_index]
        if odom.position.distance(target.position) < 0.12:
            self._path_index += 1

    def _target_speed_mps(self) -> float:
        cfg = self._config
        base = cfg.max_linear_x
        if self._corridor and self._path and self._path_index < len(self._path.poses):
            if self._path_index > 0 and self._path_index % 2 == 0:
                base *= cfg.riser_slowdown_factor
        signed = base if (self._corridor is None or self._corridor.ascending) else -base
        return max(-cfg.max_linear_x, min(cfg.max_linear_x, signed))

    def _apply_hardware_motion(self, twist: Twist) -> Twist:
        """Send cmd_vel to the connection (MuJoCo SHM + hardware WebRTC)."""
        self._connection.move(twist, duration=0.0)
        return twist

    def step(self, odom: PoseStamped) -> Twist:
        """Compute the next cmd_vel for the current phase."""
        if self._phase == StairPhase.IDLE or self._corridor is None or self._path is None:
            return Twist()

        corridor = self._corridor
        robot_yaw = odom.orientation.euler[2]
        pitch = odom.orientation.euler[1]

        if abs(pitch) > self._config.max_pitch_rad:
            logger.warning("Stair locomotion paused: excessive pitch", pitch_deg=round(pitch, 2))
            return Twist()

        if self._phase == StairPhase.APPROACH:
            dist = self._distance_to_nearest_path_point(odom)
            at_stair_mouth = point_in_stair_corridor(odom.position, corridor) or dist <= (
                self._config.approach_distance_m + corridor.safe_half_width
            )
            if at_stair_mouth:
                self._set_phase(StairPhase.ALIGN)
            speed = self._config.min_linear_x * 0.6 * self._sim_speed_scale()
            twist = twist_along_corridor(
                speed,
                robot_yaw,
                corridor,
                self._config,
                yaw_gain=self._config.yaw_gain_approach,
                on_stair=False,
            )
            return self._apply_hardware_motion(twist)

        if self._phase == StairPhase.ALIGN:
            yaw_err = angle_diff(corridor.axis_yaw, robot_yaw)
            if abs(yaw_err) < self._config.align_yaw_tolerance_rad:
                self._set_phase(StairPhase.ON_STAIR)
                self._sport.enter_stair_mode()
                logger.info("Entering ON_STAIR — Sport gait configured for climbing")
            align_forward = 0.0
            if abs(yaw_err) < math.radians(20.0):
                align_forward = self._config.min_linear_x * 0.35 * self._sim_speed_scale()
            twist = twist_along_corridor(
                align_forward,
                robot_yaw,
                corridor,
                self._config,
                yaw_gain=self._config.yaw_gain_align,
                on_stair=False,
            )
            return self._apply_hardware_motion(twist)

        if self._phase == StairPhase.ON_STAIR:
            self._advance_path_index(odom)
            if self._path_index >= len(self._path.poses) - 1:
                self._set_phase(StairPhase.EXIT)
            speed = self._target_speed_mps()
            if self._global_config is not None and self._global_config.simulation:
                # Keep commanded forward speed high on discrete MuJoCo treads.
                sign = 1.0 if speed >= 0.0 else -1.0
                speed = sign * max(abs(speed), self._config.max_linear_x * 0.92)
            twist = twist_along_corridor(
                speed,
                robot_yaw,
                corridor,
                self._config,
                yaw_gain=self._config.yaw_gain_on_stair,
                on_stair=True,
            )
            return self._apply_hardware_motion(twist)

        if self._phase == StairPhase.EXIT:
            self._sport.exit_stair_mode()
            self._finished = True
            self._phase = StairPhase.IDLE
            logger.info("Stair locomotion finished")
            return Twist()

        return Twist()
