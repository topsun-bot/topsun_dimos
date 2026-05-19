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

from dimos.simulation.mujoco.sport_state import (
    SPORT_IDX_ACTIVE,
    SPORT_IDX_COMMAND_GAIN,
    SPORT_IDX_FOOT_RAISE_M,
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
