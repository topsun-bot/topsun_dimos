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
from dimos.robot.unitree.go2.modules.startup_bark import (
    GREETING_SPEAKER_ID,
    GREETING_TEXT,
    StartupBarkModule,
)


class TestStartupBarkModule:
    """Tests for StartupBarkModule."""

    def test_module_can_be_instantiated(self) -> None:
        """Module state can be prepared without triggering runtime threads."""
        module = object.__new__(StartupBarkModule)
        assert module is not None

    def test_start_schedules_greeting(self) -> None:
        """start() schedules the greeting timer without crashing."""
        module = object.__new__(StartupBarkModule)
        module._timer = None
        with patch("dimos.robot.unitree.go2.modules.startup_bark.Module.start"):
            with patch(
                "dimos.robot.unitree.go2.modules.startup_bark.threading.Timer"
            ) as mock_timer:
                mock_timer.return_value.start = MagicMock()
                with patch.object(module, "_greet") as mock_greet:
                    module.start()
                mock_timer.assert_called_once_with(2.0, mock_greet)
                mock_timer.return_value.start.assert_called_once_with()
        module._timer = MagicMock()
        with patch("dimos.robot.unitree.go2.modules.startup_bark.Module.stop"):
            module.stop()

    def test_stop_cleans_up_resources(self) -> None:
        """stop() cancels the timer without errors."""
        module = object.__new__(StartupBarkModule)
        timer = MagicMock()
        module._timer = timer
        with patch("dimos.robot.unitree.go2.modules.startup_bark.Module.stop"):
            module.stop()
        timer.cancel.assert_called_once_with()

    def test_greet_local_does_not_crash_in_replay_mode(self) -> None:
        """_greet_local() skips playback in replay mode."""
        original_replay = global_config.replay
        original_simulation = global_config.simulation
        global_config.replay = True
        global_config.simulation = False

        module = object.__new__(StartupBarkModule)

        try:
            module._greet_local()
        except Exception as e:
            pytest.fail(f"_greet_local raised unexpectedly: {e}")

        global_config.replay = original_replay
        global_config.simulation = original_simulation

    def test_greet_local_does_not_crash_in_sim_mode(self) -> None:
        """_greet_local() skips playback in simulation mode."""
        original_replay = global_config.replay
        original_simulation = global_config.simulation
        global_config.replay = False
        global_config.simulation = True

        module = object.__new__(StartupBarkModule)

        try:
            module._greet_local()
        except Exception as e:
            pytest.fail(f"_greet_local raised unexpectedly: {e}")

        global_config.replay = original_replay
        global_config.simulation = original_simulation

    def test_greeting_text_is_master_hello(self) -> None:
        """GREETING_TEXT is the on-device default Chinese greeting."""
        assert GREETING_TEXT == "主人 你好"
        assert GREETING_SPEAKER_ID == 0

    def test_greet_on_robot_calls_tts_maker(self) -> None:
        """_greet_on_robot() always routes through AudioClient.TtsMaker."""
        original_replay = global_config.replay
        original_simulation = global_config.simulation
        original_robot_ip = global_config.robot_ip
        global_config.replay = False
        global_config.simulation = False
        global_config.robot_ip = "10.10.197.111"

        module = object.__new__(StartupBarkModule)

        try:
            with patch(
                "dimos.robot.unitree.go2.modules.startup_bark.tts_maker", return_value=0
            ) as mock_tts:
                module._greet_on_robot()
                mock_tts.assert_called_once_with(
                    GREETING_TEXT,
                    speaker_id=GREETING_SPEAKER_ID,
                    network_interface=None,
                )
        finally:
            global_config.replay = original_replay
            global_config.simulation = original_simulation
            global_config.robot_ip = original_robot_ip

    def test_greet_on_robot_skips_without_robot_ip(self) -> None:
        """_greet_on_robot() is a no-op when no robot_ip is configured."""
        original_replay = global_config.replay
        original_simulation = global_config.simulation
        original_robot_ip = global_config.robot_ip
        global_config.replay = False
        global_config.simulation = False
        global_config.robot_ip = None

        module = object.__new__(StartupBarkModule)

        try:
            with patch(
                "dimos.robot.unitree.go2.modules.startup_bark.tts_maker"
            ) as mock_tts:
                module._greet_on_robot()
                mock_tts.assert_not_called()
        finally:
            global_config.replay = original_replay
            global_config.simulation = original_simulation
            global_config.robot_ip = original_robot_ip

    def test_greet_on_robot_logs_when_tts_returns_nonzero(self) -> None:
        """A non-zero return from TtsMaker must not raise out of _greet_on_robot."""
        original_replay = global_config.replay
        original_simulation = global_config.simulation
        original_robot_ip = global_config.robot_ip
        global_config.replay = False
        global_config.simulation = False
        global_config.robot_ip = "10.10.197.111"

        module = object.__new__(StartupBarkModule)

        try:
            with patch(
                "dimos.robot.unitree.go2.modules.startup_bark.tts_maker", return_value=42
            ):
                module._greet_on_robot()
        finally:
            global_config.replay = original_replay
            global_config.simulation = original_simulation
            global_config.robot_ip = original_robot_ip
