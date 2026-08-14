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

"""Mid360-backed Go2 relocalization and spatial memory with DeepSeek agent.

The same public blueprint name selects the real Mid360/Point-LIO source or the
MuJoCo adapter according to the global ``--simulation`` flag.

Usage::

    export DIMOS_POINTLIO_LIDAR_IP=192.168.123.20
    export DIMOS_POINTLIO_HOST_IP=192.168.123.18
    dimos --robot-ip 192.168.123.161 run \
      unitree-go2-mid360-relocalization-memory-agentic-deepseek \
      --disable security-module \
      -o relocalizationmodule.map_file=<premap>

    dimos --simulation run \
      unitree-go2-mid360-relocalization-memory-agentic-deepseek \
      --disable security-module \
      -o relocalizationmodule.map_file=<premap>
"""

from dimos.agents.mcp.mcp_client import McpClient
from dimos.agents.mcp.mcp_server import McpServer
from dimos.core.coordination.blueprints import autoconnect
from dimos.robot.unitree.go2.blueprints.agentic._common_agentic import _common_agentic
from dimos.robot.unitree.go2.blueprints.smart.unitree_go2_mid360 import (
    unitree_go2_mid360_relocalization_memory,
)

unitree_go2_mid360_relocalization_memory_agentic_deepseek = autoconnect(
    unitree_go2_mid360_relocalization_memory,
    McpServer.blueprint(),
    McpClient.blueprint(
        model="deepseek-v4-pro",
        model_provider="openai",
        model_kwargs={"extra_body": {"thinking": {"type": "disabled"}}},
        supports_vision=False,
    ),
    _common_agentic,
)
