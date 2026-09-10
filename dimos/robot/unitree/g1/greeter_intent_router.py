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

"""迎宾意图路由:寒暄/常见 FAQ 走固定模板,其余转 LLM。"""

from __future__ import annotations

import re
import threading
import time

from reactivex.disposable import Disposable

from dimos.agents.skills.speak_skill_spec import SpeakSkillSpec
from dimos.core.core import rpc
from dimos.core.module import Module, ModuleConfig
from dimos.core.stream import In, Out
from dimos.core.transport import pLCMTransport
from dimos.robot.unitree.g1.g1_asr_dedupe import (
    asr_texts_similar,
    is_valid_user_utterance,
    looks_like_robot_echo,
    normalize_asr_text,
)
from dimos.robot.unitree.g1.greeter_skill_spec import GreeterSkillSpec
from dimos.robot.unitree.g1.greeter_tour_skill_spec import TourGuideSpec
from dimos.utils.logging_config import setup_logger

logger = setup_logger()

# 手势短路表里表示「随机舞蹈」,由 ``perform_dance`` 处理。
DANCE_SHORTCUT = "__dance__"

_PUNCT_RE = re.compile(r"[\s，。！？、,.!?;:'\"“”‘’\-—…]+")


def normalize_utterance(text: str) -> str:
    """Lowercase, strip whitespace and common punctuation."""
    cleaned = _PUNCT_RE.sub("", text.strip().lower())
    return cleaned


_FILLER_PREFIX = frozenset("喂嗯哦")  # 仅去前缀;「哈」保留给「哈喽」
_FILLER_SUFFIX = frozenset("呀啊哦嗯呢吧啦")
# 含这些子串的短句视为提问,不走固定寒暄(即使以「你好」开头)。
# 问路/导航类问句(未建图时走固定拒答,避免 LLM 编造位置)。
_LOCATION_QUERY_MARKERS = (
    "在哪",
    "在哪里",
    "哪儿",
    "哪里",
    "怎么走",
    "怎么去",
    "带我去",
    "在哪层",
    "在哪个",
    "导航到",
    "怎么去到",
)

_QUESTION_MARKERS = (
    "请问",
    "在哪",
    "哪里",
    "怎么",
    "什么",
    "多少",
    "几点",
    "厕所",
    "卫生间",
    "导航",
    "带我",
    "帮忙",
    "介绍",
)


def _strip_greeting_fillers(text: str) -> str:
    """去掉 Whisper 常见的语气词前缀/后缀,如「喂你好呀」→「你好」。"""
    t = text
    while t and t[0] in _FILLER_PREFIX:
        t = t[1:]
    while t and t[-1] in _FILLER_SUFFIX:
        t = t[:-1]
    return t


def _looks_like_question(norm: str) -> bool:
    return any(m in norm for m in _QUESTION_MARKERS) or norm.endswith("吗")


def _is_greeting_shortcut(norm: str, triggers: set[str]) -> bool:
    """短句寒暄:精确匹配,或触发词 + 纯语气词后缀/前缀。"""
    if norm in triggers:
        return True
    core = _strip_greeting_fillers(norm)
    if core in triggers:
        return True
    for trig in sorted(triggers, key=len, reverse=True):
        if not core.startswith(trig):
            continue
        rest = core[len(trig) :]
        if not rest or all(c in _FILLER_SUFFIX for c in rest):
            return True
    return False


def match_greeter_intent(text: str, config: GreeterIntentRouterConfig) -> str | None:
    """Return ``welcome`` / ``farewell`` for short trigger phrases, else ``None``."""
    if len(text) > config.max_shortcut_chars:
        return None
    norm = normalize_utterance(text)
    if not norm:
        return None
    if _looks_like_question(norm):
        return None
    hello = {normalize_utterance(t) for t in config.hello_triggers}
    goodbye = {normalize_utterance(t) for t in config.goodbye_triggers}
    if _is_greeting_shortcut(norm, hello):
        return "welcome"
    if _is_greeting_shortcut(norm, goodbye):
        return "farewell"
    return None


def _blocks_gesture_shortcut(norm: str) -> bool:
    """复合问句(带地点/原因等)不走手势短路;纯祈使句如「挥挥手好吗」仍允许。"""
    return any(m in norm for m in _QUESTION_MARKERS)


def match_gesture_shortcut(text: str, config: GreeterIntentRouterConfig) -> str | None:
    """Match gesture/dance voice commands; returns command name or ``DANCE_SHORTCUT``."""
    cleaned = text.strip()
    if not cleaned or len(cleaned) > config.gesture_max_chars:
        return None
    if looks_like_robot_echo(cleaned):
        return None
    norm = normalize_utterance(cleaned)
    if _blocks_gesture_shortcut(norm):
        return None
    entries = sorted(
        config.gesture_shortcuts,
        key=lambda entry: max(len(kw) for kw in entry[0]),
        reverse=True,
    )
    for keywords, command in entries:
        for kw in sorted(keywords, key=len, reverse=True):
            if kw in cleaned or normalize_utterance(kw) in norm:
                return command
    return None


def is_location_query(text: str, max_chars: int) -> bool:
    """True for wayfinding questions like 「厕所在哪」「怎么去前台」."""
    cleaned = text.strip()
    if not cleaned or len(cleaned) > max_chars:
        return False
    return any(marker in cleaned for marker in _LOCATION_QUERY_MARKERS)


def match_unmapped_location_reply(text: str, config: GreeterIntentRouterConfig) -> str | None:
    """Fixed reply when a location query is asked but no landmarks/map are loaded."""
    if config.location_landmarks_available:
        return None
    if is_location_query(text, config.location_query_max_chars):
        return config.location_unknown_template
    return None


def _match_identity_faq_fuzzy(cleaned: str, norm: str) -> bool:
    """宇树 ASR 常丢字头/错字;对「你是谁/叫什么」类问题放宽匹配。"""
    if not norm:
        return False
    if len(norm) > 20:
        return False
    for a, b in (("介绍", "自己"), ("介告", "自己"), ("介绍下", "自己")):
        if a in norm and b in norm:
            return True
    for frag in (
        "你是谁",
        "你叫什么",
        "叫什么名",
        "叫什么名字",
        "你是哪位",
        "你的名字",
        "名字是什么",
        "你叫啥",
        "叫啥名",
        "介绍下自己",
        "介绍你自己",
        "介告你自己",
    ):
        if frag in cleaned or frag in norm:
            return True
    if norm in ("名字", "叫啥", "你叫啥", "啥名", "哪位"):
        return True
    if len(norm) <= 6 and "名字" in norm:
        return True
    if "是谁" in norm and len(norm) <= 8:
        return True
    if "叫什么" in norm or "叫啥" in norm:
        return True
    return False


def match_faq_answer(text: str, config: GreeterIntentRouterConfig) -> str | None:
    """Keyword FAQ: skip LLM when a configured keyword appears in the utterance."""
    cleaned = text.strip()
    if not cleaned:
        return None
    norm = normalize_utterance(cleaned)
    for keywords, answer in config.faq_entries:
        if _IDENTITY_FAQ_KEYWORDS.intersection(keywords):
            if _match_identity_faq_fuzzy(cleaned, norm):
                return answer
        for kw in keywords:
            kw_norm = normalize_utterance(kw)
            if kw in cleaned or (kw_norm and kw_norm in norm):
                return answer
    return None


_IDENTITY_FAQ_KEYWORDS = frozenset(
    {
        "你是谁",
        "你叫什么",
        "叫什么名字",
        "你叫什么名字",
        "你的名字",
        "名字是什么",
        "叫什么名",
        "你是哪位",
        "介绍你自己",
    }
)


def collect_greeter_prewarm_texts(config: GreeterIntentRouterConfig) -> list[str]:
    """Core greeting templates only; gesture lines synthesize on first use."""
    texts = [
        config.welcome_template,
        config.farewell_template,
    ]
    for keywords, answer in config.faq_entries:
        if _IDENTITY_FAQ_KEYWORDS.intersection(keywords):
            texts.append(answer)
    return list(dict.fromkeys(t.strip() for t in texts if t.strip()))


class GreeterIntentRouterConfig(ModuleConfig):
    welcome_template: str = "您好，欢迎来访！我是智能接待员 Danee，让我来带您参观一下。"
    farewell_template: str = "再见，欢迎下次来访！"
    hello_triggers: tuple[str, ...] = (
        "你好",
        "您好",
        "哈喽",
        "hello",
        "hi",
        "hey",
    )
    goodbye_triggers: tuple[str, ...] = (
        "再见",
        "拜拜",
        "再会",
        "bye",
        "goodbye",
    )
    max_shortcut_chars: int = 12  # 仅对很短的寒暄句走模板,避免误伤长句提问。
    # 非地点类 FAQ(直接 speak)。
    faq_entries: tuple[tuple[tuple[str, ...], str], ...] = (
        (
            (
                "你是谁",
                "你叫什么",
                "叫什么名字",
                "你叫什么名字",
                "你的名字",
                "名字是什么",
                "叫什么名",
                "你是哪位",
                "介绍你自己",
            ),
            "我是智能接待员 Daneel，很高兴为您服务。",
        ),
    )
    location_landmarks_available: bool = False  # Orin 导览建图标点后改为 True
    location_query_max_chars: int = 48
    location_unknown_template: str = "抱歉，这个地点还没录入地图，请咨询工作人员。"
    llm_enabled: bool = False  # 临时关闭开放对话;改 True 并接线 llm_human_input 即可恢复。
    llm_fallback_template: str = ""  # 非空时模板外输入播此句;默认静默忽略。
    shortcut_cooldown_s: float = 12.0  # 同一句/同类短路在冷却期内不重复播报
    gesture_max_chars: int = 24
    # (触发词, 手臂命令名或 DANCE_SHORTCUT); 语音命中后只执行动作,不播报。
    gesture_shortcuts: tuple[tuple[tuple[str, ...], str], ...] = (
        (("挥手", "挥挥手", "挥一下手"), "HighWave"),
        (("面前挥手",), "FaceWave"),
        (("比心", "比个心"), "ArmHeart"),
        (("右手比心",), "RightHeart"),
        (("握手",), "Handshake"),
        (("鼓掌", "拍手"), "Clap"),
        (("击掌",), "HighFive"),
        (("拥抱", "抱一下"), "Hug"),
        (("举手", "双手举起"), "HandsUp"),
        (("飞吻",), "LeftKiss"),
        (("跳舞", "跳个舞", "跳一支舞", "表演", "来段表演"), DANCE_SHORTCUT),
    )


class GreeterIntentRouter(Module):
    """寒暄/FAQ 走固定模板;其余转给 LLM。"""

    config: GreeterIntentRouterConfig

    human_input: In[str]
    llm_human_input: Out[str]

    _greeter: GreeterSkillSpec
    _speak: SpeakSkillSpec
    # Optional导览带路模块(仅 Orin 版注入);为 None 时问路走原有「未建图固定拒答」。
    _tour: TourGuideSpec | None = None

    _stream_transport_pins = {
        "human_input": pLCMTransport,
        "llm_human_input": pLCMTransport,
    }

    _agent_idle_transport: pLCMTransport[bool] | None = None
    _busy_lock: threading.Lock
    _last_shortcut_norm: str = ""
    _last_shortcut_time: float = 0.0

    @rpc
    def start(self) -> None:
        super().start()
        self._busy_lock = threading.Lock()
        self._agent_idle_transport = pLCMTransport[bool]("/agent_idle")
        self._agent_idle_transport.start()
        self.register_disposable(Disposable(self.human_input.subscribe(self._on_human_input)))
        self._speak.prewarm_texts(collect_greeter_prewarm_texts(self.config))
        if self.config.llm_enabled:
            mode = "寒暄/手势/问路(未建图)/FAQ 走短路, 其余→LLM"
        else:
            fallback = self.config.llm_fallback_template.strip()
            mode = (
                "仅模板短路, LLM 已关闭, 其余→固定拒答"
                if fallback
                else "仅模板短路, LLM 已关闭, 其余→静默忽略"
            )
        logger.info("GreeterIntentRouter 已启动 — %s", mode)
        # 确保免提麦在启动后即可开（不依赖 McpClient/LLM 是否就绪）。
        self._agent_idle_transport.publish(True)

    @rpc
    def stop(self) -> None:
        if self._agent_idle_transport is not None:
            self._agent_idle_transport.stop()
            self._agent_idle_transport = None
        super().stop()

    def _should_skip_repeat_shortcut(self, cleaned: str) -> bool:
        norm = normalize_asr_text(cleaned)
        if not norm:
            return True
        elapsed = time.monotonic() - self._last_shortcut_time
        if self._last_shortcut_norm and elapsed < self.config.shortcut_cooldown_s:
            if norm == self._last_shortcut_norm or asr_texts_similar(cleaned, self._last_shortcut_norm):
                logger.info("GreeterIntentRouter 冷却中,忽略重复: %s", cleaned[:40])
                return True
        return False

    def _mark_shortcut(self, cleaned: str) -> None:
        self._last_shortcut_norm = normalize_asr_text(cleaned)
        self._last_shortcut_time = time.monotonic()

    def _on_human_input(self, text: str) -> None:
        cleaned = text.strip()
        if not cleaned:
            return
        if not is_valid_user_utterance(cleaned):
            logger.debug("GreeterIntentRouter 忽略无效 ASR: %s", cleaned[:60])
            return
        norm = normalize_utterance(cleaned)
        if len(norm) < 4:
            has_intent = match_greeter_intent(cleaned, self.config) is not None
            has_identity = _match_identity_faq_fuzzy(cleaned, norm)
            has_gesture = match_gesture_shortcut(cleaned, self.config) is not None
            if not has_intent and not has_identity and not has_gesture:
                logger.info("GreeterIntentRouter 忽略 ASR 碎片: %s", cleaned[:40])
                return
        if self._should_skip_repeat_shortcut(cleaned):
            return

        intent = match_greeter_intent(cleaned, self.config)
        gesture = None if intent is not None else match_gesture_shortcut(cleaned, self.config)

        # FAQ(如「你是谁」)优先于导览问路,避免被带路逻辑抢走。
        faq_answer = (
            None
            if intent is not None or gesture is not None
            else match_faq_answer(cleaned, self.config)
        )
        if faq_answer is not None:
            self._mark_shortcut(cleaned)
            lock = self._busy_lock
            if not lock.acquire(blocking=False):
                logger.info("GreeterIntentRouter 忙碌,忽略 FAQ: %s", cleaned[:40])
                return

            def _run_faq() -> None:
                try:
                    self._run_faq_shortcut(faq_answer)
                finally:
                    lock.release()

            threading.Thread(
                target=_run_faq, name="greeter-shortcut-faq", daemon=True
            ).start()
            return

        # 已接入导览带路时,问路/带路交给地标表 + 导航处理(命中→带路/讲解,未命中→固定
        # 拒答),全程不经 LLM。未接入(笔记本版)时走下面原有的「未建图固定拒答」。
        if (
            intent is None
            and gesture is None
            and self._tour is not None
            and is_location_query(cleaned, self.config.location_query_max_chars)
        ):
            self._dispatch_tour_query(cleaned)
            return

        location_reply = (
            None
            if intent is not None or gesture is not None
            else match_unmapped_location_reply(cleaned, self.config)
        )
        if intent is None and gesture is None and location_reply is None:
            if self.config.llm_enabled:
                logger.info("GreeterIntentRouter → LLM: %s", cleaned[:80])
                self.llm_human_input.publish(cleaned)
            else:
                fallback = self.config.llm_fallback_template.strip()
                if fallback:
                    self._mark_shortcut(cleaned)
                    logger.info("GreeterIntentRouter 模板外拒答: %s", cleaned[:80])
                    self._run_template_fallback_shortcut()
                else:
                    logger.info("GreeterIntentRouter 未识别,忽略: %s", cleaned[:80])
            return

        self._mark_shortcut(cleaned)
        lock = self._busy_lock
        if not lock.acquire(blocking=False):
            logger.info("GreeterIntentRouter 忙碌,忽略: %s", cleaned[:40])
            return

        def _run() -> None:
            try:
                if intent is not None:
                    self._run_greeting_shortcut(intent)
                elif gesture is not None:
                    self._run_gesture_shortcut(gesture)
                elif location_reply is not None:
                    self._run_location_unknown_shortcut(location_reply)
            finally:
                lock.release()

        shortcut_kind = intent or gesture or ("location" if location_reply else None)
        threading.Thread(target=_run, name=f"greeter-shortcut-{shortcut_kind}", daemon=True).start()

    def _set_agent_busy(self, busy: bool) -> None:
        if self._agent_idle_transport is not None:
            self._agent_idle_transport.publish(not busy)

    def _run_greeting_shortcut(self, intent: str) -> None:
        self._set_agent_busy(True)
        try:
            if intent == "welcome":
                logger.info("GreeterIntentRouter 固定迎宾: %s", self.config.welcome_template)
                result = self._greeter.greet_guest(self.config.welcome_template)
            else:
                logger.info("GreeterIntentRouter 固定道别: %s", self.config.farewell_template)
                result = self._greeter.farewell_guest(self.config.farewell_template)
            logger.info("GreeterIntentRouter 完成: %s", (result or "")[:120])
        except Exception:
            logger.exception("GreeterIntentRouter 固定模板执行失败")
        finally:
            self._set_agent_busy(False)

    def _run_gesture_shortcut(self, command: str) -> None:
        self._set_agent_busy(True)
        try:
            logger.info("GreeterIntentRouter 手势(无播报): %s", command)
            if command == DANCE_SHORTCUT:
                result = self._greeter.perform_dance()
            else:
                result = self._greeter.execute_arm_command(command)
            logger.info("GreeterIntentRouter 手势完成: %s", (result or "")[:120])
        except Exception:
            logger.exception("GreeterIntentRouter 手势执行失败")
        finally:
            self._set_agent_busy(False)

    def _run_location_unknown_shortcut(self, answer: str) -> None:
        self._set_agent_busy(True)
        try:
            logger.info("GreeterIntentRouter 未建图问路: %s", answer[:80])
            result = self._speak.speak(answer, blocking=True)
            logger.info("GreeterIntentRouter 问路完成: %s", (result or "")[:120])
        except Exception:
            logger.exception("GreeterIntentRouter 问路播报失败")
        finally:
            self._set_agent_busy(False)

    def _run_faq_shortcut(self, answer: str) -> None:
        self._set_agent_busy(True)
        try:
            logger.info("GreeterIntentRouter FAQ 直答: %s", answer[:80])
            result = self._speak.speak(answer, blocking=True)
            logger.info("GreeterIntentRouter FAQ 完成: %s", (result or "")[:120])
        except Exception:
            logger.exception("GreeterIntentRouter FAQ 播报失败")
        finally:
            self._set_agent_busy(False)

    def _dispatch_tour_query(self, text: str) -> None:
        lock = self._busy_lock
        if not lock.acquire(blocking=False):
            logger.info("GreeterIntentRouter 忙碌,忽略问路: %s", text[:40])
            return

        def _run() -> None:
            self._set_agent_busy(True)
            try:
                assert self._tour is not None
                logger.info("GreeterIntentRouter → 导览带路: %s", text[:80])
                result = self._tour.handle_location_query(text)
                logger.info("GreeterIntentRouter 导览完成: %s", (result or "")[:120])
            except Exception:
                logger.exception("GreeterIntentRouter 导览处理失败")
            finally:
                self._set_agent_busy(False)
                lock.release()

        threading.Thread(target=_run, name="greeter-tour-query", daemon=True).start()

    def _run_template_fallback_shortcut(self) -> None:
        lock = self._busy_lock
        if not lock.acquire(blocking=False):
            logger.info("GreeterIntentRouter 忙碌,忽略模板外输入")
            return

        def _run() -> None:
            self._set_agent_busy(True)
            try:
                answer = self.config.llm_fallback_template.strip()
                if not answer:
                    return
                logger.info("GreeterIntentRouter 模板外拒答播报: %s", answer[:80])
                result = self._speak.speak(answer, blocking=True)
                logger.info("GreeterIntentRouter 拒答完成: %s", (result or "")[:120])
            except Exception:
                logger.exception("GreeterIntentRouter 模板外拒答失败")
            finally:
                self._set_agent_busy(False)
                lock.release()

        threading.Thread(target=_run, name="greeter-shortcut-fallback", daemon=True).start()
