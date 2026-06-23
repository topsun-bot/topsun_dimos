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

"""G1 内置语音 ASR → dimos ``/human_input``。

宇树 G1 在 App 开启「唤醒对话模式」后,机身麦经语音服务识别,识别文本发布到 DDS
``rt/audio_msg`` (``std_msgs.msg.dds_.String_``)。本模块订阅该话题并转发给
``GreeterIntentRouter`` 等消费者,无需 USB 麦或本地 Whisper。

``G1HighLevelDdsSdk`` 必须先完成 DDS / ``AudioClient`` 初始化;本模块只订阅 ASR,
不再重复 ``ChannelFactoryInitialize``(并行初始化会导致 cyclonedds 崩溃)。

Unitree ASR 常对同一句发多条增量结果(「你」→「你叫什么」→「你叫什么名字」)。
本模块在 ``asr_quiet_period_s`` 静默后才发布一次,并过滤回声与短时重复。

参考: ``unitree_sdk2/example/g1/audio/g1_audio_client_example.cpp``
"""

from __future__ import annotations

import json
import threading
import time
from typing import Any

from reactivex.disposable import Disposable

from dimos.core.core import rpc
from dimos.core.module import Module, ModuleConfig
from dimos.core.stream import In
from dimos.core.transport import pLCMTransport
from dimos.robot.unitree.g1.connection_spec import G1RobotAudioSpec
from dimos.robot.unitree.g1.g1_asr_dedupe import is_unitree_status_payload, should_publish_asr
from dimos.utils.logging_config import setup_logger

logger = setup_logger()

_G1_ASR_TOPIC = "rt/audio_msg"
_DDS_READY_DELAY_S = 2.0
_SUBSCRIBE_RETRY_INTERVAL_S = 0.5
_SUBSCRIBE_MAX_ATTEMPTS = 40


def parse_g1_asr_payload(raw: str) -> str:
    """Normalize ``rt/audio_msg`` payload to user utterance text."""
    cleaned = raw.strip()
    if not cleaned:
        return ""
    if is_unitree_status_payload(cleaned):
        return ""
    if cleaned[0] in "{[":
        try:
            parsed = json.loads(cleaned)
        except json.JSONDecodeError:
            return cleaned
        if isinstance(parsed, str):
            return parsed.strip()
        if isinstance(parsed, dict):
            for key in ("text", "asr", "result", "data", "content"):
                value = parsed.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()
        return cleaned
    return cleaned


class G1BuiltinVoiceInputConfig(ModuleConfig):
    network_interface: str = "eth0"  # unused at runtime; DDS owned by G1HighLevelDdsSdk
    asr_topic: str = _G1_ASR_TOPIC
    asr_quiet_period_s: float = 1.0  # 略长静默,减少宇树 ASR 丢字头
    dedupe_window_s: float = 12.0  # 同一句不重复触发 greeter
    min_text_len: int = 2
    post_speak_mute_s: float = 3.0  # 机器人说完后再开 ASR,避免拾到回声
    dds_ready_delay_s: float = _DDS_READY_DELAY_S


class G1BuiltinVoiceInput(Module):
    """G1 DDS ASR (``rt/audio_msg``) → ``/human_input``."""

    config: G1BuiltinVoiceInputConfig

    agent_idle: In[bool]
    _g1_audio: G1RobotAudioSpec | None = None

    _human_transport: pLCMTransport[str] | None = None
    _subscriber: Any = None
    _subscribe_thread: threading.Thread | None = None
    _stop_event: threading.Event
    _asr_lock: threading.Lock
    _enabled: bool = False
    _asr_buffer: str = ""
    _debounce_timer: threading.Timer | None = None
    _enable_timer: threading.Timer | None = None
    _last_text: str = ""
    _last_publish_time: float = 0.0

    @rpc
    def start(self) -> None:
        super().start()
        self._stop_event = threading.Event()
        self._asr_lock = threading.Lock()
        self._human_transport = pLCMTransport("/human_input")
        self.register_disposable(Disposable(self.agent_idle.subscribe(self._on_agent_idle)))

        self._subscribe_thread = threading.Thread(
            target=self._subscribe_asr_when_dds_ready,
            name="G1BuiltinVoiceInput-subscribe",
            daemon=True,
        )
        self._subscribe_thread.start()
        logger.info(
            "G1BuiltinVoiceInput 已启动 — 等待 G1HighLevelDdsSdk 就绪后订阅 %s",
            self.config.asr_topic,
        )

    def _subscribe_asr_when_dds_ready(self) -> None:
        """Wait for sibling ``G1HighLevelDdsSdk`` then subscribe (no duplicate DDS init)."""
        delay = self.config.dds_ready_delay_s
        if delay > 0:
            if self._stop_event.wait(delay):
                return

        from unitree_sdk2py.core.channel import ChannelSubscriber  # type: ignore[import-not-found]
        from unitree_sdk2py.idl.std_msgs.msg.dds_ import String_  # type: ignore[import-not-found]

        for attempt in range(1, _SUBSCRIBE_MAX_ATTEMPTS + 1):
            if self._stop_event.is_set():
                return
            try:
                subscriber = ChannelSubscriber(self.config.asr_topic, String_)
                subscriber.Init(self._on_asr_msg, 10)
                self._subscriber = subscriber
                logger.info(
                    "G1BuiltinVoiceInput 已订阅 %s (attempt %d); App 需开启「唤醒对话模式」",
                    self.config.asr_topic,
                    attempt,
                )
                return
            except Exception as exc:
                logger.warning(
                    "G1BuiltinVoiceInput 订阅 %s 失败 (attempt %d/%d): %s",
                    self.config.asr_topic,
                    attempt,
                    _SUBSCRIBE_MAX_ATTEMPTS,
                    exc,
                )
                if self._stop_event.wait(_SUBSCRIBE_RETRY_INTERVAL_S):
                    return

        logger.error(
            "G1BuiltinVoiceInput 无法订阅 %s — 检查 G1HighLevelDdsSdk 是否启动、"
            "openclaw-gateway 是否占用 DDS",
            self.config.asr_topic,
        )

    def _cancel_debounce_timer(self) -> None:
        with self._asr_lock:
            if self._debounce_timer is not None:
                self._debounce_timer.cancel()
                self._debounce_timer = None

    def _cancel_enable_timer(self) -> None:
        if self._enable_timer is not None:
            self._enable_timer.cancel()
            self._enable_timer = None

    def _on_agent_idle(self, idle: bool) -> None:
        self._cancel_enable_timer()
        if not idle:
            self._enabled = False
            self._cancel_debounce_timer()
            logger.info("G1BuiltinVoiceInput 暂停转发(智能体忙碌)")
            return

        mute = self.config.post_speak_mute_s

        def _enable() -> None:
            self._enabled = True
            logger.info("G1BuiltinVoiceInput 转发 ASR(智能体空闲)")

        if mute > 0:
            self._enable_timer = threading.Timer(mute, _enable)
            self._enable_timer.start()
        else:
            _enable()

    def _on_asr_msg(self, msg: Any) -> None:
        if not self._enabled:
            return
        raw = getattr(msg, "data", str(msg))
        text = parse_g1_asr_payload(str(raw))
        if not text:
            return
        quiet = self.config.asr_quiet_period_s
        with self._asr_lock:
            self._asr_buffer = text
            if self._debounce_timer is not None:
                self._debounce_timer.cancel()
            self._debounce_timer = threading.Timer(quiet, self._publish_debounced_asr)
            self._debounce_timer.start()

    def _publish_debounced_asr(self) -> None:
        if self._stop_event.is_set() or not self._enabled:
            return
        with self._asr_lock:
            text = self._asr_buffer.strip()
            self._debounce_timer = None
        if not text:
            return
        now = time.monotonic()
        if not should_publish_asr(
            text,
            last_text=self._last_text,
            last_publish_time=self._last_publish_time,
            now=now,
            dedupe_window_s=self.config.dedupe_window_s,
            min_text_len=self.config.min_text_len,
        ):
            logger.debug("G1BuiltinVoiceInput 跳过重复/回声 ASR: %s", text[:40])
            return
        self._last_text = text
        self._last_publish_time = now
        if self._g1_audio is not None:
            self._g1_audio.stop_speech_playback()
        logger.info("USER (G1 ASR): %s", text)
        if self._human_transport is not None:
            self._human_transport.publish(text)

    @rpc
    def stop(self) -> None:
        if hasattr(self, "_stop_event"):
            self._stop_event.set()
        self._cancel_debounce_timer()
        self._cancel_enable_timer()
        if self._subscribe_thread is not None:
            self._subscribe_thread.join(timeout=2.0)
            self._subscribe_thread = None
        if self._subscriber is not None:
            try:
                self._subscriber.Close()
            except Exception as exc:
                logger.debug("G1 ASR subscriber close: %s", exc)
            self._subscriber = None
        if self._human_transport is not None:
            self._human_transport.stop()
            self._human_transport = None
        super().stop()


__all__ = [
    "G1BuiltinVoiceInput",
    "G1BuiltinVoiceInputConfig",
    "parse_g1_asr_payload",
]
