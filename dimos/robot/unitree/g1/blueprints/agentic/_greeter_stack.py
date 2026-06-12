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

"""Shared greeter blueprint pieces (imported by each ``unitree_g1_greeter*.py``)."""

from __future__ import annotations

from typing import Any

from dimos.agents.mcp.mcp_client import McpClient
from dimos.core.module import ModuleBase
from dimos.robot.unitree.g1.greeter_system_prompt import GREETER_SYSTEM_PROMPT
from dimos.spec.utils import Spec

# 临时关闭 LLM 开放对话;仅寒暄/手势/FAQ/未建图问路走模板。恢复时改 llm_enabled=True。
GREETER_ROUTER_KWARGS: dict[str, Any] = {"llm_enabled": False}

MCP_CLIENT_KWARGS: dict[str, Any] = {
    "system_prompt": GREETER_SYSTEM_PROMPT,
    "model": "deepseek-v4-pro",
    "model_provider": "openai",
    "model_kwargs": {
        "temperature": 0,
        "max_tokens": 120,
        "extra_body": {"thinking": {"type": "disabled"}},
    },
    "supports_vision": False,
}

GREETER_REMAPPINGS: list[tuple[type[ModuleBase], str, str | type[ModuleBase] | type[Spec]]] = [
    (McpClient, "human_input", "llm_human_input"),
]
