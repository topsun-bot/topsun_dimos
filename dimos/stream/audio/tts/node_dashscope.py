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

"""DashScope CosyVoice TTS 节点, 当 DASHSCOPE_API_KEY 可用时替代 OpenAI TTS."""

from __future__ import annotations

import io
import os
import threading
import time

import numpy as np
from reactivex import Observable, Subject

from dimos.stream.audio.base import AudioEvent
from dimos.stream.audio.text.base import AbstractTextConsumer, AbstractTextEmitter
from dimos.utils.logging_config import setup_logger

logger = setup_logger()

# DashScope CosyVoice 默认参数
_DEFAULT_MODEL = "cosyvoice-v3-flash"
_DEFAULT_VOICE = "longanyang"
_SAMPLE_RATE = 24000


class DashScopeTTSNode(AbstractTextConsumer, AbstractTextEmitter):
    """基于 DashScope CosyVoice 的 TTS 节点, 接口与 OpenAITTSNode 兼容."""

    def __init__(
        self,
        api_key: str | None = None,
        model: str = _DEFAULT_MODEL,
        voice: str = _DEFAULT_VOICE,
    ) -> None:
        self._api_key = api_key or os.environ.get("DASHSCOPE_API_KEY", "")
        self._model = model
        self._voice = voice

        self.audio_subject: Subject[AudioEvent] = Subject()
        self.text_subject: Subject[str] = Subject()
        self.subscription = None
        self.processing_thread: threading.Thread | None = None
        self.is_running = True
        self.text_queue: list[str] = []
        self.queue_lock = threading.Lock()

    # ── 与 OpenAITTSNode 兼容的公共接口 ──

    def emit_audio(self) -> Observable:  # type: ignore[type-arg]
        return self.audio_subject

    def emit_text(self) -> Observable:  # type: ignore[type-arg]
        return self.text_subject

    def consume_text(self, text_observable: Observable) -> DashScopeTTSNode:  # type: ignore[type-arg]
        logger.info("Starting DashScopeTTSNode (model=%s, voice=%s)", self._model, self._voice)
        self.processing_thread = threading.Thread(target=self._process_queue, daemon=True)
        self.processing_thread.start()
        self.subscription = text_observable.subscribe(
            on_next=self._queue_text,
            on_error=lambda e: logger.error("Error in DashScopeTTSNode: %s", e),
        )
        return self

    # ── 内部 ──

    def _queue_text(self, text: str) -> None:
        if not text.strip():
            return
        with self.queue_lock:
            self.text_queue.append(text)

    def _process_queue(self) -> None:
        while self.is_running:
            text_to_process = None
            with self.queue_lock:
                if self.text_queue:
                    text_to_process = self.text_queue.pop(0)
            if text_to_process:
                self._synthesize_speech(text_to_process)
            else:
                time.sleep(0.1)

    def _synthesize_speech(self, text: str) -> None:
        try:
            import dashscope
            from dashscope.audio.tts_v2 import SpeechSynthesizer

            dashscope.api_key = self._api_key
            synthesizer = SpeechSynthesizer(model=self._model, voice=self._voice)
            audio_bytes: bytes = synthesizer.call(text)

            if not audio_bytes:
                logger.warning("DashScope TTS returned empty audio for: %s", text)
                return

            self.text_subject.on_next(text)

            # CosyVoice 默认输出 mp3, 用 soundfile 解码
            audio_io = io.BytesIO(audio_bytes)
            try:
                import soundfile as sf  # type: ignore[import-untyped]

                with sf.SoundFile(audio_io, "r") as sound_file:
                    audio_array = sound_file.read(dtype="float32")
            except Exception:
                # mp3 可能需要额外依赖; 回退到简单 pcm 解析
                logger.warning("soundfile 无法解码 DashScope 音频, 尝试 pydub 回退")
                try:
                    from pydub import AudioSegment  # type: ignore[import-untyped]

                    audio_io.seek(0)
                    seg = AudioSegment.from_file(audio_io, format="mp3")
                    seg = seg.set_frame_rate(_SAMPLE_RATE).set_channels(1).set_sample_width(2)
                    raw = np.frombuffer(seg.raw_data, dtype=np.int16).astype(np.float32) / 32768.0
                    audio_array = raw
                except Exception as inner:
                    logger.error("无法解码 DashScope TTS 音频: %s", inner)
                    return

            audio_event = AudioEvent(
                data=audio_array,
                sample_rate=_SAMPLE_RATE,
                timestamp=time.time(),
                channels=1 if audio_array.ndim == 1 else audio_array.shape[1],
            )
            self.audio_subject.on_next(audio_event)

        except Exception as e:
            logger.error("DashScope TTS synthesis error: %s", e)

    def dispose(self) -> None:
        logger.info("Disposing DashScopeTTSNode")
        self.is_running = False
        with self.queue_lock:
            self.text_queue.clear()
        if self.processing_thread and self.processing_thread.is_alive():
            self.processing_thread.join(timeout=2.0)
        if self.subscription:
            self.subscription.dispose()
            self.subscription = None
        self.audio_subject.on_completed()
        self.text_subject.on_completed()
