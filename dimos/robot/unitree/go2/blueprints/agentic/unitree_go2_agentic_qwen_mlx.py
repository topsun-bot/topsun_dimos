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

"""
Go2 agentic blueprint using a local Qwen MLX OpenAI-compatible server.

Defaults to a server on ``http://127.0.0.1:8080`` and the portable model label
``Qwen3.6-27B-mlx-bf16``. If a deployment needs a remote host or an absolute
local model path, set ``DIMOS_QWEN_MLX_BASE_URL`` and ``DIMOS_QWEN_MLX_MODEL``.
"""

from dimos.agents.mcp.mcp_client import McpClient
from dimos.agents.mcp.mcp_server import McpServer
from dimos.agents.qwen_mlx_agent import (
    qwen_mlx_model_kwargs,
    qwen_mlx_model_name,
)
from dimos.core.coordination.blueprints import autoconnect
from dimos.robot.unitree.go2.blueprints.agentic._common_agentic import _common_agentic
from dimos.robot.unitree.go2.blueprints.smart.unitree_go2_spatial import unitree_go2_spatial

unitree_go2_agentic_qwen_mlx = autoconnect(
    unitree_go2_spatial,
    McpServer.blueprint(),
    McpClient.blueprint(
        model=qwen_mlx_model_name(),
        model_provider="openai",
        model_kwargs=qwen_mlx_model_kwargs(),
        supports_vision=False,
    ),
    _common_agentic,
)

__all__ = ["unitree_go2_agentic_qwen_mlx"]
