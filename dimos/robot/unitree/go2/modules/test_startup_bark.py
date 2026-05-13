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

import os
from unittest.mock import MagicMock, patch

import pytest

from dimos.core.global_config import global_config
from dimos.robot.unitree.go2.modules.startup_bark import BARK_TEXT, StartupBarkModule


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
        module.stop()

    def test_stop_cleans_up_resources(self) -> None:
        """stop() cleans up audio resources without errors."""
        module = StartupBarkModule()
        module._audio_output = MagicMock()
        module._go2_connection = MagicMock()
        module.stop()
        module._audio_output = None
        module._go2_connection = None

    def test_bark_local_does_not_crash_in_replay_mode(self) -> None:
        """_bark_local() works in replay mode without a speaker (logs warning)."""
        original_replay = global_config.replay
        original_simulation = global_config.simulation
        global_config.replay = True
        global_config.simulation = False

        module = StartupBarkModule()
        module._audio_output = MagicMock()

        with patch.object(module, "_generate_audio", return_value=b"fake_mp3_data"):
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
        module._audio_output = MagicMock()

        with patch.object(module, "_generate_audio", return_value=b"fake_mp3_data"):
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

        with patch.object(module, "_generate_audio", return_value=b"fake_mp3_data") as mock_gen:
            with patch.object(
                module, "_upload_audio_to_robot", return_value="test_uuid"
            ) as mock_upload:
                with patch.object(module, "_play_audio_on_robot") as mock_play:
                    module._bark_on_robot()
                    mock_gen.assert_called_once_with(BARK_TEXT)
                    mock_upload.assert_called_once_with(b"fake_mp3_data", filename="bark.wav")
                    mock_play.assert_called_once_with("test_uuid")

        global_config.replay = original_replay
        global_config.simulation = original_simulation
        global_config.robot_ip = original_robot_ip

    def test_generate_audio_returns_bytes(self) -> None:
        """_generate_audio() returns bytes from MiniMax TTS API."""
        module = StartupBarkModule()
        fake_hex = "ffd8ffe000104a46494600010100000100010000"
        fake_response_data = {
            "base_resp": {"status_code": 0, "status_msg": "success"},
            "data": {"audio": fake_hex},
        }

        with patch.dict(os.environ, {"MINIMAX_API_KEY": "test-key"}):
            with patch(
                "dimos.robot.unitree.go2.modules.startup_bark.requests.post",
            ) as mock_post:
                mock_post.return_value = MagicMock()
                mock_post.return_value.raise_for_status = MagicMock()
                mock_post.return_value.json.return_value = fake_response_data

                result = module._generate_audio("test text")

                assert result == bytes.fromhex(fake_hex)
                mock_post.assert_called_once()
                call_kwargs = mock_post.call_args.kwargs
                assert call_kwargs["headers"]["Authorization"] == "Bearer test-key"
                assert call_kwargs["json"]["model"] == "speech-2.8-hd"
                assert call_kwargs["json"]["text"] == "test text"
                assert call_kwargs["json"]["voice_setting"]["voice_id"] == "female-tianmei"

    def test_generate_audio_raises_on_api_error(self) -> None:
        """_generate_audio() raises RuntimeError on non-zero status_code."""
        module = StartupBarkModule()
        fake_response_data = {
            "base_resp": {"status_code": 10001, "status_msg": "invalid request"},
        }

        with patch.dict(os.environ, {"MINIMAX_API_KEY": "test-key"}):
            with patch(
                "dimos.robot.unitree.go2.modules.startup_bark.requests.post",
            ) as mock_post:
                mock_post.return_value = MagicMock()
                mock_post.return_value.raise_for_status = MagicMock()
                mock_post.return_value.json.return_value = fake_response_data

                with pytest.raises(RuntimeError, match="MiniMax error:"):
                    module._generate_audio("test text")

    def test_bark_text_is_five_woofs(self) -> None:
        """BARK_TEXT contains 5 bark instances as per spec."""
        assert BARK_TEXT == "汪汪 汪汪 汪汪 汪汪汪"
