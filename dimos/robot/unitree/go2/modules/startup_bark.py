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

"""Startup bark module for Unitree Go2 robot."""

import os
import threading

from unitree_webrtc_connect.constants import RTC_TOPIC

from dimos.core.core import rpc
from dimos.core.global_config import global_config
from dimos.core.module import Module
from dimos.robot.unitree.go2.connection_spec import GO2ConnectionSpec
from dimos.robot.unitree.sdk2_audio import tts_maker
from dimos.utils.logging_config import setup_logger

logger = setup_logger()

BARK_TEXT = "汪汪汪"

VUI_API = {
    "SET_SWITCH": 1001,
    "SET_VOLUME": 1003,
}
STARTUP_VUI_VOLUME = 7
USE_SDK2_TTS_ENV = "DIMOS_UNITREE_AUDIO_TTS"


class StartupBarkModule(Module):
    """Triggers the robot's built-in startup bark after connection."""

    _go2_connection: GO2ConnectionSpec | None = None
    _timer: threading.Timer | None = None

    @rpc
    def start(self) -> None:
        super().start()
        logger.info("StartupBarkModule starting, scheduling bark in 2 seconds")
        self._timer = threading.Timer(2.0, self._bark)
        self._timer.start()

    @rpc
    def stop(self) -> None:
        if self._timer:
            self._timer.cancel()
            self._timer = None
        super().stop()

    def _bark(self) -> None:
        """Trigger the bark sound based on current mode."""
        try:
            if global_config.replay or global_config.simulation:
                self._bark_local()
            else:
                self._bark_on_robot()
        except Exception as e:
            logger.error(f"Error during bark: {e}")

    def _bark_local(self) -> None:
        """Skip bark playback when not connected to a physical robot."""
        logger.info("Skipping startup bark in replay/simulation mode")

    def _bark_on_robot(self) -> None:
        """Trigger VUI by default, optionally attempting SDK2 TTS first."""

        robot_ip = global_config.robot_ip
        if not robot_ip:
            logger.warning("No robot IP configured, skipping bark")
            return

        if os.getenv(USE_SDK2_TTS_ENV) == "1":
            network_interface = global_config.robot_interface or os.getenv("ROBOT_INTERFACE")
            logger.info(
                f"Triggering startup bark via SDK2 TTS robot_ip={robot_ip} "
                f"network_interface={network_interface}"
            )
            try:
                ret = tts_maker(BARK_TEXT, speaker_id=0, network_interface=network_interface)
                if ret != 0:
                    raise RuntimeError(f"AudioClient.TtsMaker returned {ret}")
                logger.info("SDK2 TTS startup bark sent")
                return
            except Exception as exc:
                logger.warning(f"SDK2 TTS failed, falling back to VUI: {exc}")

        self._vui_fallback()

    def _vui_fallback(self) -> None:
        """Fallback to the previously working VUI service."""
        connection = self._go2_connection
        if connection is None:
            raise RuntimeError("GO2Connection not available for VUI fallback")

        logger.info("Triggering startup bark via VUI fallback")
        connection.publish_request(
            RTC_TOPIC["VUI"],
            {
                "api_id": VUI_API["SET_VOLUME"],
                "parameter": {"volume": STARTUP_VUI_VOLUME},
            },
        )
        connection.publish_request(
            RTC_TOPIC["VUI"],
            {
                "api_id": VUI_API["SET_SWITCH"],
                "parameter": {"enable": 1},
            },
        )
        logger.info("VUI startup bark request sent")
