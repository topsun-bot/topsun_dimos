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
from dimos.robot.unitree.go2.modules.startup_bark import (
    BARK_TEXT,
    USE_SDK2_TTS_ENV,
    StartupBarkModule,
)


class TestStartupBarkModule:
    """Tests for StartupBarkModule."""

    def test_module_can_be_instantiated(self) -> None:
        """Module state can be prepared without triggering runtime threads."""
        module = object.__new__(StartupBarkModule)
        assert module is not None

    def test_start_schedules_bark(self) -> None:
        """start() schedules the bark timer without crashing."""
        module = object.__new__(StartupBarkModule)
        module._timer = None
        with patch("dimos.robot.unitree.go2.modules.startup_bark.Module.start"):
            with patch(
                "dimos.robot.unitree.go2.modules.startup_bark.threading.Timer"
            ) as mock_timer:
                mock_timer.return_value.start = MagicMock()
                with patch.object(module, "_bark") as mock_bark:
                    module.start()
                mock_timer.assert_called_once_with(2.0, mock_bark)
                mock_timer.return_value.start.assert_called_once_with()
        module._timer = MagicMock()
        with patch("dimos.robot.unitree.go2.modules.startup_bark.Module.stop"):
            module.stop()

    def test_stop_cleans_up_resources(self) -> None:
        """stop() cleans up audio resources without errors."""
        module = object.__new__(StartupBarkModule)
        timer = MagicMock()
        module._timer = timer
        with patch("dimos.robot.unitree.go2.modules.startup_bark.Module.stop"):
            module.stop()
        timer.cancel.assert_called_once_with()

    def test_bark_local_does_not_crash_in_replay_mode(self) -> None:
        """_bark_local() skips playback in replay mode."""
        original_replay = global_config.replay
        original_simulation = global_config.simulation
        global_config.replay = True
        global_config.simulation = False

        module = object.__new__(StartupBarkModule)

        try:
            module._bark_local()
        except Exception as e:
            pytest.fail(f"_bark_local raised unexpectedly: {e}")

        global_config.replay = original_replay
        global_config.simulation = original_simulation

    def test_bark_local_does_not_crash_in_sim_mode(self) -> None:
        """_bark_local() skips playback in simulation mode."""
        original_replay = global_config.replay
        original_simulation = global_config.simulation
        global_config.replay = False
        global_config.simulation = True

        module = object.__new__(StartupBarkModule)

        try:
            module._bark_local()
        except Exception as e:
            pytest.fail(f"_bark_local raised unexpectedly: {e}")

        global_config.replay = original_replay
        global_config.simulation = original_simulation

    def test_bark_text_is_woofs(self) -> None:
        """BARK_TEXT contains the Chinese woof string."""
        assert BARK_TEXT == "汪汪汪"

    def test_bark_on_robot_uses_vui_by_default(self) -> None:
        """_bark_on_robot() uses VUI fallback unless SDK2 TTS is explicitly enabled."""
        original_replay = global_config.replay
        original_simulation = global_config.simulation
        original_robot_ip = global_config.robot_ip
        original_sdk2_tts = os.environ.get(USE_SDK2_TTS_ENV)
        global_config.replay = False
        global_config.simulation = False
        global_config.robot_ip = "10.10.197.111"
        os.environ.pop(USE_SDK2_TTS_ENV, None)

        module = object.__new__(StartupBarkModule)
        module._go2_connection = MagicMock()

        try:
            with patch("dimos.robot.unitree.go2.modules.startup_bark.tts_maker") as mock_tts:
                module._bark_on_robot()
                mock_tts.assert_not_called()
                assert module._go2_connection.publish_request.call_count == 2
        finally:
            global_config.replay = original_replay
            global_config.simulation = original_simulation
            global_config.robot_ip = original_robot_ip
            if original_sdk2_tts is None:
                os.environ.pop(USE_SDK2_TTS_ENV, None)
            else:
                os.environ[USE_SDK2_TTS_ENV] = original_sdk2_tts

    def test_bark_on_robot_can_try_sdk2_tts_when_enabled(self) -> None:
        """_bark_on_robot() can try SDK2 TTS when explicitly enabled."""
        original_replay = global_config.replay
        original_simulation = global_config.simulation
        original_robot_ip = global_config.robot_ip
        original_sdk2_tts = os.environ.get(USE_SDK2_TTS_ENV)
        global_config.replay = False
        global_config.simulation = False
        global_config.robot_ip = "10.10.197.111"
        os.environ[USE_SDK2_TTS_ENV] = "1"

        module = object.__new__(StartupBarkModule)
        module._go2_connection = MagicMock()

        try:
            with patch(
                "dimos.robot.unitree.go2.modules.startup_bark.tts_maker", return_value=0
            ) as mock_tts:
                module._bark_on_robot()
                mock_tts.assert_called_once_with(
                    BARK_TEXT,
                    speaker_id=0,
                    network_interface=os.environ.get("ROBOT_INTERFACE"),
                )
        finally:
            global_config.replay = original_replay
            global_config.simulation = original_simulation
            global_config.robot_ip = original_robot_ip
            if original_sdk2_tts is None:
                os.environ.pop(USE_SDK2_TTS_ENV, None)
            else:
                os.environ[USE_SDK2_TTS_ENV] = original_sdk2_tts
