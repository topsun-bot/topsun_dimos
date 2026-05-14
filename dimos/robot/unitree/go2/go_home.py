#!/usr/bin/env python3
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

from dimos_lcm.std_msgs import Bool

from dimos.core.core import rpc
from dimos.core.module import Module
from dimos.core.stream import In, Out
from dimos.msgs.geometry_msgs.PoseStamped import PoseStamped
from dimos.utils.logging_config import setup_logger

logger = setup_logger()


class GoHome(Module):
    """Record the robot's starting pose on startup and navigate back to it on demand."""

    odom: In[PoseStamped]
    goal_reached: In[Bool]
    goal_request: Out[PoseStamped]

    _home_pose: PoseStamped | None = None

    async def handle_odom(self, msg: PoseStamped) -> None:
        if self._home_pose is not None:
            return
        self._home_pose = msg
        logger.info(f"Home pose recorded: {msg}")

    async def handle_goal_reached(self, msg: Bool) -> None:
        if msg.data:
            logger.info("Robot has arrived at home!")

    @rpc
    def go_home(self) -> str:
        """Navigate the robot back to its starting position.

        Publishes the initial odom pose to the planner's goal_request stream.
        Returns an error string if no home pose has been recorded yet.
        """
        if self._home_pose is None:
            return "Error: no home pose recorded yet. Wait for odometry data."
        self.goal_request.publish(self._home_pose)
        return f"Go home started: target={self._home_pose}"

    @rpc
    def is_home_pose_set(self) -> bool:
        """Check whether the home pose has been recorded from odometry."""
        return self._home_pose is not None
