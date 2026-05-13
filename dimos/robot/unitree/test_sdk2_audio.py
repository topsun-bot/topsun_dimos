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

"""Tests for the SDK2 audio wrapper."""

from unittest.mock import MagicMock, patch

from dimos.robot.unitree import sdk2_audio


def test_tts_maker_initializes_client_and_calls_api() -> None:
    fake_client = MagicMock()
    fake_client.TtsMaker.return_value = 0

    with patch.object(sdk2_audio, "ChannelFactoryInitialize") as mock_init:
        with patch.object(sdk2_audio, "AudioClient", return_value=fake_client):
            result = sdk2_audio.tts_maker("汪汪汪", speaker_id=0, network_interface="wlp130s0f0")

    assert result == 0
    mock_init.assert_called_once_with(0, "wlp130s0f0")
    fake_client.SetTimeout.assert_called_once_with(10.0)
    fake_client.Init.assert_called_once_with()
    fake_client.TtsMaker.assert_called_once_with("汪汪汪", 0)
