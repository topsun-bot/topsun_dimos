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

"""Startup greeting module for Unitree Go2 robot.

Speaks a short greeting through the robot's on-device voice service
(``AudioClient.TtsMaker``) shortly after the WebRTC connection is up. This
uses Unitree's built-in TTS (``speaker_id=0`` is the default Chinese voice),
not a cloud synthesis service.
"""

import os
import threading

from dimos.core.core import rpc
from dimos.core.global_config import global_config
from dimos.core.module import Module
from dimos.robot.unitree.sdk2_audio import tts_maker
from dimos.utils.logging_config import setup_logger

logger = setup_logger()

GREETING_TEXT = "主人 你好"
GREETING_SPEAKER_ID = 0  # Unitree's default Chinese voice
ROBOT_INTERFACE_ENV = "ROBOT_INTERFACE"


class StartupBarkModule(Module):
    """Speaks ``主人 你好`` once after the Go2 connection is established."""

    _timer: threading.Timer | None = None

    @rpc
    def start(self) -> None:
        super().start()
        logger.info("StartupBarkModule starting, scheduling greeting in 2 seconds")
        self._timer = threading.Timer(2.0, self._greet)
        self._timer.start()

    @rpc
    def stop(self) -> None:
        if self._timer:
            self._timer.cancel()
            self._timer = None
        super().stop()

    def _greet(self) -> None:
        """Trigger the greeting based on current mode."""
        try:
            if global_config.replay or global_config.simulation:
                self._greet_local()
            else:
                self._greet_on_robot()
        except Exception as e:
            logger.error(f"Error during startup greeting: {e}")

    def _greet_local(self) -> None:
        """Skip greeting playback when not connected to a physical robot."""
        logger.info("Skipping startup greeting in replay/simulation mode")

    def _greet_on_robot(self) -> None:
        """Trigger Unitree's on-device TTS over the SDK2 voice service."""
        robot_ip = global_config.robot_ip
        if not robot_ip:
            logger.warning("No robot IP configured, skipping startup greeting")
            return

        network_interface = os.getenv(ROBOT_INTERFACE_ENV)
        logger.info(
            f"Triggering startup greeting via SDK2 TTS robot_ip={robot_ip} "
            f"network_interface={network_interface} text={GREETING_TEXT!r}"
        )
        ret = tts_maker(
            GREETING_TEXT,
            speaker_id=GREETING_SPEAKER_ID,
            network_interface=network_interface,
        )
        if ret != 0:
            logger.error(f"AudioClient.TtsMaker returned {ret}; greeting not played")
            return
        logger.info("Startup greeting sent")
