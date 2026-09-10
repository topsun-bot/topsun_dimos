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

"""Play TTS audio on the G1 body speaker via Unitree SDK2 ``AudioClient``."""

from __future__ import annotations

import time
from typing import Any

import numpy as np

from dimos.utils.logging_config import setup_logger

logger = setup_logger()

G1_SPEECH_SAMPLE_RATE = 16000
# Max bytes per PlayStream chunk (3 s @ 16 kHz mono int16) — unitree_sdk2 example default.
_G1_PCM_CHUNK_BYTES = 96000
_G1_STREAM_APP_NAME = "dimos"
# Unitree voice service / examples may use other app_name values for PlayStream/TtsMaker.
_G1_STOP_APP_NAMES = (
    "dimos",
    "voice",
    "example",
    "unitree",
    "g1",
    "robot",
    "default",
    "assistant",
    "tts",
)


def stop_g1_playback(audio_client: Any) -> None:
    """Stop any in-progress G1 speaker playback (Unitree cloud + dimos streams)."""
    for app_name in _G1_STOP_APP_NAMES:
        try:
            audio_client.PlayStop(app_name)
        except Exception:
            pass


def pcm_bytes_to_seconds(nbytes: int) -> float:
    return nbytes / float(G1_SPEECH_SAMPLE_RATE * 2)


def amplify_pcm16(pcm: bytes, gain: float) -> bytes:
    """Scale int16 PCM in place (clipped). ``gain`` 1.0 = no change."""
    if not pcm or gain == 1.0:
        return pcm
    samples = np.frombuffer(pcm, dtype=np.int16)
    scaled = np.clip(samples.astype(np.float32) * gain, -32767.0, 32767.0)
    return scaled.astype(np.int16).tobytes()


def float_audio_to_g1_pcm(
    audio: np.ndarray, sample_rate: int, *, pcm_gain: float = 1.0
) -> bytes:
    """Convert float32 mono audio to 16 kHz mono PCM16 bytes for ``PlayStream``."""
    if audio.ndim > 1:
        audio = audio.mean(axis=1)
    audio = audio.astype(np.float32, copy=False)

    if sample_rate != G1_SPEECH_SAMPLE_RATE:
        duration = len(audio) / float(sample_rate)
        src_t = np.linspace(0.0, duration, num=len(audio), endpoint=False)
        dst_len = max(1, int(duration * G1_SPEECH_SAMPLE_RATE))
        dst_t = np.linspace(0.0, duration, num=dst_len, endpoint=False)
        audio = np.interp(dst_t, src_t, audio).astype(np.float32)

    if pcm_gain != 1.0:
        audio = np.clip(audio * pcm_gain, -1.0, 1.0)
    clipped = np.clip(audio, -1.0, 1.0)
    return np.asarray(clipped * 32767.0, dtype=np.int16).tobytes()


def play_pcm_on_g1(audio_client: Any, pcm: bytes, *, stream_id: str | None = None) -> float:
    """Stream PCM to the G1 speaker. Returns estimated playback duration in seconds."""
    if not pcm:
        return 0.0

    if len(pcm) % 2 != 0:
        pcm = pcm[:-1]

    if stream_id is None:
        stream_id = str(int(time.time() * 1000))

    playback_s = pcm_bytes_to_seconds(len(pcm))
    offset = 0
    chunk_index = 0

    # Send all chunks back-to-back (same stream_id). Do NOT sleep 1 s between
    # chunks — a 96 kB chunk is 3 s of audio; early PlayStop / next chunk cuts playback.
    while offset < len(pcm):
        chunk = pcm[offset : offset + _G1_PCM_CHUNK_BYTES]
        code, _ = audio_client.PlayStream(_G1_STREAM_APP_NAME, stream_id, chunk)
        if code != 0:
            raise RuntimeError(f"G1 PlayStream chunk {chunk_index} failed with code {code}")
        offset += len(chunk)
        chunk_index += 1

    time.sleep(playback_s)
    audio_client.PlayStop(_G1_STREAM_APP_NAME)

    logger.info(
        "G1 PlayStream done: %d bytes, %d chunks, ~%.2fs audio",
        len(pcm),
        chunk_index,
        playback_s,
    )
    return playback_s


def play_float_audio_on_g1(
    audio_client: Any, audio: np.ndarray, sample_rate: int, *, stream_id: str | None = None
) -> float:
    pcm = float_audio_to_g1_pcm(audio, sample_rate)
    return play_pcm_on_g1(audio_client, pcm, stream_id=stream_id)


__all__ = [
    "G1_SPEECH_SAMPLE_RATE",
    "amplify_pcm16",
    "float_audio_to_g1_pcm",
    "pcm_bytes_to_seconds",
    "play_float_audio_on_g1",
    "play_pcm_on_g1",
    "stop_g1_playback",
]
