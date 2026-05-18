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

import base64
import json
import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from unitree_webrtc_connect.constants import AUDIO_API, RTC_TOPIC

from dimos.core.global_config import global_config
from dimos.robot.unitree.go2.modules.startup_bark import (
    GREETING_AUDIO_CHUNK_SIZE,
    GREETING_AUDIO_FILE_ENV,
    PLAY_MODE_NO_CYCLE,
    StartupBarkModule,
    resolve_startup_audio_path,
)


class TestStartupBarkModule:
    def test_module_can_be_instantiated(self) -> None:
        module = object.__new__(StartupBarkModule)
        assert module is not None

    def test_start_schedules_playback(self) -> None:
        module = object.__new__(StartupBarkModule)
        module._timer = None
        with patch("dimos.robot.unitree.go2.modules.startup_bark.Module.start"):
            with patch(
                "dimos.robot.unitree.go2.modules.startup_bark.threading.Timer"
            ) as mock_timer:
                mock_timer.return_value.start = MagicMock()
                with patch.object(module, "_play_startup_sound") as mock_play:
                    module.start()
                mock_timer.assert_called_once_with(2.0, mock_play)
                mock_timer.return_value.start.assert_called_once_with()
        module._timer = MagicMock()
        with patch("dimos.robot.unitree.go2.modules.startup_bark.Module.stop"):
            module.stop()

    def test_stop_cancels_timer(self) -> None:
        timer = MagicMock()
        module = object.__new__(StartupBarkModule)
        module._timer = timer
        with patch("dimos.robot.unitree.go2.modules.startup_bark.Module.stop"):
            module.stop()
        timer.cancel.assert_called_once_with()

    def test_skips_in_replay_mode(self) -> None:
        module = object.__new__(StartupBarkModule)
        with patch("dimos.robot.unitree.go2.modules.startup_bark.global_config") as config:
            config.replay = True
            config.simulation = False
            with patch.object(module, "_play_on_robot") as mock_play:
                module._play_startup_sound()
            mock_play.assert_not_called()

    def test_resolve_uses_env_file(self, tmp_path: Path) -> None:
        audio = tmp_path / "hello.mp3"
        audio.write_bytes(b"fake")
        with patch.dict(os.environ, {GREETING_AUDIO_FILE_ENV: str(audio)}):
            assert resolve_startup_audio_path() == audio

    def test_resolve_prefers_output_audio_in_defaults(self, tmp_path: Path) -> None:
        (tmp_path / "startup_greeting.mp3").write_bytes(b"a")
        preferred = tmp_path / "output_audio.mp3"
        preferred.write_bytes(b"b")
        with patch.dict(os.environ, {}, clear=True):
            with patch(
                "dimos.robot.unitree.go2.modules.startup_bark._GO2_ASSETS_DIR",
                tmp_path,
            ):
                assert resolve_startup_audio_path() == preferred

    def test_resolve_uses_sole_asset_clip(self, tmp_path: Path) -> None:
        audio = tmp_path / "xm4101.mp3"
        audio.write_bytes(b"fake")
        with patch.dict(os.environ, {}, clear=True):
            with patch(
                "dimos.robot.unitree.go2.modules.startup_bark._GO2_ASSETS_DIR",
                tmp_path,
            ):
                assert resolve_startup_audio_path() == audio

    def test_resolve_prefers_output_audio_when_multiple(self, tmp_path: Path) -> None:
        (tmp_path / "other.mp3").write_bytes(b"a")
        preferred = tmp_path / "output_audio.mp3"
        preferred.write_bytes(b"b")
        with patch.dict(os.environ, {}, clear=True):
            with patch(
                "dimos.robot.unitree.go2.modules.startup_bark._GO2_ASSETS_DIR",
                tmp_path,
            ):
                assert resolve_startup_audio_path() == preferred

    def test_resolve_returns_none_when_missing(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            with patch(
                "dimos.robot.unitree.go2.modules.startup_bark._GO2_ASSETS_DIR",
                Path("/nonexistent"),
            ):
                assert resolve_startup_audio_path() is None

    def test_play_on_robot_uploads_and_plays_via_audiohub(self) -> None:
        original_replay = global_config.replay
        original_simulation = global_config.simulation
        global_config.replay = False
        global_config.simulation = False

        module = object.__new__(StartupBarkModule)
        module._connection = MagicMock()
        wav_data = b"RIFF greeting bytes"
        filename = "startup_bark_1234"
        audio_uuid = "audio-uuid"
        fake_path = Path("/tmp/output_audio.mp3")

        module._connection.publish_request.side_effect = [
            {"ok": True},
            {
                "data": {
                    "data": json.dumps(
                        {"audio_list": [{"CUSTOM_NAME": filename, "UNIQUE_ID": audio_uuid}]}
                    )
                }
            },
            {"ok": True},
            {"ok": True},
        ]

        try:
            with patch(
                "dimos.robot.unitree.go2.modules.startup_bark.resolve_startup_audio_path",
                return_value=fake_path,
            ):
                with patch.object(module, "_load_audio_file_as_wav", return_value=wav_data):
                    with patch(
                        "dimos.robot.unitree.go2.modules.startup_bark.time.time",
                        return_value=1.234,
                    ):
                        module._play_on_robot()
        finally:
            global_config.replay = original_replay
            global_config.simulation = original_simulation

        expected_chunk = base64.b64encode(wav_data).decode("utf-8")
        assert len(expected_chunk) < GREETING_AUDIO_CHUNK_SIZE

        upload_call, list_call, mode_call, play_call = (
            module._connection.publish_request.call_args_list
        )
        assert upload_call.args[0] == RTC_TOPIC["AUDIO_HUB_REQ"]
        assert upload_call.args[1]["api_id"] == AUDIO_API["UPLOAD_AUDIO_FILE"]
        assert list_call.args[1]["api_id"] == AUDIO_API["GET_AUDIO_LIST"]
        assert mode_call.args[1]["api_id"] == AUDIO_API["SET_PLAY_MODE"]
        assert json.loads(mode_call.args[1]["parameter"]) == {"play_mode": PLAY_MODE_NO_CYCLE}
        assert play_call.args[1]["api_id"] == AUDIO_API["SELECT_START_PLAY"]
        assert json.loads(play_call.args[1]["parameter"]) == {"unique_id": audio_uuid}
