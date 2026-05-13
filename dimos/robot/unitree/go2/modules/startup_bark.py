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

"""Startup VUI cue module for Unitree Go2 robot."""

import threading

from unitree_webrtc_connect.constants import RTC_TOPIC

from dimos.core.core import rpc
from dimos.core.global_config import global_config
from dimos.core.module import Module
from dimos.robot.unitree.go2.connection_spec import GO2ConnectionSpec
from dimos.utils.logging_config import setup_logger

logger = setup_logger()

BARK_TEXT = "汪汪 汪汪 汪汪 汪汪汪"

VUI_API = {
    "SET_SWITCH": 1001,
    "SET_VOLUME": 1003,
}
STARTUP_VUI_VOLUME = 7


class StartupBarkModule(Module):
    """Triggers the robot's built-in VUI startup sound after connection.

    Real robot mode uses the Unitree VUI service directly. Replay and
    simulation skip playback because there is no physical VUI service.
    """

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
        self._go2_connection = None
        super().stop()

    def _bark(self) -> None:
        """Trigger the bark sound based on current mode."""
        try:
            self._bark_on_robot()
        except Exception as e:
            logger.error(f"Error during bark: {e}")

    def _bark_local(self) -> None:
        """Skip bark playback when no physical robot VUI service is available."""
        logger.info("Skipping startup bark in replay/simulation mode")

    def _bark_on_robot(self) -> None:
        """Trigger the robot's built-in VUI startup sound."""
        if global_config.replay or global_config.simulation:
            self._bark_local()
            return

        robot_ip = global_config.robot_ip
        if not robot_ip:
            logger.warning("No robot IP configured, skipping bark")
            return

        logger.info(f"Triggering startup bark via VUI service on robot at {robot_ip}")
        self._vui_request(VUI_API["SET_VOLUME"], {"volume": STARTUP_VUI_VOLUME})
        self._vui_request(VUI_API["SET_SWITCH"], {"enable": 1})
        logger.info("VUI startup bark request sent")

    def _vui_request(self, api_id: int, parameter: dict[str, int]) -> object:
        """Send a request to the Unitree VUI service via GO2Connection RPC."""
        if self._go2_connection is None:
            raise RuntimeError(
                "GO2Connection not available; bark may have fired before connection was established"
            )
        return self._go2_connection.publish_request(
            RTC_TOPIC["VUI"],
            {
                "api_id": api_id,
                "parameter": parameter,
            },
        )
