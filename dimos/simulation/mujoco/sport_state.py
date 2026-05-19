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
from enum import IntEnum
from typing import Any

import numpy as np
from numpy.typing import NDArray
from unitree_webrtc_connect.constants import SPORT_CMD

# Shared-memory layout: float32 values, seq channel index 5 (see shared_memory.py).
SPORT_SHM_FLOATS = 9
SPORT_SEQ_INDEX = 5

SPORT_IDX_ACTIVE = 0
SPORT_IDX_FOOT_RAISE_M = 1
SPORT_IDX_BODY_HEIGHT_M = 2
SPORT_IDX_GAIT_ID = 3
SPORT_IDX_SPEED_LEVEL = 4
SPORT_IDX_CROSS_STEP = 5
SPORT_IDX_COMMAND_GAIN = 6
SPORT_IDX_ACTION_SCALE_BOOST = 7
SPORT_IDX_EXEC_MODE = 8

DEFAULT_FOOT_RAISE_M = 0.06
DEFAULT_COMMAND_GAIN = 1.0
DEFAULT_ACTION_SCALE_BOOST = 1.0

# ONNX destabilizes above ~1.3× command / ~1.35× action on discrete MuJoCo treads.
STAIR_SIM_MAX_COMMAND_GAIN = 1.22
STAIR_SIM_MAX_ACTION_SCALE_BOOST = 1.28

# SwitchGait ids that commonly select stair / climb profiles on Go2 (app-dependent).
_STAIR_GAIT_IDS = frozenset({1, 2})


class SportExecMode(IntEnum):
    """Mirrors SDK2 stair-related sport *execution* styles in simulation."""

    NORMAL = 0
    STAIR = 1
    CROSS_STEP = 2
    CROSS_WALK = 3
    ONESIDED_STEP = 4


@dataclass(frozen=True)
class SportSimGains:
    """Runtime modifiers read by ``Go1OnnxController`` in the MuJoCo subprocess."""

    active: bool
    foot_raise_m: float
    body_height_m: float
    cross_step: bool
    command_gain: float
    action_scale_boost: float
    exec_mode: SportExecMode = SportExecMode.NORMAL


def _parameter_data(payload: dict[str, Any]) -> float | int | bool | None:
    param = payload.get("parameter")
    if not isinstance(param, dict):
        return None
    return param.get("data")


def _set_exec_mode(buf: NDArray[Any], mode: SportExecMode) -> None:
    buf[SPORT_IDX_EXEC_MODE] = np.float32(float(mode.value))
    if mode == SportExecMode.CROSS_STEP:
        buf[SPORT_IDX_CROSS_STEP] = np.float32(1.0)
    elif mode in (SportExecMode.NORMAL, SportExecMode.STAIR):
        if float(buf[SPORT_IDX_CROSS_STEP]) < 0.5:
            buf[SPORT_IDX_CROSS_STEP] = np.float32(0.0)


def _recompute_derived_gains(buf: NDArray[Any]) -> None:
    foot_raise = float(buf[SPORT_IDX_FOOT_RAISE_M])
    cross = float(buf[SPORT_IDX_CROSS_STEP]) > 0.5
    active = float(buf[SPORT_IDX_ACTIVE]) > 0.5
    body_h = float(buf[SPORT_IDX_BODY_HEIGHT_M])
    speed_level = float(buf[SPORT_IDX_SPEED_LEVEL])
    try:
        exec_mode = SportExecMode(int(round(float(buf[SPORT_IDX_EXEC_MODE]))))
    except ValueError:
        exec_mode = SportExecMode.NORMAL

    if not active:
        buf[SPORT_IDX_COMMAND_GAIN] = DEFAULT_COMMAND_GAIN
        buf[SPORT_IDX_ACTION_SCALE_BOOST] = DEFAULT_ACTION_SCALE_BOOST
        return

    # Heuristic mapping from FootRaiseHeight (m) to ONNX policy drive (tunable).
    cmd_gain = 1.0 + 3.2 * foot_raise
    action_boost = 1.0 + 4.5 * foot_raise
    if foot_raise >= 0.1:
        cmd_gain += 0.12
        action_boost += 0.15

    if cross or exec_mode == SportExecMode.CROSS_STEP:
        cmd_gain += 0.15
        action_boost += 0.08

    if exec_mode == SportExecMode.CROSS_WALK:
        cmd_gain += 0.08
        action_boost += 0.12

    if exec_mode == SportExecMode.ONESIDED_STEP:
        cmd_gain += 0.1
        action_boost += 0.18

    if exec_mode == SportExecMode.STAIR:
        cmd_gain += 0.05

    # Lower body (negative BodyHeight) → slightly more forward drive for climb.
    if body_h < -0.005:
        cmd_gain += 0.08

    # SpeedLevel 0 = slow stair profile on hardware.
    if speed_level < 0.5:
        cmd_gain *= 0.92

    if foot_raise >= 0.08:
        cmd_gain = min(cmd_gain, STAIR_SIM_MAX_COMMAND_GAIN)
        action_boost = min(action_boost, STAIR_SIM_MAX_ACTION_SCALE_BOOST)

    buf[SPORT_IDX_COMMAND_GAIN] = np.float32(cmd_gain)
    buf[SPORT_IDX_ACTION_SCALE_BOOST] = np.float32(action_boost)


def sport_array_to_gains(buf: NDArray[Any]) -> SportSimGains:
    try:
        exec_mode = SportExecMode(int(round(float(buf[SPORT_IDX_EXEC_MODE]))))
    except ValueError:
        exec_mode = SportExecMode.NORMAL
    return SportSimGains(
        active=float(buf[SPORT_IDX_ACTIVE]) > 0.5,
        foot_raise_m=float(buf[SPORT_IDX_FOOT_RAISE_M]),
        body_height_m=float(buf[SPORT_IDX_BODY_HEIGHT_M]),
        cross_step=float(buf[SPORT_IDX_CROSS_STEP]) > 0.5,
        command_gain=float(buf[SPORT_IDX_COMMAND_GAIN]),
        action_scale_boost=float(buf[SPORT_IDX_ACTION_SCALE_BOOST]),
        exec_mode=exec_mode,
    )


def default_sport_buffer() -> NDArray[Any]:
    buf = np.zeros(SPORT_SHM_FLOATS, dtype=np.float32)
    buf[SPORT_IDX_FOOT_RAISE_M] = DEFAULT_FOOT_RAISE_M
    buf[SPORT_IDX_COMMAND_GAIN] = DEFAULT_COMMAND_GAIN
    buf[SPORT_IDX_ACTION_SCALE_BOOST] = DEFAULT_ACTION_SCALE_BOOST
    buf[SPORT_IDX_EXEC_MODE] = np.float32(float(SportExecMode.NORMAL.value))
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
        _set_exec_mode(buf, SportExecMode.STAIR)
        _recompute_derived_gains(buf)
        return True

    if api_id == SPORT_CMD["BodyHeight"] and data is not None:
        buf[SPORT_IDX_ACTIVE] = np.float32(1.0)
        buf[SPORT_IDX_BODY_HEIGHT_M] = np.float32(float(data))
        _recompute_derived_gains(buf)
        return True

    if api_id == SPORT_CMD["SwitchGait"] and data is not None:
        buf[SPORT_IDX_ACTIVE] = np.float32(1.0)
        gait_id = int(float(data))
        buf[SPORT_IDX_GAIT_ID] = np.float32(float(gait_id))
        if gait_id in _STAIR_GAIT_IDS:
            _set_exec_mode(buf, SportExecMode.STAIR)
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
        if bool(data):
            _set_exec_mode(buf, SportExecMode.CROSS_STEP)
        _recompute_derived_gains(buf)
        return True

    if api_id == SPORT_CMD["CrossWalk"]:
        buf[SPORT_IDX_ACTIVE] = np.float32(1.0)
        _set_exec_mode(buf, SportExecMode.CROSS_WALK)
        _recompute_derived_gains(buf)
        return True

    if api_id == SPORT_CMD["OnesidedStep"]:
        buf[SPORT_IDX_ACTIVE] = np.float32(1.0)
        _set_exec_mode(buf, SportExecMode.ONESIDED_STEP)
        _recompute_derived_gains(buf)
        return True

    if api_id == SPORT_CMD["Bound"]:
        buf[SPORT_IDX_ACTIVE] = np.float32(1.0)
        _set_exec_mode(buf, SportExecMode.STAIR)
        buf[SPORT_IDX_COMMAND_GAIN] = np.float32(float(buf[SPORT_IDX_COMMAND_GAIN]) * 1.1)
        _recompute_derived_gains(buf)
        return True

    if api_id == SPORT_CMD["EconomicGait"]:
        if bool(data):
            buf[SPORT_IDX_COMMAND_GAIN] = np.float32(float(buf[SPORT_IDX_COMMAND_GAIN]) * 0.85)
        _recompute_derived_gains(buf)
        return True

    if api_id == SPORT_CMD["FreeWalk"]:
        buf[SPORT_IDX_ACTIVE] = np.float32(1.0)
        if float(buf[SPORT_IDX_FOOT_RAISE_M]) > DEFAULT_FOOT_RAISE_M + 0.02:
            _set_exec_mode(buf, SportExecMode.STAIR)
        _recompute_derived_gains(buf)
        return True

    if api_id == SPORT_CMD["BalanceStand"]:
        buf[SPORT_IDX_ACTIVE] = np.float32(1.0)
        _recompute_derived_gains(buf)
        return True

    return False


def clear_sport_buffer(buf: NDArray[Any]) -> None:
    buf[:] = default_sport_buffer()
