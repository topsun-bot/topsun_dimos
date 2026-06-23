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

"""Hands-free, energy-based voice-activity-detection recorder.

Buffers incoming audio while the RMS volume stays above ``threshold`` and emits
one combined recording via :meth:`emit_recording` once trailing silence exceeds
``silence_hangover_s``. This is the hands-free counterpart to ``KeyRecorder``
(which needs an Enter keypress) — speech is segmented automatically.

Feed it RAW microphone audio (not the auto-gain ``AudioNormalizer`` output), so
the fixed energy threshold stays meaningful. Use :meth:`set_enabled` to gate
capture, e.g. mute while the robot is speaking to avoid self-triggering.
"""

import threading
from typing import Any

import numpy as np
from reactivex import Observable
from reactivex.subject import Subject

from dimos.stream.audio.base import AbstractAudioConsumer, AudioEvent
from dimos.stream.audio.volume import calculate_rms_volume
from dimos.utils.logging_config import setup_logger

logger = setup_logger()


def _combine_audio_events(events: list[AudioEvent]) -> AudioEvent | None:
    """Concatenate buffered frames into a single AudioEvent (mono-friendly)."""
    valid = [e for e in events if e is not None and e.data is not None and e.data.size > 0]
    if not valid:
        return None
    first = valid[0]
    flat = [e.data.reshape(-1) for e in valid]
    combined = np.concatenate(flat).astype(first.data.dtype)
    if combined.size == 0:
        return None
    return AudioEvent(
        data=combined,
        sample_rate=first.sample_rate,
        timestamp=first.timestamp,
        channels=1,
    )


class VadRecorder(AbstractAudioConsumer):
    """Energy-based VAD that auto-segments utterances into recordings."""

    def __init__(
        self,
        threshold: float = 0.008,
        silence_hangover_s: float = 0.8,
        min_speech_s: float = 0.15,
        max_speech_s: float = 15.0,
    ) -> None:
        """
        Args:
            threshold: RMS volume (0..1) above which a frame counts as speech.
            silence_hangover_s: Trailing silence that ends an utterance.
            min_speech_s: Utterances shorter than this are discarded as blips.
            max_speech_s: Hard cap; force-finalize an utterance after this long.
        """
        self.threshold = threshold
        self.silence_hangover_s = silence_hangover_s
        self.min_speech_s = min_speech_s
        self.max_speech_s = max_speech_s

        self._audio_observable: Observable | None = None  # type: ignore[type-arg]
        self._subscription: Any = None
        self._recording_subject: Subject = Subject()  # type: ignore[type-arg]
        self._lock = threading.RLock()
        self._enabled = True
        self._buffer: list[AudioEvent] = []
        self._in_speech = False
        self._speech_start = 0.0
        self._last_voice = 0.0

    def _reset_state(self) -> None:
        self._buffer = []
        self._in_speech = False
        self._speech_start = 0.0
        self._last_voice = 0.0

    def set_enabled(self, enabled: bool) -> None:
        """Enable/disable capture. Disabling drops any in-progress utterance."""
        with self._lock:
            if self._enabled and not enabled:
                self._reset_state()
            self._enabled = enabled

    def consume_audio(self, audio_observable: Observable) -> "VadRecorder":  # type: ignore[type-arg]
        self._audio_observable = audio_observable
        self._subscription = audio_observable.subscribe(
            on_next=self._on_event,
            on_error=lambda e: logger.error(f"VAD audio error: {e}"),
        )
        return self

    def emit_recording(self) -> Observable:  # type: ignore[type-arg]
        """Observable emitting one combined AudioEvent per detected utterance."""
        return self._recording_subject

    def stop(self) -> None:
        if self._subscription is not None:
            self._subscription.dispose()
            self._subscription = None

    def _on_event(self, event: AudioEvent) -> None:
        combined: AudioEvent | None = None
        with self._lock:
            if not self._enabled:
                return
            volume = calculate_rms_volume(event.data)
            ts = event.timestamp

            if volume >= self.threshold:
                if not self._in_speech:
                    self._in_speech = True
                    self._speech_start = ts
                    self._buffer = []
                    logger.info("VAD: 检测到说话 (rms=%.4f >= %.4f)", volume, self.threshold)
                self._last_voice = ts
                self._buffer.append(event)
            elif self._in_speech:
                self._buffer.append(event)
                if ts - self._last_voice >= self.silence_hangover_s:
                    combined = self._finalize_locked()

            if self._in_speech and ts - self._speech_start >= self.max_speech_s:
                combined = self._finalize_locked()

        # Emit outside the lock — downstream transcription is heavy.
        if combined is not None:
            self._recording_subject.on_next(combined)

    def _finalize_locked(self) -> AudioEvent | None:
        speech_duration = self._last_voice - self._speech_start
        buffer = self._buffer
        self._reset_state()
        if speech_duration < self.min_speech_s:
            logger.info(
                "VAD: 丢弃过短语音 (%.2fs < %.2fs)",
                speech_duration,
                self.min_speech_s,
            )
            return None
        logger.info("VAD: 一句话结束 (%.2fs), 送去识别", speech_duration)
        return _combine_audio_events(buffer)


__all__ = ["VadRecorder"]
