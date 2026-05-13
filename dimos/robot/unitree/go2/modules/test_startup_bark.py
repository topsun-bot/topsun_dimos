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

from unittest.mock import MagicMock, call, patch

import pytest
from unitree_webrtc_connect.constants import RTC_TOPIC

from dimos.core.global_config import global_config
from dimos.robot.unitree.go2.modules.startup_bark import (
    BARK_TEXT,
    STARTUP_VUI_VOLUME,
    VUI_API,
    StartupBarkModule,
)


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
        """_bark_local() skips playback in replay mode."""
        original_replay = global_config.replay
        original_simulation = global_config.simulation
        global_config.replay = True
        global_config.simulation = False

        module = StartupBarkModule()

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

        module = StartupBarkModule()

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

    def test_bark_on_robot_sends_vui_requests(self) -> None:
        """_bark_on_robot() sends volume and switch requests to VUI in real mode."""
        original_replay = global_config.replay
        original_simulation = global_config.simulation
        original_robot_ip = global_config.robot_ip
        global_config.replay = False
        global_config.simulation = False
        global_config.robot_ip = "192.168.1.1"

        module = StartupBarkModule()
        module._go2_connection = MagicMock()

        module._bark_on_robot()

        assert module._go2_connection.publish_request.call_args_list == [
            call(
                RTC_TOPIC["VUI"],
                {"api_id": VUI_API["SET_VOLUME"], "parameter": {"volume": STARTUP_VUI_VOLUME}},
            ),
            call(RTC_TOPIC["VUI"], {"api_id": VUI_API["SET_SWITCH"], "parameter": {"enable": 1}}),
        ]

        global_config.replay = original_replay
        global_config.simulation = original_simulation
        global_config.robot_ip = original_robot_ip

    def test_vui_request_raises_without_connection(self) -> None:
        """_vui_request() fails clearly before GO2Connection is injected."""
        module = StartupBarkModule()

        with pytest.raises(RuntimeError, match="GO2Connection not available"):
            module._vui_request(VUI_API["SET_SWITCH"], {"enable": 1})

    def test_bark_text_is_five_woofs(self) -> None:
        """BARK_TEXT contains 5 bark instances as per spec."""
        assert BARK_TEXT == "汪汪 汪汪 汪汪 汪汪汪"
