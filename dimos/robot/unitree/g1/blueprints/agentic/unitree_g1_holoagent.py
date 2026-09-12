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

"""G1 agentic stack plus HoloAgent robot_bridge skills.

Requires a running HorizonRobotics HoloAgent ``robot_bridge`` for the
``holoagent_*`` navigation skills (default ``http://127.0.0.1:8000``).
Native DimOS G1 move / arm / navigation skills remain available.
HoloAgent ``/api/arm`` is not wired: use native ``execute_arm_command``.
"""

from dimos.agents.mcp.mcp_client import McpClient
from dimos.agents.skills.holoagent import HOLOAGENT_SKILLS_PROMPT, HoloAgentNavSkillContainer
from dimos.core.coordination.blueprints import autoconnect
from dimos.robot.unitree.g1.blueprints.agentic.unitree_g1_agentic import unitree_g1_agentic
from dimos.robot.unitree.g1.system_prompt import G1_SYSTEM_PROMPT

unitree_g1_holoagent = autoconnect(
    unitree_g1_agentic,
    # Later McpClient atom wins (same instance name) so the agent also
    # calls holoagent_stop_nav; native stop_all_motion does not cancel
    # a robot_bridge goal.
    McpClient.blueprint(system_prompt=G1_SYSTEM_PROMPT + HOLOAGENT_SKILLS_PROMPT),
    HoloAgentNavSkillContainer.blueprint(),
)

__all__ = ["unitree_g1_holoagent"]
