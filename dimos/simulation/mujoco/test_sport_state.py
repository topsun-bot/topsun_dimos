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

from __future__ import annotations

import numpy as np
from unitree_webrtc_connect.constants import SPORT_CMD

from dimos.robot.unitree.go2.stair_locomotion.sport_actions import API_WALK_STAIR
from dimos.simulation.mujoco.sport_state import (
    WALK_STAIR_SIM_FOOT_RAISE_M,
    SPORT_IDX_ACTIVE,
    SPORT_IDX_COMMAND_GAIN,
    SPORT_IDX_FOOT_RAISE_M,
    SportExecMode,
    apply_sport_api_payload,
    default_sport_buffer,
    sport_array_to_gains,
)


def test_foot_raise_height_updates_sim_gains() -> None:
    buf = default_sport_buffer()
    ok = apply_sport_api_payload(
        buf,
        {"api_id": SPORT_CMD["FootRaiseHeight"], "parameter": {"data": 0.12}},
    )
    assert ok
    gains = sport_array_to_gains(buf)
    assert gains.active
    assert abs(gains.foot_raise_m - 0.12) < 1e-5
    assert gains.exec_mode == SportExecMode.STAIR
    assert float(buf[SPORT_IDX_ACTIVE]) == 1.0
    assert float(buf[SPORT_IDX_COMMAND_GAIN]) > 1.0


def test_cross_step_increases_command_gain() -> None:
    buf = default_sport_buffer()
    apply_sport_api_payload(
        buf,
        {"api_id": SPORT_CMD["FootRaiseHeight"], "parameter": {"data": 0.12}},
    )
    base_gain = float(buf[SPORT_IDX_COMMAND_GAIN])
    apply_sport_api_payload(buf, {"api_id": SPORT_CMD["CrossStep"], "parameter": {"data": True}})
    assert float(buf[SPORT_IDX_COMMAND_GAIN]) > base_gain
    assert sport_array_to_gains(buf).exec_mode == SportExecMode.CROSS_STEP


def test_cross_walk_sets_exec_mode() -> None:
    buf = default_sport_buffer()
    apply_sport_api_payload(buf, {"api_id": SPORT_CMD["CrossWalk"]})
    assert sport_array_to_gains(buf).exec_mode == SportExecMode.CROSS_WALK


def test_walk_stair_enables_stair_sim_profile() -> None:
    buf = default_sport_buffer()
    ok = apply_sport_api_payload(
        buf,
        {"api_id": API_WALK_STAIR, "parameter": {"data": True}},
    )
    assert ok
    gains = sport_array_to_gains(buf)
    assert gains.active
    assert abs(gains.foot_raise_m - WALK_STAIR_SIM_FOOT_RAISE_M) < 1e-5
    assert gains.exec_mode == SportExecMode.WALK_STAIR
    assert gains.command_gain <= 1.2


def test_walk_stair_disable_clears_active() -> None:
    buf = default_sport_buffer()
    apply_sport_api_payload(buf, {"api_id": API_WALK_STAIR, "parameter": {"data": True}})
    apply_sport_api_payload(buf, {"api_id": API_WALK_STAIR, "parameter": {"data": False}})
    gains = sport_array_to_gains(buf)
    assert not gains.active


def test_switch_gait_stair_id_sets_stair_mode() -> None:
    buf = default_sport_buffer()
    apply_sport_api_payload(
        buf,
        {"api_id": SPORT_CMD["SwitchGait"], "parameter": {"data": 1}},
    )
    assert sport_array_to_gains(buf).exec_mode == SportExecMode.STAIR

