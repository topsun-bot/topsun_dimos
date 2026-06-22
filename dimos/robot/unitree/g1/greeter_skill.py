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

"""宇树 G1 的无移动迎宾手势技能。

提供一组友好的手臂手势技能,供笔记本友好的 ``unitree_g1_greeter`` 蓝图使用:
机器人站立不动,仅通过语音(``SpeakSkill``)和手臂手势与人互动。所有手势都走
WebRTC 连接(``G1ConnectionSpec.publish_request``),因此本容器无需本地相机或
激光雷达即可工作——非常适合在上机部署前,先在笔记本上开发迎宾/接待流程。
"""

from pathlib import Path
import random
import threading
import time
from typing import Literal

from dimos.agents.annotation import skill
from dimos.agents.skills.speak_skill_spec import SpeakSkillSpec
from dimos.core.core import rpc
from dimos.core.module import Module, ModuleConfig
from dimos.robot.unitree.g1.connection_spec import G1ConnectionSpec
from dimos.robot.unitree.g1.effectors.high_level.commands import (
    ARM_API_ID,
    ARM_COMMANDS,
    ARM_EXECUTE_CUSTOM_ACTION_API_ID,
    ARM_GET_ACTION_LIST_API_ID,
    ARM_STOP_CUSTOM_ACTION_API_ID,
    ARM_TOPIC,
    execute_g1_command,
)
from dimos.utils.logging_config import setup_logger

logger = setup_logger()

# 手势序列,元素为 (命令名, 之后停顿秒数)。停顿是为了让机器人上一个预设手臂
# 动作播放完毕,再发送下一个。
# 手势命令发出后即可返回;短停顿仅用于日志上区分动作,不阻塞后续 speak。
_WELCOME_SEQUENCE: list[tuple[str, float]] = [
    ("Handshake", 0.5),
]
_GOODBYE_SEQUENCE: list[tuple[str, float]] = [
    ("HighWave", 0.5),
]

# 笔记本 WebRTC 开发:固定手臂小舞步(腿不动)。Orin 上改用 UniStore 全身舞。
_WEBRTC_ARM_DANCE_SEQUENCE: list[tuple[str, float]] = [
    ("Clap", 0.8),
    ("HighWave", 0.8),
    ("ArmHeart", 0.8),
    ("HandsUp", 0.5),
]

DanceStyle = Literal["auto", "webrtc_arms", "onboard_random"]


def is_onboard_compute() -> bool:
    """True when DimOS runs on the robot's Jetson (Orin NX, etc.)."""
    return Path("/etc/nv_tegra_release").exists()


def resolve_dance_style(
    dance_style: DanceStyle,
    unitree_connection_type: str,
    onboard_compute: bool,
) -> Literal["webrtc_arms", "onboard_random"]:
    """Pick dance mode: laptop WebRTC → arm-only; Orin → random full-body."""
    if dance_style == "webrtc_arms":
        return "webrtc_arms"
    if dance_style == "onboard_random":
        return "onboard_random"
    if onboard_compute:
        return "onboard_random"
    if unitree_connection_type == "webrtc":
        return "webrtc_arms"
    return "onboard_random"


class GreeterSkillConfig(ModuleConfig):
    """迎宾技能配置。舞蹈名可用 ``list_actions`` 核对后填入。"""

    dance_style: DanceStyle = "auto"
    dance_names: tuple[str, ...] = ("Waist_Drum_Dance",)


class GreeterSkillContainer(Module):
    """G1 迎宾手臂手势技能容器(无移动)。"""

    config: GreeterSkillConfig

    _connection: G1ConnectionSpec
    _speak_skill: SpeakSkillSpec

    @rpc
    def start(self) -> None:
        super().start()

    @rpc
    def stop(self) -> None:
        super().stop()

    def _run_sequence(self, sequence: list[tuple[str, float]]) -> str:
        results: list[str] = []
        for command_name, pause in sequence:
            results.append(
                execute_g1_command(
                    self._connection.publish_request,
                    ARM_COMMANDS,
                    ARM_API_ID,
                    ARM_TOPIC,
                    command_name,
                    logger=logger,
                )
            )
            if pause > 0:
                time.sleep(pause)
        return "\n".join(results)

    def _speak_with_sequence(self, text: str, sequence: list[tuple[str, float]]) -> str:
        """播报与手臂动作并行(边说话边做手势)。"""
        speech_box: list[str] = []
        gesture_box: list[str] = []
        errors: list[BaseException] = []

        def _speak() -> None:
            try:
                speech_box.append(self._speak_skill.speak(text, blocking=True))
            except BaseException as exc:
                errors.append(exc)

        def _gesture() -> None:
            try:
                gesture_box.append(self._run_sequence(sequence))
            except BaseException as exc:
                errors.append(exc)

        speak_thread = threading.Thread(target=_speak, name="greeter-speak", daemon=True)
        gesture_thread = threading.Thread(target=_gesture, name="greeter-gesture", daemon=True)
        speak_thread.start()
        gesture_thread.start()
        speak_thread.join()
        gesture_thread.join()
        if errors:
            raise errors[0]
        speech = speech_box[0] if speech_box else ""
        gesture = gesture_box[0] if gesture_box else ""
        return f"{speech}\n{gesture}"

    @skill
    def greet_guest(self, spoken_text: str) -> str:
        """欢迎到访的客人:边握手边播报欢迎语。

        Args:
            spoken_text: 要说出口的欢迎语,一两句、不超过 20 字。
        """
        text = spoken_text.strip()
        if not text:
            return "欢迎语不能为空。"
        return self._speak_with_sequence(text, _WELCOME_SEQUENCE)

    @skill
    def farewell_guest(self, spoken_text: str) -> str:
        """向离开的客人道别:边挥手边播报告别语。

        Args:
            spoken_text: 要说出口的告别语,一两句、不超过 20 字。
        """
        text = spoken_text.strip()
        if not text:
            return "告别语不能为空。"
        return self._speak_with_sequence(text, _GOODBYE_SEQUENCE)

    @skill
    def list_actions(self) -> str:
        """列出机器人当前可用的动作(预设手势 + UniStore 舞蹈名)。

        只读诊断,不移动机器人。播舞蹈前可先查有哪些 ``name`` 可用。
        """
        try:
            response = self._connection.publish_request(
                ARM_TOPIC, {"api_id": ARM_GET_ACTION_LIST_API_ID, "parameter": {}}
            )
            return f"Action list: {response}"
        except Exception as e:
            logger.error(f"Failed to query action list: {e}")
            return "Failed to query the action list."

    @skill
    def execute_arm_command(self, command_name: str) -> str:
        """执行单个手臂手势。普通问答不要用;迎宾/道别用 greet_guest/farewell_guest。

        Args:
            command_name: 手势名,如 HighWave、ArmHeart、Handshake。
        """
        return execute_g1_command(
            self._connection.publish_request,
            ARM_COMMANDS,
            ARM_API_ID,
            ARM_TOPIC,
            command_name,
            logger=logger,
        )

    @skill
    def execute_custom_action(self, action_name: str) -> str:
        """按名称播放 UniStore/示教全身动作(如舞蹈)。与 ``execute_arm_command`` 不同。

        先用 ``list_actions`` 查准确名称。客人说「跳舞」时优先用 ``perform_dance``。

        Args:
            action_name: 动作列表中的精确名称,如 Waist_Drum_Dance。
        """
        if not action_name.strip():
            return "action_name cannot be empty."
        try:
            self._connection.publish_request(
                ARM_TOPIC,
                {
                    "api_id": ARM_EXECUTE_CUSTOM_ACTION_API_ID,
                    "parameter": {"action_name": action_name.strip()},
                },
            )
            return f"Custom action '{action_name}' started."
        except Exception as e:
            logger.error(f"Failed to execute custom action {action_name}: {e}")
            return f"Failed to start custom action '{action_name}'."

    @skill
    def stop_custom_action(self) -> str:
        """停止正在播放的全身/示教动作(如舞蹈)。"""
        try:
            self._connection.publish_request(
                ARM_TOPIC,
                {"api_id": ARM_STOP_CUSTOM_ACTION_API_ID, "parameter": {}},
            )
            return "Custom action stop requested."
        except Exception as e:
            logger.error(f"Failed to stop custom action: {e}")
            return "Failed to stop custom action."

    def _resolved_dance_style(self) -> Literal["webrtc_arms", "onboard_random"]:
        return resolve_dance_style(
            self.config.dance_style,
            self.config.g.unitree_connection_type,
            is_onboard_compute(),
        )

    @skill
    def perform_dance(self) -> str:
        """表演跳舞:笔记本 WebRTC 为固定手臂舞步,Orin 上为随机全身舞。

        语音短路已会先 speak 短台词再调用本技能;通过 LLM 调用时请先 speak 一句再调本技能。
        """
        style = self._resolved_dance_style()
        if style == "webrtc_arms":
            logger.info("跳舞模式: WebRTC 手臂固定舞步")
            return self._run_sequence(_WEBRTC_ARM_DANCE_SEQUENCE)
        names = [n.strip() for n in self.config.dance_names if n.strip()]
        if not names:
            return "未配置舞蹈名称。"
        action_name = random.choice(names)
        logger.info("跳舞模式: Orin 随机全身舞 %s", action_name)
        return self.execute_custom_action(action_name)


