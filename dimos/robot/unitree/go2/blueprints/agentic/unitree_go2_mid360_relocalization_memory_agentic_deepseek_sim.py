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

"""MuJoCo validation blueprint for Mid360 spatial memory with DeepSeek.

Usage::

    dimos --simulation mujoco run \
      unitree-go2-mid360-relocalization-memory-agentic-deepseek-sim \
      --disable security-module
"""

from dimos.agents.mcp.mcp_client import McpClient
from dimos.agents.mcp.mcp_server import McpServer
from dimos.agents.skills.navigation import NavigationSkillContainer
from dimos.core.coordination.blueprints import autoconnect
from dimos.robot.unitree.go2.blueprints.agentic._common_agentic import _common_agentic
from dimos.robot.unitree.go2.blueprints.smart.unitree_go2_mid360 import (
    unitree_go2_mid360_relocalization_memory_sim,
)
from dimos.robot.unitree.go2.go2_mid360_recorder import Go2Mid360NavigationRecorder
from dimos.robot.unitree.unitree_skill_container import UnitreeSkillContainer

unitree_go2_mid360_relocalization_memory_agentic_deepseek_sim = autoconnect(
    unitree_go2_mid360_relocalization_memory_sim,
    McpServer.blueprint(),
    McpClient.blueprint(
        model="deepseek-v4-pro",
        model_provider="openai",
        model_kwargs={"extra_body": {"thinking": {"type": "disabled"}}},
        supports_vision=False,
    ),
    _common_agentic,
)

# Deterministic simulation entrypoint for exercising the exact same navigation
# skills through MCP when DeepSeek credentials are intentionally unavailable.
# It excludes McpClient only; the navigation, memory, VLM, and movement skill
# implementations are identical to those in the DeepSeek blueprint above.
unitree_go2_mid360_relocalization_memory_skills_sim = autoconnect(
    unitree_go2_mid360_relocalization_memory_sim,
    McpServer.blueprint(),
    NavigationSkillContainer.blueprint(),
    UnitreeSkillContainer.blueprint(),
)

# Same deterministic skill stack with a memory2 recorder attached to the exact
# adapted lidar/odom streams used by navigation. It is intended for generating
# and replaying a MuJoCo premap before the corresponding real-robot test.
unitree_go2_mid360_relocalization_memory_record_skills_sim = autoconnect(
    unitree_go2_mid360_relocalization_memory_sim,
    Go2Mid360NavigationRecorder.blueprint(),
    McpServer.blueprint(),
    NavigationSkillContainer.blueprint(),
    UnitreeSkillContainer.blueprint(),
)
