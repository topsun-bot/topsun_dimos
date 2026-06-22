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

"""Test CosyVoice synthesis + G1 AudioClient.PlayStream on Orin.

Usage on Orin::

    bash ~/topsun_dimos/scripts/orin_test_cosyvoice_speaker.sh eth0
"""

from __future__ import annotations

import io
import os
import sys

import numpy as np
import soundfile as sf  # type: ignore[import-untyped]
from unitree_sdk2py.core.channel import ChannelFactoryInitialize  # type: ignore[import-not-found]
from unitree_sdk2py.g1.audio.g1_audio_client import AudioClient  # type: ignore[import-not-found]

from dimos.robot.unitree.g1.audio.g1_speech_player import float_audio_to_g1_pcm, play_pcm_on_g1

_SAMPLE_RATE = 24000


def _synthesize_cosyvoice(text: str) -> np.ndarray:
    import dashscope  # type: ignore[import]
    from dashscope.audio.tts_v2 import SpeechSynthesizer  # type: ignore[import]

    api_key = os.environ.get("DASHSCOPE_API_KEY", "")
    if not api_key:
        raise RuntimeError("DASHSCOPE_API_KEY not set")

    dashscope.api_key = api_key
    synthesizer = SpeechSynthesizer(model="cosyvoice-v3-flash", voice="longanyang")
    audio_bytes: bytes = synthesizer.call(text)
    if not audio_bytes:
        raise RuntimeError("DashScope returned empty audio")

    with sf.SoundFile(io.BytesIO(audio_bytes), "r") as sound_file:
        sr = int(sound_file.samplerate)
        audio = sound_file.read(dtype="float32")
    if audio.ndim > 1:
        audio = audio.mean(axis=1)
    if sr != _SAMPLE_RATE:
        duration = len(audio) / float(sr)
        src_t = np.linspace(0.0, duration, num=len(audio), endpoint=False)
        dst_len = max(1, int(duration * _SAMPLE_RATE))
        dst_t = np.linspace(0.0, duration, num=dst_len, endpoint=False)
        audio = np.interp(dst_t, src_t, audio).astype(np.float32)
    return audio.astype(np.float32)


def main() -> None:
    iface = sys.argv[1] if len(sys.argv) > 1 else "eth0"
    text = sys.argv[2] if len(sys.argv) > 2 else "您好，欢迎来访，我是智能接待员。"

    print(f"CosyVoice synthesizing: {text!r}")
    audio = _synthesize_cosyvoice(text)
    pcm = float_audio_to_g1_pcm(audio, _SAMPLE_RATE)
    print(f"PCM ready: {len(pcm)} bytes @ 16kHz mono int16")

    ChannelFactoryInitialize(0, iface)
    client = AudioClient()
    client.SetTimeout(30.0)
    client.Init()
    client.SetVolume(85)

    print(f"Playing via PlayStream on {iface!r} ...")
    play_pcm_on_g1(client, pcm)
    print("Done.")


if __name__ == "__main__":
    main()
