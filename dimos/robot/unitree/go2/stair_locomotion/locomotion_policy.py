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
from dimos.navigation.stairs.geometry import point_in_stair_corridor, project_point_to_centerline
from dimos.navigation.stairs.plan_in_corridor import _project_on_polyline
from dimos.robot.unitree.go2.stair_locomotion.config import StairLocomotionConfig
from dimos.robot.unitree.go2.stair_locomotion.sport_api import Go2SportConnection, Go2StairSportController
from dimos.robot.unitree.go2.stair_locomotion.twist_limiter import twist_along_corridor
from dimos.utils.logging_config import setup_logger
from dimos.utils.trigonometry import angle_diff

logger = setup_logger()

# MuJoCo: hold ALIGN this many control ticks before Sport mode (10 Hz → 0.8 s).
_SIM_ALIGN_HOLD_TICKS = 8
# Climbing pitch on a ramp/stair tread often exceeds flat-ground tip limits.
_SIM_MAX_PITCH_RAD = math.radians(38.0)
# After WalkStair settle, ramp linear speed over this many ticks (10 Hz).
_SIM_ON_STAIR_RAMP_TICKS = 40


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
            # Gentle MuJoCo profile: Go1 ONNX + discrete treads tip easily if driven hard.
            self._config = StairLocomotionConfig(
                min_linear_x=0.14,
                max_linear_x=0.20,
                stair_sport_mode="walk_stair",
                foot_raise_height_m=0.11,
                riser_slowdown_factor=0.95,
                yaw_gain_approach=0.4,
                yaw_gain_align=0.55,
                yaw_gain_on_stair=0.10,
                align_yaw_tolerance_rad=math.radians(14.0),
                max_pitch_rad=_SIM_MAX_PITCH_RAD,
                sim_tip_grace_ticks=35,
                sim_on_stair_settle_ticks=12,
            )
        else:
            self._config = StairLocomotionConfig()
        self._sport = Go2StairSportController(connection, self._config, global_config=global_config)
        self._phase = StairPhase.IDLE
        self._corridor: StairCorridor | None = None
        self._path: Path | None = None
        self._path_index = 0
        self._finished = False
        self._align_ticks = 0
        self._on_stair_ticks = 0
        self._aborted = False
        self._along_lo = 0.0
        self._along_hi = 1.0
        self._stuck_along_m = 0.0
        self._stuck_ticks = 0
        self._sim_unstuck_boost = 0

    @property
    def phase(self) -> StairPhase:
        return self._phase

    @property
    def finished(self) -> bool:
        return self._finished

    @property
    def aborted(self) -> bool:
        return self._aborted

    def reset(self) -> None:
        if self._phase == StairPhase.ON_STAIR:
            self._sport.exit_stair_mode()
        self._phase = StairPhase.IDLE
        self._corridor = None
        self._path = None
        self._path_index = 0
        self._finished = False
        self._aborted = False
        self._align_ticks = 0
        self._on_stair_ticks = 0
        self._along_lo = 0.0
        self._along_hi = 1.0
        self._stuck_along_m = 0.0
        self._stuck_ticks = 0
        self._sim_unstuck_boost = 0

    def _bind_along_corridor_span(self, path: Path) -> None:
        if self._corridor is None or len(self._corridor.centerline) < 2:
            return
        cl = self._corridor.centerline
        d0, _ = _project_on_polyline(path.poses[0].position.x, path.poses[0].position.y, cl)
        d1, _ = _project_on_polyline(path.poses[-1].position.x, path.poses[-1].position.y, cl)
        if not self._corridor.ascending:
            d0, d1 = d1, d0
        self._along_lo = min(d0, d1)
        self._along_hi = max(d0, d1)

    def _along_corridor_m(self, odom: PoseStamped) -> float:
        if self._corridor is None:
            return 0.0
        _, _, along = project_point_to_centerline(
            odom.position.x, odom.position.y, self._corridor.centerline
        )
        return along

    def _climb_progress(self, odom: PoseStamped) -> float:
        span = self._along_hi - self._along_lo
        if span < 1e-3:
            return 1.0
        return max(0.0, min(1.0, (self._along_corridor_m(odom) - self._along_lo) / span))

    def _stair_traversal_complete(self, odom: PoseStamped) -> bool:
        return self._climb_progress(odom) >= self._config.climb_complete_ratio

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
        self._aborted = False
        self._align_ticks = 0
        self._on_stair_ticks = 0
        self._sim_unstuck_boost = 0
        self._bind_along_corridor_span(path)
        logger.info(
            "Stair locomotion started",
            mean_riser=round(corridor.mean_riser, 3),
            path_poses=len(path.poses),
            along_span_m=round(self._along_hi - self._along_lo, 2),
            phase=self._phase.value,
        )

    def update_path(self, path: Path) -> None:
        """Refresh centerline waypoints without restarting the climb state machine."""
        self._path = path
        self._bind_along_corridor_span(path)

    def _distance_to_nearest_path_point(self, odom: PoseStamped) -> float:
        if not self._path or not self._path.poses:
            return float("inf")
        pos = odom.position
        return min(pos.distance(pose.position) for pose in self._path.poses)

    def _set_phase(self, phase: StairPhase) -> None:
        if phase != self._phase:
            logger.info("Stair phase transition", from_phase=self._phase.value, to_phase=phase.value)
            if phase == StairPhase.ALIGN:
                self._align_ticks = 0
            self._phase = phase

    def _sim_forward_floor(self) -> float | None:
        if self._global_config is not None and self._global_config.simulation:
            if self._phase == StairPhase.ON_STAIR:
                ticks_since_drive = self._on_stair_ticks - self._config.sim_on_stair_settle_ticks
                if ticks_since_drive < _SIM_ON_STAIR_RAMP_TICKS:
                    return 0.12
            return 0.22
        return None

    def _abort_if_tipped(self, odom: PoseStamped) -> bool:
        """Abort only while climbing; ignore roll glitches during APPROACH/ALIGN."""
        if self._phase != StairPhase.ON_STAIR:
            return False
        if (
            self._global_config is not None
            and self._global_config.simulation
            and self._on_stair_ticks < self._config.sim_tip_grace_ticks
        ):
            return False
        pitch = odom.pitch
        roll = odom.roll
        # Forward/back fall on stairs; ignore roll unless nearly inverted.
        if abs(pitch) > self._config.max_pitch_rad or abs(roll) > math.radians(70.0):
            logger.warning(
                "Stair locomotion aborted — robot tipped",
                pitch_deg=round(math.degrees(pitch), 1),
                roll_deg=round(math.degrees(roll), 1),
                on_stair_ticks=self._on_stair_ticks,
            )
            self._sport.exit_stair_mode()
            self._phase = StairPhase.IDLE
            self._finished = True
            self._aborted = True
            return True
        return False

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
        """Send cmd_vel to GO2 (MuJoCo SHM / hardware). Simulation relies on cmd_vel stream + move."""
        self._connection.move(twist, duration=0.0)
        if (
            self._global_config is not None
            and self._global_config.simulation
            and self._phase == StairPhase.ON_STAIR
            and self._on_stair_ticks >= self._config.sim_on_stair_settle_ticks
            and self._on_stair_ticks % 15 == 0
        ):
            logger.info(
                "ON_STAIR cmd_vel",
                linear_x=round(twist.linear.x, 3),
                angular_z=round(twist.angular.z, 3),
                on_stair_ticks=self._on_stair_ticks,
            )
        return twist

    def step(self, odom: PoseStamped) -> Twist:
        """Compute the next cmd_vel for the current phase."""
        if self._phase == StairPhase.IDLE or self._corridor is None or self._path is None:
            return Twist()

        if self._phase == StairPhase.ON_STAIR:
            self._on_stair_ticks += 1

        if self._abort_if_tipped(odom):
            return Twist()

        corridor = self._corridor
        robot_yaw = odom.yaw

        if self._phase == StairPhase.APPROACH:
            dist = self._distance_to_nearest_path_point(odom)
            at_stair_mouth = point_in_stair_corridor(odom.position, corridor) or dist <= (
                self._config.approach_distance_m + corridor.safe_half_width
            )
            if at_stair_mouth:
                self._set_phase(StairPhase.ALIGN)
            speed = self._config.min_linear_x * 0.85
            twist = twist_along_corridor(
                speed,
                robot_yaw,
                corridor,
                self._config,
                yaw_gain=self._config.yaw_gain_approach,
                on_stair=False,
                sim_min_forward_ratio=self._sim_forward_floor(),
            )
            return self._apply_hardware_motion(twist)

        if self._phase == StairPhase.ALIGN:
            yaw_err = angle_diff(corridor.axis_yaw, robot_yaw)
            aligned = abs(yaw_err) < self._config.align_yaw_tolerance_rad
            if aligned:
                self._align_ticks += 1
            else:
                self._align_ticks = 0

            hold_ok = (
                self._global_config is None
                or not self._global_config.simulation
                or self._align_ticks >= _SIM_ALIGN_HOLD_TICKS
            )
            if aligned and hold_ok:
                self._set_phase(StairPhase.ON_STAIR)
                self._sport.enter_stair_mode()
                logger.info("Entering ON_STAIR — Sport gait configured for climbing")

            align_forward = 0.0
            if abs(yaw_err) < math.radians(18.0):
                align_forward = self._config.min_linear_x * 0.4
            twist = twist_along_corridor(
                align_forward,
                robot_yaw,
                corridor,
                self._config,
                yaw_gain=self._config.yaw_gain_align,
                on_stair=False,
                sim_min_forward_ratio=self._sim_forward_floor(),
            )
            return self._apply_hardware_motion(twist)

        if self._phase == StairPhase.ON_STAIR:
            # Let Sport SHM + policy settle before forward drive.
            if self._on_stair_ticks < self._config.sim_on_stair_settle_ticks:
                return self._apply_hardware_motion(Twist())

            if (
                self._global_config is not None
                and self._global_config.simulation
                and self._on_stair_ticks == self._config.sim_on_stair_settle_ticks
            ):
                logger.info(
                    "ON_STAIR driving — cmd_vel active after WalkStair settle",
                    settle_ticks=self._config.sim_on_stair_settle_ticks,
                )

            self._advance_path_index(odom)
            progress = self._climb_progress(odom)
            if (
                self._global_config is not None
                and self._global_config.simulation
                and self._on_stair_ticks % 30 == 0
            ):
                along = self._along_corridor_m(odom)
                if abs(along - self._stuck_along_m) < 0.04:
                    self._stuck_ticks += 30
                else:
                    self._stuck_ticks = 0
                    self._stuck_along_m = along
                if self._stuck_ticks >= 150:
                    if self._sim_unstuck_boost < 4:
                        self._sim_unstuck_boost += 1
                    logger.warning(
                        "ON_STAIR progress stalled — boosting forward drive",
                        progress=round(progress, 2),
                        along_m=round(along, 2),
                        odom_z=round(odom.position.z, 3),
                        sim_boost=self._sim_unstuck_boost,
                    )
                    self._stuck_ticks = 0
            if self._stair_traversal_complete(odom):
                logger.info(
                    "Stair centerline climb complete",
                    progress=round(progress, 3),
                    along_m=round(self._along_corridor_m(odom), 2),
                )
                self._set_phase(StairPhase.EXIT)
            speed = self._target_speed_mps()
            if self._global_config is not None and self._global_config.simulation:
                if self._sim_unstuck_boost > 0:
                    speed = min(
                        self._config.max_linear_x * (1.0 + 0.12 * self._sim_unstuck_boost),
                        0.26,
                    )
                ticks_since_drive = self._on_stair_ticks - self._config.sim_on_stair_settle_ticks
                if 0 <= ticks_since_drive < _SIM_ON_STAIR_RAMP_TICKS:
                    ramp = ticks_since_drive / float(_SIM_ON_STAIR_RAMP_TICKS)
                    speed *= 0.25 + 0.75 * ramp
            twist = twist_along_corridor(
                speed,
                robot_yaw,
                corridor,
                self._config,
                yaw_gain=self._config.yaw_gain_on_stair,
                on_stair=True,
                sim_min_forward_ratio=self._sim_forward_floor(),
            )
            return self._apply_hardware_motion(twist)

        if self._phase == StairPhase.EXIT:
            self._sport.exit_stair_mode()
            self._finished = True
            self._phase = StairPhase.IDLE
            logger.info("Stair locomotion finished")
            return Twist()

        return Twist()
