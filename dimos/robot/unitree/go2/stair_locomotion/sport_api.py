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

"""Go2 stair climbing via official SportClient actions (``WalkStair`` + ``Move``)."""

from __future__ import annotations

import time
from typing import Any, Protocol

from unitree_webrtc_connect.constants import RTC_TOPIC, SPORT_CMD

from dimos.core.global_config import GlobalConfig
from dimos.robot.unitree.go2.stair_locomotion.config import StairLocomotionConfig
from dimos.robot.unitree.go2.stair_locomotion.sport_actions import (
    API_WALK_STAIR,
    SportClientCall,
    balance_stand_call,
    body_height_call,
    cross_step_call,
    foot_raise_height_call,
    free_walk_call,
    speed_level_call,
    sport_payload,
    switch_gait_call,
    walk_stair_call,
)
from dimos.utils.logging_config import setup_logger

logger = setup_logger()


def _safe_get_attr(obj: Any, name: str) -> Any | None:
    """``getattr`` that does not probe unknown attrs on DimOS ``ModuleProxy`` (RPC)."""
    try:
        return getattr(obj, name)
    except (AttributeError, RuntimeError):
        return None


class Go2SportConnection(Protocol):
    """Minimal connection surface for stair locomotion (``GO2Connection`` implements this)."""

    def publish_request(self, topic: str, data: dict[str, Any]) -> dict[Any, Any]: ...

    def balance_stand(self) -> bool: ...

    def free_walk(self) -> bool: ...

    def set_obstacle_avoidance(self, enabled: bool = True) -> None: ...


# Re-export for tests / callers that still reference WebRTC ids.
API_BALANCE_STAND = SPORT_CMD["BalanceStand"]
API_FREE_WALK = SPORT_CMD["FreeWalk"]
API_FOOT_RAISE_HEIGHT = SPORT_CMD["FootRaiseHeight"]
API_CROSS_STEP = SPORT_CMD["CrossStep"]


class Go2StairSportController:
    """Enter/exit stair mode using ``SportClient``-defined sport actions."""

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
        self._stair_mode_active = False

    def _settle(self) -> None:
        if self._global_config is not None and self._global_config.simulation:
            return
        time.sleep(self._config.sport_settle_s)

    def _invoke(self, call: SportClientCall) -> None:
        """Publish the same payload ``SportClient`` would send over DDS."""
        payload = sport_payload(call)
        try:
            self._connection.publish_request(RTC_TOPIC["SPORT_MOD"], payload)
            logger.debug(
                "SportClient action",
                method=call.method,
                api_id=call.api_id,
            )
        except Exception as exc:
            logger.warning(
                "SportClient action failed",
                method=call.method,
                api_id=call.api_id,
                error=str(exc),
            )

    def _invoke_dds_sport_client(self, call: SportClientCall) -> bool:
        """Optional: call an attached ``unitree_sdk2py`` ``SportClient`` (DDS teleop path)."""
        client = _safe_get_attr(self._connection, "sport_client")
        if client is None:
            return False
        method = getattr(client, call.method, None)
        if method is None:
            return False
        try:
            if call.parameter is None:
                method()
            elif "data" in call.parameter:
                method(bool(call.parameter["data"]))
            else:
                method(**call.parameter)
            return True
        except Exception as exc:
            logger.warning(
                "DDS SportClient call failed",
                method=call.method,
                error=str(exc),
            )
            return False

    def _sport(self, call: SportClientCall) -> None:
        if not self._invoke_dds_sport_client(call):
            self._invoke(call)

    def prepare_locomotion(self) -> None:
        """``BalanceStand`` + ``FreeWalk`` before stair motion (official example order)."""
        if self._global_config is not None and self._global_config.simulation:
            self._sport(balance_stand_call())
            self._sport(free_walk_call())
            return
        self._connection.balance_stand()
        self._settle()
        self._connection.free_walk()
        self._settle()

    def _enter_stair_mode_manual(self) -> None:
        """Fallback: tune ``FootRaiseHeight`` / ``SwitchGait`` when ``WalkStair`` unavailable."""
        self._sport(speed_level_call(self._config.speed_level))
        self._settle()
        self._sport(foot_raise_height_call(self._config.foot_raise_height_m))
        self._settle()
        self._sport(body_height_call(self._config.body_height_delta_m))
        self._settle()
        self._sport(switch_gait_call(self._config.gait_id))
        self._settle()
        if self._config.use_economic_gait:
            self._sport(
                SportClientCall("EconomicGait", SPORT_CMD["EconomicGait"], {"data": True})
            )
        if self._config.use_cross_step:
            self._sport(cross_step_call(True))
            self._settle()

    def enter_stair_mode(self) -> None:
        if self._stair_mode_active:
            return

        if self._config.disable_obstacle_avoidance_on_stair:
            try:
                self._connection.set_obstacle_avoidance(False)
            except Exception:
                logger.debug("set_obstacle_avoidance unavailable on this connection")

        self.prepare_locomotion()

        if self._config.stair_sport_mode == "walk_stair":
            self._sport(walk_stair_call(True))
            self._settle()
            action = "WalkStair(True)"
        else:
            self._enter_stair_mode_manual()
            action = "manual FootRaiseHeight/SwitchGait"

        self._stair_mode_active = True
        sim_note = ""
        if self._global_config is not None and self._global_config.simulation:
            backend = getattr(self._global_config, "mujoco_backend", "dimos")
            sim_note = (
                " (Unitree MuJoCo mirrors SportClient via SHM)"
                if backend == "unitree"
                else " (DimOS MuJoCo mirrors SportClient via SHM)"
            )
        logger.info(
            "Go2 stair sport mode active",
            sport_action=action,
            stair_sport_mode=self._config.stair_sport_mode,
            note=sim_note,
        )

    def exit_stair_mode(self) -> None:
        if not self._stair_mode_active:
            return

        if self._config.stair_sport_mode == "walk_stair":
            self._sport(walk_stair_call(False))
        else:
            self._sport(foot_raise_height_call(0.06))
            self._sport(body_height_call(0.0))
            self._sport(speed_level_call(1))
            self._sport(switch_gait_call(0))
            if self._config.use_cross_step:
                self._sport(cross_step_call(False))

        if self._config.disable_obstacle_avoidance_on_stair:
            try:
                self._connection.set_obstacle_avoidance(True)
            except Exception:
                pass

        self._stair_mode_active = False
        if self._global_config is not None and self._global_config.simulation:
            clearer = _safe_get_attr(self._connection, "clear_sim_sport_state")
            if callable(clearer):
                clearer()
        logger.info("Go2 stair sport mode cleared")
