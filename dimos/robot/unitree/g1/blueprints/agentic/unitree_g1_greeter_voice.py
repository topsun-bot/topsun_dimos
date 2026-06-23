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

"""带语音输入的 G1 迎宾蓝图(按 Enter 录音)。

说「你好」「再见」等短句走固定模板(先说话后挥手)。当前 LLM 已关闭,模板外问题固定拒答。

用法::

    dimos --robot-ip 10.10.196.17 --unitree-cloud-region cn run unitree-g1-greeter-voice
    # 前台运行,按 Enter 开始/结束录音
"""

from dimos.agents.mcp.mcp_client import McpClient
from dimos.agents.mcp.mcp_server import McpServer
from dimos.agents.skills.speak_skill import SpeakSkill
from dimos.agents.voice_input import VoiceInput
from dimos.core.coordination.blueprints import autoconnect
from dimos.robot.unitree.g1.blueprints.agentic._greeter_stack import (
    GREETER_REMAPPINGS,
    GREETER_ROUTER_KWARGS,
    MCP_CLIENT_KWARGS,
)
from dimos.robot.unitree.g1.connection import G1Connection
from dimos.robot.unitree.g1.greeter_intent_router import GreeterIntentRouter
from dimos.robot.unitree.g1.greeter_skill import GreeterSkillContainer

unitree_g1_greeter_voice = (
    autoconnect(
        G1Connection.blueprint(),
        GreeterSkillContainer.blueprint(),
        SpeakSkill.blueprint(),
        VoiceInput.blueprint(whisper_model="tiny"),
        GreeterIntentRouter.blueprint(**GREETER_ROUTER_KWARGS),
        McpServer.blueprint(),
        McpClient.blueprint(**MCP_CLIENT_KWARGS),
    )
    .remappings(GREETER_REMAPPINGS)
    .global_config(n_workers=6, robot_model="unitree_g1")
)

__all__ = ["unitree_g1_greeter_voice"]
