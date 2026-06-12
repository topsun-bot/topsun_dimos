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

"""G1 Orin 机载导览迎宾蓝图(阶段 A — 空壳)。

在 ``unitree_g1_nav_onboard``(FastLIO2 + nav_stack + MovementManager +
``G1HighLevelDdsSdk``)之上叠加笔记本版迎宾链路:``GreeterSkillContainer`` +
``SpeakSkill`` + ``GreeterIntentRouter`` + ``VadVoiceInput``,移动与手臂手势全部
走 DDS(无 WebRTC ``G1Connection``)。

对话策略与笔记本版一致:``llm_enabled=False``,仅 ``GreeterIntentRouter`` 模板
短路,模板外输入固定拒答。``McpServer`` / ``McpClient`` 保留以便日后扩展,但运行时
不向 LLM 转发用户输入。

用法(在 Orin SSH 会话中)::

    source .venv/bin/activate
    export LIDAR_HOST_IP=192.168.123.164
    export LIDAR_IP=192.168.123.120
    dimos run unitree-g1-greeter-onboard
"""

from dimos.agents.mcp.mcp_client import McpClient
from dimos.agents.mcp.mcp_server import McpServer
from dimos.agents.skills.speak_skill import SpeakSkill
from dimos.agents.voice_input import VadVoiceInput
from dimos.core.coordination.blueprints import autoconnect
from dimos.robot.unitree.g1.blueprints.agentic._greeter_stack import (
    GREETER_REMAPPINGS,
    GREETER_ROUTER_KWARGS,
    MCP_CLIENT_KWARGS,
)
from dimos.robot.unitree.g1.blueprints.navigation.unitree_g1_nav_onboard import (
    unitree_g1_nav_onboard,
)
from dimos.robot.unitree.g1.greeter_intent_router import GreeterIntentRouter
from dimos.robot.unitree.g1.greeter_skill import GreeterSkillContainer
from dimos.robot.unitree.g1.greeter_tour_skill import GreeterTourSkillContainer

# 与笔记本版相同的模板-only 策略(llm_enabled=False),额外开启地标问路/带路:
# 已标点 → 走地标表 + 导航;未标点名称仍固定拒答。讲解词只来自标点录入,不经 LLM。
GREETER_ONBOARD_ROUTER_KWARGS = {**GREETER_ROUTER_KWARGS, "location_landmarks_available": True}

unitree_g1_greeter_onboard = (
    autoconnect(
        # 机载导航栈:LiDAR 建图 + 导航 + 移动管理 + DDS 高层控制。
        unitree_g1_nav_onboard,
        # 迎宾链路(手臂手势经 DDS ``G1HighLevelDdsSdk.publish_request`` 下发)。
        GreeterSkillContainer.blueprint(),
        SpeakSkill.blueprint(),
        VadVoiceInput.blueprint(whisper_model="tiny"),
        GreeterIntentRouter.blueprint(**GREETER_ONBOARD_ROUTER_KWARGS),
        # 标点 + 导航带路 + 到站讲解(``goal`` 流接入 SimplePlanner)。
        GreeterTourSkillContainer.blueprint(),
        McpServer.blueprint(),
        McpClient.blueprint(**MCP_CLIENT_KWARGS),
    )
    .remappings(GREETER_REMAPPINGS)
    .global_config(n_workers=20, robot_model="unitree_g1")
)

__all__ = ["unitree_g1_greeter_onboard"]
