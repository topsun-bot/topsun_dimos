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

"""Map ``unitree_sdk2py.go2.sport.SportClient`` actions to Sport API ids.

Official reference (运控服务接口 / Python 示例 ``TestOption``)::

    TestOption(name="walk stair", id=16)  →  ``sport_client.WalkStair(True/False)``

``WalkStair`` was removed from SDK2 Python bindings for firmware V1.1.6+, but the
onboard sport service still accepts API **1049** on many Go2 Edu units. DimOS
invokes the same id via WebRTC ``SPORT_MOD`` (and MuJoCo SHM in simulation).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from unitree_webrtc_connect.constants import SPORT_CMD

# Pre-V1.1.6 SDK2: ROBOT_SPORT_API_ID_WALKSTAIR (not in current sport_api.hpp).
API_WALK_STAIR = 1049

StairSportMode = Literal["walk_stair", "manual"]


@dataclass(frozen=True)
class SportClientCall:
    """One ``SportClient`` method invocation (DDS / WebRTC parity)."""

    method: str
    api_id: int
    parameter: dict[str, Any] | None = None


def walk_stair_call(enabled: bool) -> SportClientCall:
    """``SportClient.WalkStair(flag)`` — official stair climbing mode."""
    return SportClientCall("WalkStair", API_WALK_STAIR, {"data": enabled})


def balance_stand_call() -> SportClientCall:
    return SportClientCall("BalanceStand", SPORT_CMD["BalanceStand"])


def free_walk_call() -> SportClientCall:
    return SportClientCall("FreeWalk", SPORT_CMD["FreeWalk"])


def cross_step_call(enabled: bool) -> SportClientCall:
    return SportClientCall("CrossStep", SPORT_CMD["CrossStep"], {"data": enabled})


def foot_raise_height_call(height_m: float) -> SportClientCall:
    return SportClientCall(
        "FootRaiseHeight",
        SPORT_CMD["FootRaiseHeight"],
        {"data": height_m},
    )


def body_height_call(delta_m: float) -> SportClientCall:
    return SportClientCall("BodyHeight", SPORT_CMD["BodyHeight"], {"data": delta_m})


def speed_level_call(level: int) -> SportClientCall:
    return SportClientCall("SpeedLevel", SPORT_CMD["SpeedLevel"], {"data": level})


def switch_gait_call(gait_id: int) -> SportClientCall:
    return SportClientCall("SwitchGait", SPORT_CMD["SwitchGait"], {"data": gait_id})


def sport_payload(call: SportClientCall) -> dict[str, Any]:
    payload: dict[str, Any] = {"api_id": call.api_id}
    if call.parameter is not None:
        payload["parameter"] = call.parameter
    return payload
