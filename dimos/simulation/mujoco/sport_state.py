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

"""Map Unitree Go2 Sport API (SDK2 / WebRTC) into MuJoCo shared-memory gait modifiers.

Official references (High-level motion control / Sport):
- https://support.unitree.com/home/en/developer
- unitree_sdk2 ``sport_client.hpp``: ``FootRaiseHeight``, ``FreeWalk``, ``CrossStep``, …
- DimOS WebRTC ids: ``unitree_webrtc_connect.constants.SPORT_CMD``

MuJoCo cannot execute onboard sport firmware; this module approximates stair-related
parameters for the Go1 ONNX locomotion policy (forward gain + front-leg lift bias).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
from numpy.typing import NDArray
from unitree_webrtc_connect.constants import SPORT_CMD

# Shared-memory layout: 8 float32 values, seq channel index 5 (see shared_memory.py).
SPORT_SHM_FLOATS = 8
SPORT_SEQ_INDEX = 5

SPORT_IDX_ACTIVE = 0
SPORT_IDX_FOOT_RAISE_M = 1
SPORT_IDX_BODY_HEIGHT_M = 2
SPORT_IDX_GAIT_ID = 3
SPORT_IDX_SPEED_LEVEL = 4
SPORT_IDX_CROSS_STEP = 5
SPORT_IDX_COMMAND_GAIN = 6
SPORT_IDX_ACTION_SCALE_BOOST = 7

DEFAULT_FOOT_RAISE_M = 0.06
DEFAULT_COMMAND_GAIN = 1.0
DEFAULT_ACTION_SCALE_BOOST = 1.0


@dataclass(frozen=True)
class SportSimGains:
    """Runtime modifiers read by ``Go1OnnxController`` in the MuJoCo subprocess."""

    active: bool
    foot_raise_m: float
    body_height_m: float
    cross_step: bool
    command_gain: float
    action_scale_boost: float


def _parameter_data(payload: dict[str, Any]) -> float | int | bool | None:
    param = payload.get("parameter")
    if not isinstance(param, dict):
        return None
    return param.get("data")


def _recompute_derived_gains(buf: NDArray[Any]) -> None:
    foot_raise = float(buf[SPORT_IDX_FOOT_RAISE_M])
    cross = float(buf[SPORT_IDX_CROSS_STEP]) > 0.5
    active = float(buf[SPORT_IDX_ACTIVE]) > 0.5

    if not active:
        buf[SPORT_IDX_COMMAND_GAIN] = DEFAULT_COMMAND_GAIN
        buf[SPORT_IDX_ACTION_SCALE_BOOST] = DEFAULT_ACTION_SCALE_BOOST
        return

    # Heuristic mapping from FootRaiseHeight (m) to ONNX policy drive (tunable).
    buf[SPORT_IDX_COMMAND_GAIN] = np.float32(1.0 + 2.5 * foot_raise + (0.15 if cross else 0.0))
    buf[SPORT_IDX_ACTION_SCALE_BOOST] = np.float32(1.0 + 3.0 * foot_raise)


def sport_array_to_gains(buf: NDArray[Any]) -> SportSimGains:
    return SportSimGains(
        active=float(buf[SPORT_IDX_ACTIVE]) > 0.5,
        foot_raise_m=float(buf[SPORT_IDX_FOOT_RAISE_M]),
        body_height_m=float(buf[SPORT_IDX_BODY_HEIGHT_M]),
        cross_step=float(buf[SPORT_IDX_CROSS_STEP]) > 0.5,
        command_gain=float(buf[SPORT_IDX_COMMAND_GAIN]),
        action_scale_boost=float(buf[SPORT_IDX_ACTION_SCALE_BOOST]),
    )


def default_sport_buffer() -> NDArray[Any]:
    buf = np.zeros(SPORT_SHM_FLOATS, dtype=np.float32)
    buf[SPORT_IDX_FOOT_RAISE_M] = DEFAULT_FOOT_RAISE_M
    buf[SPORT_IDX_COMMAND_GAIN] = DEFAULT_COMMAND_GAIN
    buf[SPORT_IDX_ACTION_SCALE_BOOST] = DEFAULT_ACTION_SCALE_BOOST
    return buf


def apply_sport_api_payload(buf: NDArray[Any], payload: dict[str, Any]) -> bool:
    """Update *buf* from a WebRTC ``SPORT_MOD`` request. Returns True if recognized."""
    api_id = payload.get("api_id")
    if api_id is None:
        return False

    data = _parameter_data(payload)

    if api_id == SPORT_CMD["FootRaiseHeight"] and data is not None:
        buf[SPORT_IDX_ACTIVE] = np.float32(1.0)
        buf[SPORT_IDX_FOOT_RAISE_M] = np.float32(float(data))
        _recompute_derived_gains(buf)
        return True

    if api_id == SPORT_CMD["BodyHeight"] and data is not None:
        buf[SPORT_IDX_ACTIVE] = np.float32(1.0)
        buf[SPORT_IDX_BODY_HEIGHT_M] = np.float32(float(data))
        _recompute_derived_gains(buf)
        return True

    if api_id == SPORT_CMD["SwitchGait"] and data is not None:
        buf[SPORT_IDX_ACTIVE] = np.float32(1.0)
        buf[SPORT_IDX_GAIT_ID] = np.float32(float(data))
        _recompute_derived_gains(buf)
        return True

    if api_id == SPORT_CMD["SpeedLevel"] and data is not None:
        buf[SPORT_IDX_ACTIVE] = np.float32(1.0)
        buf[SPORT_IDX_SPEED_LEVEL] = np.float32(float(data))
        _recompute_derived_gains(buf)
        return True

    if api_id == SPORT_CMD["CrossStep"]:
        buf[SPORT_IDX_ACTIVE] = np.float32(1.0)
        buf[SPORT_IDX_CROSS_STEP] = np.float32(1.0 if bool(data) else 0.0)
        _recompute_derived_gains(buf)
        return True

    if api_id == SPORT_CMD["EconomicGait"]:
        # Slight conservative drive when economic gait is requested on stairs.
        if bool(data):
            buf[SPORT_IDX_COMMAND_GAIN] = np.float32(float(buf[SPORT_IDX_COMMAND_GAIN]) * 0.85)
        _recompute_derived_gains(buf)
        return True

    if api_id in (SPORT_CMD["FreeWalk"], SPORT_CMD["BalanceStand"]):
        buf[SPORT_IDX_ACTIVE] = np.float32(1.0)
        _recompute_derived_gains(buf)
        return True

    return False


def clear_sport_buffer(buf: NDArray[Any]) -> None:
    buf[:] = default_sport_buffer()
