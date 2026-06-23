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

from unittest.mock import MagicMock, patch

import numpy as np

from dimos.robot.unitree.g1.audio.g1_speech_player import (
    G1_SPEECH_SAMPLE_RATE,
    _G1_PCM_CHUNK_BYTES,
    float_audio_to_g1_pcm,
    pcm_bytes_to_seconds,
    play_pcm_on_g1,
)


def test_float_audio_to_g1_pcm_resamples_to_16k_mono_int16() -> None:
    audio = np.zeros(24000, dtype=np.float32)
    pcm = float_audio_to_g1_pcm(audio, sample_rate=24000)
    assert len(pcm) == G1_SPEECH_SAMPLE_RATE * 2
    assert pcm[0:2] == b"\x00\x00"


def test_play_pcm_on_g1_waits_full_duration_before_play_stop() -> None:
    client = MagicMock()
    client.PlayStream.return_value = (0, None)
    pcm = b"\x00\x00" * (_G1_PCM_CHUNK_BYTES // 2 + 1000)
    expected_s = pcm_bytes_to_seconds(len(pcm))

    with patch("dimos.robot.unitree.g1.audio.g1_speech_player.time.sleep") as sleep_mock:
        play_pcm_on_g1(client, pcm, stream_id="test")
        assert client.PlayStream.call_count == 2
        sleep_mock.assert_called_once_with(expected_s)
        client.PlayStop.assert_called_once()
