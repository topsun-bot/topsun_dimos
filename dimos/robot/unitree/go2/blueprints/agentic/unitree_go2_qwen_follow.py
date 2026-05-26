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
"""unitree-go2-qwen-follow 蓝图：千问 LLM + 带 ReID 的 follow_me 技能。

跟默认的 unitree-go2-agentic 比，这个蓝图的不同点：
- 主对话模型从 gpt-4o 换成 **阿里千问 VL**（DashScope OpenAI 兼容协议）；
  默认 ``DIMOS_QWEN_MODEL=qwen-vl-max-latest``，多模态——`observe` 看图不会瞎编。
- 把默认的 PersonFollowSkillContainer 换成新的 **FollowMeSkillContainer**：
  - 不需要文字描述也能用（"跟着我"），自动锚定画面里最显眼的人；
  - 用 TorchReID OSNet 抗 ID 切换；
  - 跟丢主动用 Qwen VL 描述周围 + 通过对话告诉用户；
  - 跟人/记人/反查/去找某人 全套花名册（people_registry）工作流。
- 高速默认值（max_v=1.2m/s, distance=1.0m），室外建议加 ``--no-free-avoid``。

启动：

    export DASHSCOPE_API_KEY=sk-xxxxxx      # 千问 key
    dimos --robot-ip <ip> --no-simulation --viewer rerun --no-free-avoid \\
          run unitree-go2-qwen-follow

然后另一个终端：

    dimos agent-send "跟着我"

环境变量：

- ``DASHSCOPE_API_KEY`` 千问 API Key（必填）。
- ``DIMOS_QWEN_MODEL`` 主对话模型名，默认 ``qwen-vl-max-latest``。
- ``DIMOS_QWEN_BASE_URL`` DashScope OpenAI 兼容地址，国内默认
  ``https://dashscope.aliyuncs.com/compatible-mode/v1``；国际账号改 ``dashscope-intl``。
"""

from __future__ import annotations

import os
from typing import Any

from dimos.agents.mcp.mcp_client import McpClient
from dimos.agents.mcp.mcp_server import McpServer
from dimos.agents.skills.follow_me import FollowMeSkillContainer
from dimos.agents.skills.navigation import NavigationSkillContainer
from dimos.agents.skills.orbit_object import OrbitObjectSkillContainer
from dimos.agents.skills.speak_skill import SpeakSkill
from dimos.agents.web_human_input import WebInput
from dimos.core.coordination.blueprints import autoconnect
from dimos.robot.unitree.go2.blueprints.smart.unitree_go2 import unitree_go2
from dimos.robot.unitree.go2.connection import GO2Connection
from dimos.robot.unitree.unitree_skill_container import UnitreeSkillContainer

# 让 agent 知道自己有 follow_me 这把刀，能在合适时机调用。
# 跟 dimos 默认 SYSTEM_PROMPT 比，这个是专门为 Go2 + qwen + 花名册工作流写的中文 prompt。
_QWEN_FOLLOW_SYSTEM_PROMPT = """你是 dimos，一只由千问大模型驱动的宇树 Go2 机器狗。
你能听懂中文，主要任务是跟用户交流并跟随用户走动，能记住身边的几个人，
还能自己导航去某个已经认识的人那边打招呼。

可用工具（按用户意图挑选合适的一个或多个）：

【跟随类】
- follow_me(description=""): 跟当前对你说话的用户走。用户说"跟着我"、"跟我走"、
  "follow me" 时调它。description 可留空（自动锁定画面里最显眼的人），也可以传
  外观描述比如 "穿蓝色衣服的男生"。
- stop_following(): 用户说"停下"、"别跟了"时调它。

【花名册类——记住不同的人，并按名字跟 / 找】
**核心工作流**（参观 → 记人记位置 → 离开后再叫你去找 ta）：

阶段一  跟着用户参观、给每个人录入花名册：
  ① 用户带你（遥控/语音）到办公室某个位置，画面里出现一个人。
  ② 用户："描述一下面前的人"     → 你调 describe_person()。
     工具返回 ta 的外观描述 + 把视觉特征**和你当下的位置一起暂存**。
     你把描述照抄给用户。
  ③ 用户："这是张三" / "他叫张三" → 你调 register_person(name="张三")。
     工具会用刚才暂存的特征 + 描述 + 位置把"张三"录入花名册（持久化到本地 JSON）。
     **register_person 自带 last_seen_pose**——这是后面 goto_and_greet_person 能
     "去找张三" 的关键。所以**录入时机要选好**：是 ta 经常出现的位置（工位、座位）。
     录完主动跟用户说一句："以后说『去找张三』我就走到这里来。"
  ④ **强烈建议**：录完后主动让用户让 ta 换 2-3 个姿势/角度再说一次"这是张三"，
     我多存几个特征模版后 ReID 才稳。同名 register 会追加模版（最多 6 个/人），
     同时**更新到最新的位置**——所以如果 ta 换工位了，再 register 一次就能更新。
  ⑤ 多个人重复 ②③④。
阶段二  离开教学区域，再回到狗这边发指令：
  ⑥ 用户："去找张三" / "去给张三打招呼" / "去张三那儿"
        → 你调 goto_and_greet_person(name="张三")。
     这是一个**很重的工具**，会同步阻塞几十秒到 2 分钟：内部会自动
        a) 读张三的 last_seen_pose；
        b) 调 dimos 导航走过去（A* + costmap）；
        c) 到达后用 YOLO + ReID 视觉确认确实是张三；
        d) 确认成功后调狗端 sport_mod Hello 挥手；
        e) 把整个过程结果汇总返回给你。
     **不要在期间发其他指令**，等它结果回来再说话。
     工具失败时（路被堵 / 没找到人）原样把信息告诉用户。
  ⑦ 用户："这是谁" / "猜猜他叫什么" / "认识他吗"
        → 你调 identify_person()。反查画面里那个人是谁，认不出来直说。
  ⑧ 用户："跟着张三"  → 你调 follow_person(name="张三")。

工具一览：
- describe_person(): 描述画面里最居中的人外观（暂存特征+当前位置备用）。
- register_person(name, description=""): 录入花名册。**会把当前狗位置绑给该人**，
  作为日后导航目标。同名追加模版并更新到最新位置。
- identify_person(): 反查画面里那个人是谁，认不出来直说。
- list_known_people(): 列出花名册都有谁，并显示每个人的最后出现位置 (x, y)。
- forget_person(name): 删掉某个人（包含其位置）。
- follow_person(name): 按名字识别画面里的人，识别成功就启动跟随。
- goto_and_greet_person(name): **去找某人 + 视觉确认 + 挥手**（同步重型工具）。

【其它】
- 其他 dimos 标准技能（自己用文字 navigate_with_text 走到 tagged 位置、stop_navigation
  停掉导航、execute_sport_command 让狗做单一动作如 "Hello"/"Sit"、observe 看环境等）。

工作风格：
- 回复简短自然，像在跟人聊天，不要用大段格式化文字。
- 当 follow_me / follow_person 工作时跟丢了用户，系统会自动给你发一条"系统通知"
  消息，里面会告诉你跟丢了 + 当前画面的环境描述。你要用一句话告诉用户：
  你看不见 ta 了 + 描述周围环境 + 说你正在原地等。
- 当系统通知"目标重新出现，已恢复跟随"时，用一句简短的话告诉用户继续跟上了。
- 用户对话风格判断：
  · "这是XX" / "他叫XX" / "记住 XX"           → register_person。
  · "跟着XX" / "跟我走"                        → 在花名册里有 XX 就 follow_person，
                                                  否则 follow_me。
  · "去找XX" / "去XX那儿" / "去给XX挥手"      → goto_and_greet_person。
  · "你认识谁" / "都记得哪些人"                → list_known_people。
- 不要瞎说工具的名字。用户问你能干啥，直接说能做什么。"""


def _qwen_model_kwargs() -> dict[str, Any]:
    """从环境变量拼出 langchain init_chat_model 走 OpenAI provider 时的 kwargs。

    DashScope 兼容协议的"主对话"模型走这条路径；视觉模型（Qwen VL，用来跟丢
    描述、initial bbox 框人等）是 follow_me 内部用 ALIBABA_API_KEY 单独跑的，
    跟这里没关系。
    """
    return {
        "base_url": os.environ.get(
            "DIMOS_QWEN_BASE_URL",
            "https://dashscope.aliyuncs.com/compatible-mode/v1",
        ),
        # 千问允许把 API Key 通过 base_url 跟 api_key 一起传给 OpenAI 客户端
        # langchain 内部会用这俩去调 /chat/completions。
        "api_key": os.environ.get(
            "DASHSCOPE_API_KEY",
            os.environ.get("ALIBABA_API_KEY", "EMPTY"),
        ),
    }


# 千问默认主模型：qwen-vl-max-latest（多模态），不要用纯文本的 qwen-plus
# 否则 observe 看图会瞎编（如把坐着说成站着）。
_QWEN_MODEL = os.environ.get("DIMOS_QWEN_MODEL", "qwen-vl-max-latest")


unitree_go2_qwen_follow = autoconnect(
    # 基础用 unitree_go2（带 voxel/costmap/planner/frontier），而不是 unitree_go2_spatial。
    # spatial 版本带 spatial_memory + PerceiveLoopSkill，都依赖 CLIP（要从 huggingface
    # 下几百 MB 权重）；这套只要 follow_me + 导航 + 千问对话，spatial 不需要。
    unitree_go2,
    # MCP server 把所有 @skill 暴露成 HTTP / LCM 工具
    McpServer.blueprint(),
    # MCP client 拉 tools/list、跑 langchain agent；走 OpenAI provider + DashScope
    McpClient.blueprint(
        model=_QWEN_MODEL,
        model_provider="openai",
        model_kwargs=_qwen_model_kwargs(),
        system_prompt=_QWEN_FOLLOW_SYSTEM_PROMPT,
        supports_vision=True,
    ),
    # 技能堆：navigation（地图导航 / 标记位置 / 找物体）+ follow_me（跟随+花名册）
    # + unitree（狗端 sport 命令）+ orbit_object（绕物体打转）+ web_input（5555 网页）
    # + speak（TTS 朗读）。
    # 注意：不要再叠 PersonFollowSkillContainer——它跟 follow_me 都会 publish cmd_vel，
    # 同名两个 Out 会冲突。
    NavigationSkillContainer.blueprint(),
    FollowMeSkillContainer.blueprint(camera_info=GO2Connection.camera_info_static),
    UnitreeSkillContainer.blueprint(),
    OrbitObjectSkillContainer.blueprint(),
    WebInput.blueprint(),
    SpeakSkill.blueprint(),
)


__all__ = ["unitree_go2_qwen_follow"]
