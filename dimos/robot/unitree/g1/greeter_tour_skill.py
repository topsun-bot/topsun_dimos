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

"""G1 Orin 导览带路技能(标点 + 导航 + 到站讲解)。

链路:

1. 工作人员标点:``tag_location(name, intro_script=..., synonyms=...)`` 记录当前里程计位姿与
   讲解词,持久化到地标表并预缓存讲解词 TTS。``intro_script`` 可省略,此时在标点瞬间调用
   LLM 生成(失败则用固定模板),**仅生成一次**;到站与问路仍播已保存文本,不经 LLM。
2. 客人问路/带路:``GreeterIntentRouter`` 把问路句转给 ``handle_location_query``,匹配
   到地标则发布导航目标(``goal`` 流 → ``SimplePlanner``),否则播固定「未录入」模板。
3. 到站:订阅里程计,当与目标距离小于阈值时播 ``intro_script``(仅语音,不做手势)。
"""

from __future__ import annotations

import math
from pathlib import Path
import re
import threading

from reactivex.disposable import Disposable

from dimos.agents.annotation import skill
from dimos.agents.skills.speak_skill_spec import SpeakSkillSpec
from dimos.core.core import rpc
from dimos.core.module import Module, ModuleConfig
from dimos.core.stream import In, Out
from dimos.msgs.geometry_msgs.PointStamped import PointStamped
from dimos.msgs.nav_msgs.Odometry import Odometry
from dimos.robot.unitree.g1.greeter_intro_script_generator import resolve_intro_script_for_tag
from dimos.robot.unitree.g1.greeter_landmark_store import (
    GreeterLandmarkStore,
    Landmark,
)
from dimos.robot.unitree.g1.greeter_skill_spec import GreeterSkillSpec
from dimos.utils.logging_config import setup_logger

logger = setup_logger()

_SYNONYM_SPLIT_RE = re.compile(r"[,，、;；\s]+")


def parse_synonyms(raw: str) -> tuple[str, ...]:
    """把逗号/顿号/空格分隔的同义词串解析为去重元组。"""
    parts = [p.strip() for p in _SYNONYM_SPLIT_RE.split(raw.strip()) if p.strip()]
    return tuple(dict.fromkeys(parts))


def is_guide_request(text: str, guide_keywords: tuple[str, ...]) -> bool:
    """带路意图(「带我去…」)为 True;纯问路(「…在哪」)为 False。"""
    cleaned = text.strip()
    return any(kw in cleaned for kw in guide_keywords)


def distance_2d(ax: float, ay: float, bx: float, by: float) -> float:
    return math.hypot(ax - bx, ay - by)


def within_arrival(px: float, py: float, landmark: Landmark, threshold_m: float) -> bool:
    """机器人当前 (px, py) 是否已到达 ``landmark`` 的到站阈值内。"""
    return distance_2d(px, py, landmark.x, landmark.y) <= threshold_m


def default_store_path() -> Path:
    return Path.home() / ".local" / "state" / "dimos" / "greeter_landmarks.json"


class GreeterTourSkillConfig(ModuleConfig):
    """导览带路配置。"""

    store_path: str = ""  # 空 → ``default_store_path()``
    arrival_threshold_m: float = 0.8
    arrival_gesture: str = ""  # 到站后仅播报讲解词;非空则额外做手势(默认不做)
    guide_keywords: tuple[str, ...] = (
        "带我去",
        "带我到",
        "带我",
        "带路",
        "领我",
        "送我",
        "去一下",
    )
    guide_speak_template: str = "好的，请跟我来，我带您去{name}。"
    unknown_template: str = "抱歉，这个地点还没录入地图，请咨询工作人员。"
    intro_script_fallback_template: str = "这里是{name}，欢迎参观。"
    llm_intro_script_enabled: bool = True
    llm_intro_model: str = ""  # 空 → 读 DIMOS_GREETER_INTRO_MODEL 或按 base_url 推断
    llm_intro_timeout_sec: float = 15.0
    llm_intro_max_chars: int = 120


class GreeterTourSkillContainer(Module):
    """标点、导航带路与到站讲解;讲解词只来自标点录入。"""

    config: GreeterTourSkillConfig

    # 用 PGO 校正后的里程计(世界/地图坐标系),与导航目标同一参考系。
    corrected_odometry: In[Odometry]
    goal: Out[PointStamped]

    _speak: SpeakSkillSpec
    _greeter: GreeterSkillSpec | None = None

    def __init__(self, **kwargs: object) -> None:
        super().__init__(**kwargs)
        self._store: GreeterLandmarkStore | None = None
        self._lock = threading.Lock()
        self._latest_pose: tuple[float, float, float] | None = None
        self._pending: Landmark | None = None

    @rpc
    def start(self) -> None:
        super().start()
        path = Path(self.config.store_path) if self.config.store_path else default_store_path()
        self._store = GreeterLandmarkStore(path)
        self._store.load()
        self.register_disposable(Disposable(self.corrected_odometry.subscribe(self._on_odom)))
        self._prewarm_landmarks()
        logger.info(
            "GreeterTourSkillContainer 已启动 — 地标 %d 个, store=%s",
            len(self._store),
            path,
        )

    @rpc
    def stop(self) -> None:
        super().stop()

    def _prewarm_landmarks(self) -> None:
        assert self._store is not None
        texts = [*self._store.intro_scripts(), self.config.unknown_template]
        if texts:
            self._speak.prewarm_texts(texts)

    def _on_odom(self, odom: Odometry) -> None:
        pose = (float(odom.x), float(odom.y), float(odom.z))
        trigger: Landmark | None = None
        with self._lock:
            self._latest_pose = pose
            if self._pending is not None and within_arrival(
                pose[0], pose[1], self._pending, self.config.arrival_threshold_m
            ):
                trigger = self._pending
                self._pending = None
        if trigger is not None:
            threading.Thread(
                target=self._announce_arrival,
                args=(trigger,),
                name="greeter-tour-arrival",
                daemon=True,
            ).start()

    def _announce_arrival(self, landmark: Landmark) -> None:
        try:
            logger.info("到站 %s,播报讲解词: %s", landmark.name, landmark.intro_script[:60])
            script = landmark.intro_script.strip() or f"我们到了{landmark.name}。"
            self._speak.speak(script, blocking=True)
        except Exception:
            logger.exception("到站讲解失败: %s", landmark.name)

    @skill
    def tag_location(self, name: str, intro_script: str = "", synonyms: str = "") -> str:
        """标记当前位置为一个导览地标,并保存到站讲解词(到站后会原样播报)。

        在机器人走到目标点后调用。坐标取当前里程计位姿。若未提供 ``intro_script``,
        且配置开启 ``llm_intro_script_enabled``,则在标点时调用 LLM 生成 1~2 句讲解词并
        写入地标表;生成失败则使用固定模板。生成后讲解词不再改写。

        Args:
            name: 地标名称,也是问路关键词,如 前台、厕所、展厅A。
            intro_script: 可选。到站后播报的讲解词;留空则由 LLM 在标点时生成。
            synonyms: 可选同义词,用逗号/顿号分隔,如 "卫生间,洗手间"。
        """
        clean_name = name.strip()
        if not clean_name:
            return "地标名称不能为空。"
        if self._store is None:
            return "地标存储尚未初始化。"
        with self._lock:
            pose = self._latest_pose
        if pose is None:
            return "尚未收到里程计数据,无法记录坐标。请确认导航栈已运行。"
        script, source = resolve_intro_script_for_tag(
            clean_name,
            intro_script,
            llm_enabled=self.config.llm_intro_script_enabled,
            fallback_template=self.config.intro_script_fallback_template,
            model=self.config.llm_intro_model,
            timeout_sec=self.config.llm_intro_timeout_sec,
            max_chars=self.config.llm_intro_max_chars,
        )
        landmark = Landmark(
            name=clean_name,
            x=pose[0],
            y=pose[1],
            z=pose[2],
            intro_script=script,
            synonyms=parse_synonyms(synonyms),
        )
        self._store.upsert(landmark)
        self._speak.prewarm_texts([landmark.intro_script])
        logger.info(
            "已标点 %s @ (%.2f, %.2f),讲解词来源=%s: %s",
            clean_name,
            pose[0],
            pose[1],
            source,
            landmark.intro_script[:80],
        )
        source_note = {"provided": "手写", "llm": "LLM 生成", "fallback": "默认模板"}[source]
        return (
            f"已记录地标 '{clean_name}'（讲解词:{source_note}）,共 {len(self._store)} 个地标。"
        )

    @skill
    def list_landmarks(self) -> str:
        """列出已标点的地标名称(只读诊断)。"""
        if self._store is None or len(self._store) == 0:
            return "尚未标记任何地标。"
        return "已标记地标: " + ", ".join(self._store.names())

    @rpc
    def landmark_count(self) -> int:
        return len(self._store) if self._store is not None else 0

    @rpc
    def handle_location_query(self, text: str) -> str:
        """处理问路/带路:匹配到地标则导航(带路)或播指引,否则播固定「未录入」模板。"""
        if self._store is None:
            return "地标存储尚未初始化。"
        landmark = self._store.match(text)
        if landmark is None:
            logger.info("问路未匹配到地标,固定拒答: %s", text[:40])
            self._speak.speak(self.config.unknown_template, blocking=True)
            return "no_landmark"
        if is_guide_request(text, self.config.guide_keywords):
            return self._start_guidance(landmark)
        logger.info("问路命中地标 %s,播讲解词", landmark.name)
        self._speak.speak(landmark.intro_script, blocking=True)
        return f"described:{landmark.name}"

    def _start_guidance(self, landmark: Landmark) -> str:
        with self._lock:
            self._pending = landmark
        self.goal.publish(PointStamped(x=landmark.x, y=landmark.y, z=landmark.z, frame_id="map"))
        logger.info("带路前往 %s @ (%.2f, %.2f)", landmark.name, landmark.x, landmark.y)
        self._speak.speak(
            self.config.guide_speak_template.format(name=landmark.name), blocking=True
        )
        return f"guiding:{landmark.name}"


__all__ = [
    "GreeterTourSkillConfig",
    "GreeterTourSkillContainer",
    "distance_2d",
    "is_guide_request",
    "parse_synonyms",
    "within_arrival",
]
