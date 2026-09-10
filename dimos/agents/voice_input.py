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

"""Local-mic and cockpit voice input for the agent.

``VoiceInput`` / ``VadVoiceInput`` publish Whisper transcripts on
``/human_input`` from a local microphone (push-to-talk or hands-free VAD).
Greeter blueprints depend on those names.

``CockpitVoiceInput`` is the robot-side of the cockpit chat-panel mic:
``audio_in`` carries one utterance per button hold as ordered AudioChunk
slices. Reassembled bytes go through the owned local STT chain and the
transcript is published on ``human_input``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from queue import Empty, Full, Queue
import select
import sys
from threading import Event, Lock, Thread, Timer
import threading
import time
from typing import TYPE_CHECKING, Any

import reactivex as rx
from reactivex.disposable import Disposable
import sounddevice as sd  # type: ignore[import-untyped]

from dimos.constants import DEFAULT_THREAD_JOIN_TIMEOUT
from dimos.core.core import rpc
from dimos.core.module import Module, ModuleConfig
from dimos.core.stream import In, Out
from dimos.core.transport import pLCMTransport
from dimos.stream.audio.base import AudioEvent
from dimos.stream.audio.decode import decode_audio_bytes, ffmpeg_requirement
from dimos.stream.audio.node_key_recorder import KeyRecorder
from dimos.stream.audio.node_microphone import SounddeviceAudioSource
from dimos.stream.audio.node_normalizer import AudioNormalizer
from dimos.stream.audio.node_vad_recorder import VadRecorder
from dimos.stream.audio.pipeline import whisper_pipeline
from dimos.utils.logging_config import setup_logger
from dimos.web.relay_bridge.audio_codec import AudioChunk

VOICE_PTT_TOPIC = "/voice_ptt_toggle"

if TYPE_CHECKING:
    from dimos.core.coordination.blueprints import Blueprint

logger = setup_logger()


class VoiceInputConfig(ModuleConfig):
    whisper_model: str = "base"
    language: str = "zh"  # 传给 Whisper 的语言代码,如 "zh"、"en"。
    device_index: int | None = None  # None 表示系统默认输入设备。
    always_listen: bool = True  # 保持麦克风持续订阅,录音更顺滑。


class VoiceInput(Module):
    """麦克风 -> Whisper 语音识别 -> ``/human_input``。"""

    config: VoiceInputConfig

    _human_transport: pLCMTransport[str] | None = None
    _recorder: KeyRecorder | None = None

    @rpc
    def start(self) -> None:
        super().start()

        # 延迟导入:让本模块(及使用它的蓝图)在未安装 Whisper 后端的机器上也能正常导入。
        from dimos.stream.audio.stt.node_whisper import WhisperNode

        self._human_transport = pLCMTransport("/human_input")

        mic = SounddeviceAudioSource(device_index=self.config.device_index)
        normalizer = AudioNormalizer()
        stt_node = WhisperNode(
            model=self.config.whisper_model,
            modelopts={"language": self.config.language, "fp16": False},
        )
        recorder = KeyRecorder(
            always_subscribe=self.config.always_listen,
            ptt_topic=VOICE_PTT_TOPIC,
        )

        # Wire pipeline before KeyRecorder starts listening for Enter.
        normalizer.consume_audio(mic.emit_audio())
        recorder.consume_audio(normalizer.emit_audio())
        stt_node.consume_audio(recorder.emit_recording())

        self.register_disposable(stt_node.emit_text().subscribe(self._publish_text))

        self._recorder = recorder
        logger.info(
            "VoiceInput 已启动 — 按 Enter 开始录音,对着麦克风说 2~5 秒,再按 Enter 发送"
        )

    def _publish_text(self, text: str) -> None:
        cleaned = text.strip()
        if not cleaned:
            return
        logger.info("USER (voice): %s", cleaned)
        if self._human_transport is not None:
            self._human_transport.publish(cleaned)

    @rpc
    def stop(self) -> None:
        if self._recorder is not None:
            self._recorder.stop()
            self._recorder = None
        if self._human_transport is not None:
            self._human_transport.stop()
            self._human_transport = None
        super().stop()


def blueprint_has_voice_input(blueprint: Blueprint) -> bool:
    """True if the blueprint deploys push-to-talk ``VoiceInput``."""
    return any(bp.module is VoiceInput for bp in blueprint.active_blueprints)


_ptt_forwarder_thread: threading.Thread | None = None


def start_stdin_ptt_forwarder() -> None:
    """Forward Enter key presses from the terminal to ``VOICE_PTT_TOPIC`` (main process only)."""
    global _ptt_forwarder_thread
    if _ptt_forwarder_thread is not None and _ptt_forwarder_thread.is_alive():
        return
    if not sys.stdin.isatty():
        logger.warning("stdin 不是 TTY,无法转发按键;请在前台终端运行 dimos")
        return

    def _run() -> None:
        transport = pLCMTransport[str](VOICE_PTT_TOPIC)
        transport.start()
        logger.info("语音按键转发已启动 — 在本终端按 Enter 开始/结束录音")
        try:
            while True:
                if select.select([sys.stdin], [], [], 0.5)[0]:
                    line = sys.stdin.readline()
                    if line:
                        transport.publish("toggle")
        finally:
            transport.stop()

    _ptt_forwarder_thread = threading.Thread(target=_run, name="VoicePttForwarder", daemon=True)
    _ptt_forwarder_thread.start()


class VadVoiceInputConfig(ModuleConfig):
    whisper_model: str = "base"
    language: str = "zh"  # 传给 Whisper 的语言代码,如 "zh"、"en"。
    device_index: int | None = None  # None 表示系统默认输入设备。
    vad_threshold: float = 0.008  # RMS 能量阈值,高于此判定为说话。
    silence_hangover_s: float = 0.8  # 尾部静音超过此值则一句话结束。
    min_speech_s: float = 0.15  # 短于此的声音当噪声丢弃。
    post_speak_cooldown_s: float = 0.5  # 智能体说完后延迟开麦,避免拾到喇叭尾音。


class VadVoiceInput(Module):
    """免提语音输入:麦克风 -> 能量 VAD 自动分句 -> Whisper -> ``/human_input``。

    无需按键:检测到有人说话即自动录音、停顿即结束并识别。订阅 ``agent_idle``,
    只在智能体空闲时开麦——智能体思考/说话(speak 阻塞)期间关麦,避免把机器人
    自己的声音当成新提问(防自问自答)。
    """

    config: VadVoiceInputConfig

    agent_idle: In[bool]

    _human_transport: pLCMTransport[str] | None = None
    _vad: VadRecorder | None = None
    _enable_timer: Timer | None = None

    @rpc
    def start(self) -> None:
        super().start()

        # 延迟导入:未装 Whisper 后端也能导入蓝图。
        from dimos.stream.audio.stt.node_whisper import WhisperNode

        self._human_transport = pLCMTransport("/human_input")

        mic = SounddeviceAudioSource(device_index=self.config.device_index)
        vad = VadRecorder(
            threshold=self.config.vad_threshold,
            silence_hangover_s=self.config.silence_hangover_s,
            min_speech_s=self.config.min_speech_s,
        )
        stt_node = WhisperNode(
            model=self.config.whisper_model,
            modelopts={"language": self.config.language, "fp16": False},
        )

        # 原始麦克风音频直接进 VAD(不经归一化,保证能量阈值有意义)。
        vad.consume_audio(mic.emit_audio())
        stt_node.consume_audio(vad.emit_recording())

        self.register_disposable(stt_node.emit_text().subscribe(self._publish_text))
        self.register_disposable(Disposable(self.agent_idle.subscribe(self._on_agent_idle)))

        self._vad = vad
        mic_info = (
            sd.query_devices(self.config.device_index, "input")
            if self.config.device_index is not None
            else sd.query_devices(kind="input")
        )
        logger.info(
            "VadVoiceInput 已启动(免提,正在聆听) 麦克风=%s threshold=%.4f",
            mic_info.get("name", "?"),
            self.config.vad_threshold,
        )

    def _cancel_enable_timer(self) -> None:
        if self._enable_timer is not None:
            self._enable_timer.cancel()
            self._enable_timer = None

    def _on_agent_idle(self, idle: bool) -> None:
        # 智能体空闲才开麦;思考/说话时关麦,防止把机器人自己的声音当成提问。
        if self._vad is None:
            return
        self._cancel_enable_timer()
        if idle:
            cooldown = self.config.post_speak_cooldown_s

            def _enable() -> None:
                if self._vad is not None:
                    self._vad.set_enabled(True)
                    logger.info("VadVoiceInput 开麦(智能体空闲)")

            if cooldown > 0:
                self._enable_timer = Timer(cooldown, _enable)
                self._enable_timer.start()
            else:
                _enable()
        else:
            self._vad.set_enabled(False)
            logger.info("VadVoiceInput 关麦(智能体忙碌)")

    def _publish_text(self, text: str) -> None:
        cleaned = text.strip()
        if not cleaned:
            logger.warning("VadVoiceInput: Whisper 未识别出文字(请大声说、说完停顿 1 秒)")
            return
        logger.info("USER (voice): %s", cleaned)
        if self._human_transport is not None:
            self._human_transport.publish(cleaned)

    @rpc
    def stop(self) -> None:
        self._cancel_enable_timer()
        if self._vad is not None:
            self._vad.stop()
            self._vad = None
        if self._human_transport is not None:
            self._human_transport.stop()
            self._human_transport = None
        super().stop()


@dataclass
class _Assembly:
    """One in-flight utterance: ordered slices of its container bytes."""

    expected_seq: int = 0
    total_bytes: int = 0
    last_at: float = 0.0
    parts: list[bytes] = field(default_factory=list)


class CockpitVoiceInputConfig(ModuleConfig):
    # A cancelled hold sends no final frame; reap its assembly after this.
    stale_utterance_s: float = 15.0
    max_utterance_bytes: int = 2 * 1024 * 1024
    max_pending_utterances: int = 4


class CockpitVoiceInput(Module):
    """Cockpit push-to-talk: AudioChunk slices on ``audio_in`` -> STT -> ``human_input``."""

    config: CockpitVoiceInputConfig

    audio_in: In[AudioChunk]
    human_input: Out[str]

    _assemblies: dict[str, _Assembly]
    _lock: Lock
    _queue: Queue[bytes]
    _thread: Thread
    _stop_event: Event
    _audio_subject: rx.subject.Subject[AudioEvent] | None

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._assemblies = {}
        self._lock = Lock()
        self._queue = Queue(maxsize=self.config.max_pending_utterances)
        self._thread = Thread(
            target=self._worker_loop,
            name=f"{self.__class__.__name__}-thread",
            daemon=True,
        )
        self._stop_event = Event()
        self._audio_subject = None

    def __reduce__(self) -> Any:
        return (self.__class__, (), {})

    @rpc
    def start(self) -> None:
        requirement_error = ffmpeg_requirement()
        if requirement_error is not None:
            raise RuntimeError(requirement_error)
        super().start()
        self._audio_subject, transcripts = whisper_pipeline()
        self.register_disposable(
            transcripts.subscribe(
                on_next=self._publish_text,
                # Upstream stream failures still terminate the subscription.
                on_error=lambda e: logger.error("voice STT pipeline failed: %s", e),
            )
        )
        # Not handle_audio_in: the auto-bound handler mailbox is latest-wins
        # and would drop chunks; a manual subscription sees every one.
        self.register_disposable(Disposable(self.audio_in.subscribe(self._on_chunk)))
        if not self._thread.is_alive():
            self._thread.start()

    @rpc
    def stop(self) -> None:
        self._stop_event.set()
        if self._thread.is_alive():
            self._thread.join(timeout=DEFAULT_THREAD_JOIN_TIMEOUT)
        super().stop()

    def _on_chunk(self, chunk: AudioChunk) -> None:
        """Transport-thread hot path: bookkeeping only, decode and STT queued."""
        now = time.monotonic()
        finished: bytes | None = None
        with self._lock:
            self._reap_stale_locked(now)
            assembly = self._assemblies.get(chunk.sid)
            if assembly is None:
                if chunk.seq != 0:
                    logger.warning("voice utterance %s dropped: no start chunk", chunk.sid)
                    return
                assembly = _Assembly()
                self._assemblies[chunk.sid] = assembly
            if chunk.seq != assembly.expected_seq:
                del self._assemblies[chunk.sid]
                logger.warning(
                    "voice utterance %s dropped: chunk gap (got seq %d, expected %d)",
                    chunk.sid,
                    chunk.seq,
                    assembly.expected_seq,
                )
                return
            assembly.expected_seq += 1
            assembly.last_at = now
            assembly.total_bytes += len(chunk.data)
            if assembly.total_bytes > self.config.max_utterance_bytes:
                del self._assemblies[chunk.sid]
                logger.warning(
                    "voice utterance %s dropped: over %d bytes",
                    chunk.sid,
                    self.config.max_utterance_bytes,
                )
                return
            if chunk.data:
                assembly.parts.append(chunk.data)
            if chunk.final:
                del self._assemblies[chunk.sid]
                data = b"".join(assembly.parts)
                if data:
                    finished = data
        if finished is not None:
            try:
                self._queue.put_nowait(finished)
            except Full:
                logger.warning("voice utterance dropped: transcription queue full")

    def _reap_stale(self) -> None:
        now = time.monotonic()
        with self._lock:
            self._reap_stale_locked(now)

    def _reap_stale_locked(self, now: float) -> None:
        for sid, assembly in list(self._assemblies.items()):
            if now - assembly.last_at > self.config.stale_utterance_s:
                del self._assemblies[sid]
                logger.info("voice utterance %s reaped: cancelled or sender gone", sid)

    def _worker_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                raw = self._queue.get(timeout=0.5)
            except Empty:
                # Expiry must not depend on new traffic: a cancelled hold may
                # be the last thing a browser ever sends.
                self._reap_stale()
                continue
            try:
                event = decode_audio_bytes(raw)
                if event is not None and self._audio_subject is not None:
                    self._audio_subject.on_next(event)
            except Exception:
                # One bad clip must not end voice input for the process.
                logger.exception("voice transcription failed")

    def _publish_text(self, text: str) -> None:
        text = text.strip()
        if text:
            self.human_input.publish(text)
