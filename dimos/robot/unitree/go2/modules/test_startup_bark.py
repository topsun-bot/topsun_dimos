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

"""Tests for StartupBarkModule."""

from unittest.mock import MagicMock, patch

import pytest

from dimos.core.global_config import global_config
from dimos.robot.unitree.go2.modules.startup_bark import StartupBarkModule


class TestStartupBarkModule:
    """Tests for StartupBarkModule."""

    def test_module_can_be_instantiated(self) -> None:
        """Module can be created without errors."""
        module = StartupBarkModule()
        assert module is not None

    def test_start_schedules_bark(self) -> None:
        """start() schedules the bark timer without crashing."""
        module = StartupBarkModule()
        with patch.object(module, "_bark"):
            module.start()
            # Timer should have been scheduled
            # We can't easily verify the timer is running, but start() should not raise
        module.stop()

    def test_stop_cleans_up_resources(self) -> None:
        """stop() cleans up TTS and audio resources without errors."""
        module = StartupBarkModule()
        module._tts_node = MagicMock()
        module._audio_output = MagicMock()
        module._webrtc_connection = MagicMock()
        # Should not raise
        module.stop()
        module._tts_node = None
        module._audio_output = None
        module._webrtc_connection = None

    def test_bark_local_does_not_crash_in_replay_mode(self) -> None:
        """_bark_local() works in replay mode without a speaker (logs warning)."""
        # Temporarily set replay mode
        original_replay = global_config.replay
        original_simulation = global_config.simulation
        global_config.replay = True
        global_config.simulation = False

        module = StartupBarkModule()
        module._tts_node = MagicMock()
        module._audio_output = MagicMock()

        # Should not raise even without a speaker
        try:
            module._bark_local()
        except Exception as e:
            pytest.fail(f"_bark_local raised unexpectedly: {e}")

        global_config.replay = original_replay
        global_config.simulation = original_simulation

    def test_bark_local_does_not_crash_in_sim_mode(self) -> None:
        """_bark_local() works in simulation mode without a speaker."""
        original_replay = global_config.replay
        original_simulation = global_config.simulation
        global_config.replay = False
        global_config.simulation = True

        module = StartupBarkModule()
        module._tts_node = MagicMock()
        module._audio_output = MagicMock()

        try:
            module._bark_local()
        except Exception as e:
            pytest.fail(f"_bark_local raised unexpectedly: {e}")

        global_config.replay = original_replay
        global_config.simulation = original_simulation

    def test_bark_on_robot_skips_when_no_ip(self) -> None:
        """_bark_on_robot() logs a warning when robot_ip is not set."""
        original_replay = global_config.replay
        original_simulation = global_config.simulation
        original_robot_ip = global_config.robot_ip
        global_config.replay = False
        global_config.simulation = False
        global_config.robot_ip = None

        module = StartupBarkModule()

        with patch("dimos.robot.unitree.go2.modules.startup_bark.logger") as mock_logger:
            module._bark_on_robot()
            mock_logger.warning.assert_called_once_with("No robot IP configured, skipping bark")

        global_config.replay = original_replay
        global_config.simulation = original_simulation
        global_config.robot_ip = original_robot_ip

    def test_bark_on_robot_generates_and_uploads_audio(self) -> None:
        """_bark_on_robot() calls generate, upload, and play in real mode."""
        original_replay = global_config.replay
        original_simulation = global_config.simulation
        original_robot_ip = global_config.robot_ip
        global_config.replay = False
        global_config.simulation = False
        global_config.robot_ip = "192.168.1.1"

        module = StartupBarkModule()
        module._openai_client = MagicMock()

        mock_response = MagicMock()
        mock_response.content = b"fake_mp3_data"
        module._openai_client.audio.speech.create.return_value = mock_response

        with patch.object(
            module, "_upload_audio_to_robot", return_value="test_uuid"
        ) as mock_upload:
            with patch.object(module, "_play_audio_on_robot") as mock_play:
                module._bark_on_robot()
                mock_upload.assert_called_once_with(b"fake_mp3_data", filename="bark.wav")
                mock_play.assert_called_once_with("test_uuid")

        global_config.replay = original_replay
        global_config.simulation = original_simulation
        global_config.robot_ip = original_robot_ip

    def test_generate_audio_returns_bytes(self) -> None:
        """_generate_audio() returns bytes from OpenAI TTS."""
        module = StartupBarkModule()
        module._openai_client = MagicMock()

        mock_response = MagicMock()
        mock_response.content = b"fake_audio_bytes"
        module._openai_client.audio.speech.create.return_value = mock_response

        result = module._generate_audio("test text")

        assert result == b"fake_audio_bytes"
        module._openai_client.audio.speech.create.assert_called_once_with(
            model="tts-1",
            voice="echo",
            input="test text",
            speed=1.3,
            response_format="mp3",
        )
