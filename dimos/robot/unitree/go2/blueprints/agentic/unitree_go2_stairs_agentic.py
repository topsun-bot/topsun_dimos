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

"""Go2 stair stack + agent/MCP + locomotion skills (simulation stair climbing).

Does not use ``_common_agentic`` (avoids PersonFollow / hydra). Install::

    uv sync --extra go2-sim
    # go2-sim includes sim + agents (httpx/langchain) + web (McpServer)

Example::

    dimos --simulation --viewer rerun-web run unitree-go2-stairs-agentic
    dimos mcp call climb_stairs
    dimos agent-send "先 free_walk 再 climb_stairs"
    dimos mcp call move --arg x=0.2 --arg duration=1.0
"""

from dimos.agents.mcp.mcp_client import McpClient
from dimos.agents.mcp.mcp_server import McpServer
from dimos.agents.system_prompt import SYSTEM_PROMPT
from dimos.core.coordination.blueprints import autoconnect
from dimos.robot.unitree.go2.blueprints.agentic._stairs_agentic import _stairs_agentic
from dimos.robot.unitree.go2.blueprints.smart.unitree_go2_stairs import unitree_go2_stairs
from dimos.robot.unitree.go2.go2_stair_system_prompt import GO2_STAIR_SKILLS_APPENDIX

unitree_go2_stairs_agentic = autoconnect(
    unitree_go2_stairs,
    McpServer.blueprint(),
    McpClient.blueprint(system_prompt=SYSTEM_PROMPT + GO2_STAIR_SKILLS_APPENDIX),
    _stairs_agentic,
)

__all__ = ["unitree_go2_stairs_agentic"]
