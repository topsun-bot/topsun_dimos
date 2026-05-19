# Copyright 2026 Dimensional Inc.
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

"""Unitree Go2 Sport API helpers (WebRTC ``SPORT_MOD`` / SDK2 ``SportClient`` parity).

API ids align with ``unitree_skill_container.UNITREE_WEBRTC_CONTROLS`` and
unitree_sdk2 ``sport_client.hpp``.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Protocol

from unitree_webrtc_connect.constants import RTC_TOPIC, SPORT_CMD

from dimos.core.global_config import GlobalConfig
from dimos.robot.unitree.go2.stair_locomotion.config import StairLocomotionConfig
from dimos.utils.logging_config import setup_logger

logger = setup_logger()


class Go2SportConnection(Protocol):
    """Minimal connection surface for stair locomotion (``GO2Connection`` implements this)."""

    def publish_request(self, topic: str, data: dict[str, Any]) -> dict[Any, Any]: ...

    def balance_stand(self) -> bool: ...

    def free_walk(self) -> bool: ...

    def set_obstacle_avoidance(self, enabled: bool = True) -> None: ...


# Sport API ids — keep in sync with ``unitree_webrtc_connect.constants.SPORT_CMD``
API_BALANCE_STAND = SPORT_CMD["BalanceStand"]
API_FREE_WALK = SPORT_CMD["FreeWalk"]
API_SWITCH_GAIT = SPORT_CMD["SwitchGait"]
API_BODY_HEIGHT = SPORT_CMD["BodyHeight"]
API_FOOT_RAISE_HEIGHT = SPORT_CMD["FootRaiseHeight"]
API_SPEED_LEVEL = SPORT_CMD["SpeedLevel"]
API_ECONOMIC_GAIT = SPORT_CMD["EconomicGait"]
API_CROSS_STEP = SPORT_CMD["CrossStep"]
API_CROSS_WALK = SPORT_CMD["CrossWalk"]
API_ONESIDED_STEP = SPORT_CMD["OnesidedStep"]


@dataclass
class _SportSnapshot:
    foot_raise_height_m: float = 0.06
    body_height_m: float = 0.0
    speed_level: int = 1
    gait_id: int = 0


class Go2StairSportController:
    """Configure / restore Go2 sport parameters for straight stair climbing."""

    def __init__(
        self,
        connection: Go2SportConnection,
        config: StairLocomotionConfig,
        *,
        global_config: GlobalConfig | None = None,
    ) -> None:
        self._connection = connection
        self._config = config
        self._global_config = global_config
        self._snapshot = _SportSnapshot()
        self._stair_mode_active = False

    def _settle(self) -> None:
        """Sleep after Sport API calls on hardware; no-op in MuJoCo sim."""
        if self._global_config is not None and self._global_config.simulation:
            return
        time.sleep(self._config.sport_settle_s)

    def _sport(self, api_id: int, data: float | int | bool | None = None) -> None:
        payload: dict[str, Any] = {"api_id": api_id}
        if data is not None:
            payload["parameter"] = {"data": data}
        try:
            self._connection.publish_request(RTC_TOPIC["SPORT_MOD"], payload)
        except Exception as exc:
            logger.warning("Sport API call failed", api_id=api_id, error=str(exc))

    def prepare_locomotion(self) -> None:
        """BalanceStand + FreeWalk — required before velocity / stair sport tweaks."""
        if self._global_config is not None and self._global_config.simulation:
            self._sport(API_BALANCE_STAND)
            self._sport(API_FREE_WALK)
            return
        self._connection.balance_stand()
        self._settle()
        self._connection.free_walk()
        self._settle()

    def enter_stair_mode(self) -> None:
        """Apply stair-friendly gait:抬脚高度、机身高度、步态、低速."""
        if self._stair_mode_active:
            return

        if self._config.disable_obstacle_avoidance_on_stair:
            try:
                self._connection.set_obstacle_avoidance(False)
            except Exception:
                logger.debug("set_obstacle_avoidance unavailable on this connection")

        self.prepare_locomotion()

        self._sport(API_SPEED_LEVEL, self._config.speed_level)
        self._settle()

        self._sport(API_FOOT_RAISE_HEIGHT, self._config.foot_raise_height_m)
        self._settle()

        self._sport(API_BODY_HEIGHT, self._config.body_height_delta_m)
        self._settle()

        self._sport(API_SWITCH_GAIT, self._config.gait_id)
        self._settle()

        if self._config.use_economic_gait:
            self._sport(API_ECONOMIC_GAIT, True)

        if self._config.use_cross_step:
            self._sport(API_CROSS_STEP, True)
            self._settle()

        self._stair_mode_active = True
        sim_note = ""
        if self._global_config is not None and self._global_config.simulation:
            backend = getattr(self._global_config, "mujoco_backend", "dimos")
            sim_note = (
                " (Unitree MuJoCo + ONNX mirrors Sport API via SHM)"
                if backend == "unitree"
                else " (DimOS MuJoCo mirrors Sport API via SHM)"
            )
        logger.info(
            "Go2 stair sport mode active",
            foot_raise_m=self._config.foot_raise_height_m,
            speed_level=self._config.speed_level,
            gait_id=self._config.gait_id,
            note=sim_note,
        )

    def exit_stair_mode(self) -> None:
        """Restore conservative defaults after leaving the stair corridor."""
        if not self._stair_mode_active:
            return

        self._sport(API_FOOT_RAISE_HEIGHT, self._snapshot.foot_raise_height_m)
        self._sport(API_BODY_HEIGHT, self._snapshot.body_height_m)
        self._sport(API_SPEED_LEVEL, self._snapshot.speed_level)
        self._sport(API_SWITCH_GAIT, self._snapshot.gait_id)
        if self._config.use_cross_step:
            self._sport(API_CROSS_STEP, False)

        if self._config.disable_obstacle_avoidance_on_stair:
            try:
                self._connection.set_obstacle_avoidance(True)
            except Exception:
                pass

        self._stair_mode_active = False
        if self._global_config is not None and self._global_config.simulation:
            clearer = getattr(self._connection, "clear_sim_sport_state", None)
            if callable(clearer):
                clearer()
        logger.info("Go2 stair sport mode cleared")
