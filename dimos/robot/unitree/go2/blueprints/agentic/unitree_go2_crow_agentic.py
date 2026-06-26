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

"""Unitree Go2 agentic blueprint with CROW as the NL input source.

Replaces WebInput/HumanCLI with CrowAgent so the robot can be driven
autonomously by a CROW OrchestratorAgent without a human terminal.

Start dimos:
    dimos run unitree-go2-crow-agentic

Start CROW with dimos bridge:
    cd ~/Projects/crow && python main.py --dimos http://127.0.0.1:9990/mcp
"""

from dimos.agents.crow_agent import CrowAgent
from dimos.agents.mcp.mcp_client import McpClient
from dimos.agents.mcp.mcp_server import McpServer
from dimos.agents.skills.navigation import NavigationSkillContainer
from dimos.agents.skills.person_follow import PersonFollowSkillContainer
from dimos.agents.skills.speak_skill import SpeakSkill
from dimos.core.coordination.blueprints import autoconnect
from dimos.robot.unitree.go2.blueprints.smart.unitree_go2_spatial import unitree_go2_spatial
from dimos.robot.unitree.go2.connection import GO2Connection
from dimos.robot.unitree.unitree_skill_container import UnitreeSkillContainer

unitree_go2_crow_agentic = autoconnect(
    unitree_go2_spatial,
    NavigationSkillContainer.blueprint(),
    PersonFollowSkillContainer.blueprint(camera_info=GO2Connection.camera_info_static),
    UnitreeSkillContainer.blueprint(),
    SpeakSkill.blueprint(),
    CrowAgent.blueprint(),  # replaces WebInput — receives commands from CROW
    McpServer.blueprint(),
    McpClient.blueprint(),
)

__all__ = ["unitree_go2_crow_agentic"]
