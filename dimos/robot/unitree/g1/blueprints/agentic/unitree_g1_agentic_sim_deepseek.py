#!/usr/bin/env python3
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

"""Agentic G1 sim stack with DeepSeek V4 Pro."""

from dimos.agents.mcp.mcp_client import McpClient
from dimos.agents.mcp.mcp_server import McpServer
from dimos.agents.skills.door_navigation import DoorNavigationSkill
from dimos.agents.skills.navigation import NavigationSkillContainer
from dimos.agents.skills.speak_skill import SpeakSkill
from dimos.core.coordination.blueprints import autoconnect
from dimos.robot.unitree.g1.blueprints.perceptive.unitree_g1_sim import unitree_g1_sim
from dimos.robot.unitree.g1.skill_container import UnitreeG1SkillContainer
from dimos.robot.unitree.g1.system_prompt import G1_SYSTEM_PROMPT

unitree_g1_agentic_sim_deepseek = autoconnect(
    unitree_g1_sim,
    McpServer.blueprint(),
    McpClient.blueprint(
        system_prompt=G1_SYSTEM_PROMPT,
        model="deepseek-v4-pro",
        model_provider="openai",
        model_kwargs={"model_kwargs": {"extra_body": {"thinking": {"type": "none"}}}},
    ),
    NavigationSkillContainer.blueprint(),
    DoorNavigationSkill.blueprint(),
    SpeakSkill.blueprint(),
    UnitreeG1SkillContainer.blueprint(),
)

__all__ = ["unitree_g1_agentic_sim_deepseek"]
