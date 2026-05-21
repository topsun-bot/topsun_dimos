# Copyright 2026 Dimensional Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Go2 locomotion skills: cmd_vel walking and SportClient stair modes."""

from __future__ import annotations

from typing import Any

from dimos.agents.annotation import skill
from dimos.core.core import rpc
from dimos.core.module import Module
from dimos.msgs.geometry_msgs.PoseStamped import PoseStamped
from dimos.msgs.geometry_msgs.Quaternion import Quaternion
from dimos.msgs.geometry_msgs.Twist import Twist
from dimos.msgs.geometry_msgs.Vector3 import Vector3
from dimos.navigation.navigation_spec import NavigationInterfaceSpec
from dimos.robot.unitree.go2.connection_spec import GO2ConnectionSpec
from dimos.robot.unitree.go2.stair_locomotion.config import StairLocomotionConfig
from dimos.robot.unitree.go2.stair_locomotion.sport_api import Go2StairSportController
from dimos.simulation.mujoco.build_scene_stairs import stair_top_goal_xy
from dimos.utils.logging_config import setup_logger

logger = setup_logger()


class Go2LocomotionSkillContainer(Module):
    """Basic Go2 movement via cmd_vel and Sport API (including WalkStair for stairs)."""

    _connection: GO2ConnectionSpec
    _navigation: NavigationInterfaceSpec
    _sport: Go2StairSportController | None = None

    @rpc
    def start(self) -> None:
        super().start()
        self._sport = Go2StairSportController(
            self._connection,
            StairLocomotionConfig(),
            global_config=self.config.g,
        )

    @rpc
    def stop(self) -> None:
        if self._sport is not None:
            self._sport.exit_stair_mode()
            self._sport = None
        super().stop()

    @skill
    def move(self, x: float, y: float = 0.0, yaw: float = 0.0, duration: float = 0.0) -> str:
        """Move using body-frame velocity commands (FreeWalk / cmd_vel).

        Use for short manual drives. For stair ascent in simulation, prefer ``climb_stairs``.

        Args:
            x: Forward velocity in m/s (positive = forward).
            y: Lateral velocity in m/s (positive = left).
            yaw: Yaw rate in rad/s (positive = counter-clockwise).
            duration: Seconds to hold the command; 0 means until the next command.
        """
        twist = Twist(linear=Vector3(x, y, 0.0), angular=Vector3(0.0, 0.0, yaw))
        self._connection.move(twist, duration=duration)
        return (
            f"cmd_vel linear=({x:.2f}, {y:.2f}) angular_z={yaw:.2f} "
            f"duration={duration:.1f}s"
        )

    @skill
    def stop_motion(self) -> str:
        """Stop all motion (zero cmd_vel)."""
        self._connection.move(Twist(), duration=0.0)
        return "Zero velocity command sent."

    @skill
    def stop_navigation(self) -> str:
        """Cancel the active navigation goal and stop the planner."""
        self._navigation.cancel_goal()
        self._connection.move(Twist(), duration=0.0)
        return "Navigation goal cancelled and velocity cleared."

    @skill
    def balance_stand(self) -> str:
        """Enter BalanceStand before switching locomotion modes."""
        ok = self._connection.balance_stand()
        return "BalanceStand sent." if ok else "BalanceStand request failed."

    @skill
    def free_walk(self) -> str:
        """Enable FreeWalk so cmd_vel velocity commands are accepted."""
        ok = self._connection.free_walk()
        return "FreeWalk enabled." if ok else "FreeWalk request failed."

    @skill
    def walk_stair(self, enabled: bool = True) -> str:
        """Enable or disable official Go2 WalkStair sport mode (API 1049).

        Call with ``enabled=True`` before climbing stairs on hardware or in MuJoCo sim.
        Call ``walk_stair(False)`` when finished.

        Args:
            enabled: True to enter WalkStair, False to exit.
        """
        if self._sport is None:
            return "Locomotion skill container is not started."

        if enabled:
            self._sport.enter_stair_mode()
            return "WalkStair sport mode enabled (BalanceStand + FreeWalk + WalkStair)."
        self._sport.exit_stair_mode()
        return "WalkStair sport mode disabled."

    @skill
    def climb_stairs(self) -> str:
        """Climb the straight stair run: WalkStair on + navigation goal at the top tread.

        Works in ``unitree-go2-stairs`` simulation (and on hardware when stair corridor
        is detected). Ensure ``stair_navigation`` is enabled and wait for mapping /
        ``Stair corridor armed`` in logs if planning fails.

        Returns immediately; use ``stop_navigation`` to cancel.
        """
        if self._sport is None:
            return "Locomotion skill container is not started."

        self._sport.enter_stair_mode()
        goal_x, goal_y = stair_top_goal_xy()
        goal = PoseStamped(
            position=Vector3(goal_x, goal_y, 0.0),
            orientation=Quaternion.from_euler(Vector3(0.0, 0.0, 0.0)),
            frame_id="world",
        )
        self._navigation.set_goal(goal)
        logger.info("climb_stairs goal", x=round(goal_x, 2), y=round(goal_y, 2))
        return (
            f"WalkStair enabled; navigation goal set to stair top ({goal_x:.2f}, {goal_y:.2f}). "
            "Monitor logs for corridor planning. Cancel with stop_navigation."
        )


go2_locomotion_skill_container = Go2LocomotionSkillContainer.blueprint

__all__ = ["Go2LocomotionSkillContainer", "go2_locomotion_skill_container"]
