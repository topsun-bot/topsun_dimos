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

"""Headless MuJoCo: climb >= 5 uniform 15 cm treads (Go2 via Go1 ONNX + WalkStair)."""

from __future__ import annotations

from typing import Any

import mujoco
import numpy as np
import pytest

from dimos.core.global_config import GlobalConfig
from dimos.robot.unitree.go2.stair_locomotion.sport_actions import sport_payload, walk_stair_call
from dimos.simulation.mujoco.build_scene_stairs import (
    NUM_STEPS,
    RISER_M,
    STAIR_START_X,
    TREAD_M,
    _approach_top_z,
    build_scene_stairs_xml,
)
from dimos.simulation.mujoco.model import load_model, load_scene_xml
from dimos.simulation.mujoco.sport_state import (
    apply_sport_api_payload,
    default_sport_buffer,
    sport_array_to_gains,
)

MIN_MAIN_STEPS_CLIMBED = 5
MAX_CONTROL_STEPS = 22000
SETTLE_STEPS = 400
APPROACH_FORWARD_CMD = 0.20
STAIR_FORWARD_CMD = 0.24


class _PhasedStairSimController:
    """Approach on flat (no Sport), then WalkStair + forward cmd on the stair run."""

    def __init__(self) -> None:
        self._sport_buf = default_sport_buffer()
        self._vx = APPROACH_FORWARD_CMD
        self._stair_armed = False

    def _maybe_arm_stair(self, x: float) -> None:
        if self._stair_armed or x < STAIR_START_X - 0.30:
            return
        apply_sport_api_payload(self._sport_buf, sport_payload(walk_stair_call(True)))
        self._vx = STAIR_FORWARD_CMD
        self._stair_armed = True

    def get_command(self) -> np.ndarray[Any, Any]:
        return np.array([self._vx, 0.0, 0.0], dtype=np.float32)

    def get_sport_gains(self) -> Any:
        return sport_array_to_gains(self._sport_buf)

    def stop(self) -> None:
        pass


def _uniform_steps_climbed(x: float, z: float) -> int:
    """Macro treads cleared by base height (15 cm landing-to-landing)."""
    if x < STAIR_START_X - 0.15:
        return 0
    z_main = _approach_top_z()
    return max(0, min(int((z - z_main - 0.08) / RISER_M), NUM_STEPS))


@pytest.mark.mujoco
def test_climbs_at_least_five_uniform_15cm_steps(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DIMOS_STAIR_TREAD_M", "0.28")
    build_scene_stairs_xml(force=True)
    config = GlobalConfig(
        simulation=True,
        mujoco_room="stairs",
        mujoco_start_pos="1.85,0.0",
    )
    controller = _PhasedStairSimController()
    scene_xml = load_scene_xml(config)
    model, data = load_model(controller, robot="unitree_go1", scene_xml=scene_xml)

    pos = config.mujoco_start_pos_float
    data.qpos[0:3] = [pos[0], pos[1], 0.32]
    data.qpos[3:7] = [1.0, 0.0, 0.0, 0.0]
    data.qvel[:] = 0.0

    for _ in range(SETTLE_STEPS):
        mujoco.mj_step(model, data)

    peak_z = float(data.qpos[2])
    peak_steps = 0
    for _ in range(MAX_CONTROL_STEPS):
        controller._maybe_arm_stair(float(data.qpos[0]))
        mujoco.mj_step(model, data)
        x = float(data.qpos[0])
        z = float(data.qpos[2])
        peak_z = max(peak_z, z)
        peak_steps = max(peak_steps, _uniform_steps_climbed(x, z))

    assert peak_steps >= MIN_MAIN_STEPS_CLIMBED, (
        f"Expected >= {MIN_MAIN_STEPS_CLIMBED} uniform {RISER_M:.2f} m treads; "
        f"got {peak_steps} (peak_z={peak_z:.3f}, stair_start_x={STAIR_START_X})"
    )
