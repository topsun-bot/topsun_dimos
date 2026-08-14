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

"""Mid360 Go2 navigation and spatial memory using the DeepSeek agent.

This is the Mid360 counterpart of ``unitree-go2-agentic-deepseek``. It keeps
ordinary live mapping/navigation and spatial memory, but does not load a saved
map or add relocalization. The same public blueprint name supports both modes::

    dimos run unitree-go2-mid360-agentic-deepseek
    dimos --simulation run unitree-go2-mid360-agentic-deepseek
"""

from dimos.agents.mcp.mcp_client import McpClient
from dimos.agents.mcp.mcp_server import McpServer
from dimos.core.coordination.blueprints import autoconnect
from dimos.robot.unitree.go2.blueprints.agentic._common_agentic import _common_agentic
from dimos.robot.unitree.go2.blueprints.smart.unitree_go2_mid360 import (
    unitree_go2_mid360_spatial,
)

unitree_go2_mid360_agentic_deepseek = autoconnect(
    unitree_go2_mid360_spatial,
    McpServer.blueprint(),
    McpClient.blueprint(
        model="deepseek-v4-pro",
        model_provider="openai",
        model_kwargs={"extra_body": {"thinking": {"type": "disabled"}}},
        supports_vision=False,
    ),
    _common_agentic,
)
