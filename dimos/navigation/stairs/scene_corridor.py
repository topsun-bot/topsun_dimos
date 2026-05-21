# Copyright 2026 Dimensional Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Preset stair corridor matching ``build_scene_stairs`` / MuJoCo ``scene_stairs``."""

from __future__ import annotations

from dimos.navigation.stairs.contracts import StairCorridor
from dimos.navigation.stairs.geometry import extend_centerline_approach
from dimos.simulation.mujoco.build_scene_stairs import (
    NUM_STEPS,
    RISER_M,
    STAIR_START_X,
    STAIR_START_Y,
    STAIR_WIDTH_M,
    TREAD_M,
    _step_center_x,
)


def mujoco_scene_stairs_corridor(
    *,
    safe_half_width: float = 0.25,
    approach_margin_m: float = 2.5,
) -> StairCorridor:
    """Corridor aligned with the default DimOS / unitree ``scene_stairs`` layout (+x)."""
    half_w = STAIR_WIDTH_M / 2.0
    x0 = STAIR_START_X - approach_margin_m
    x1 = _step_center_x(NUM_STEPS - 1) + TREAD_M
    centerline = extend_centerline_approach(
        [(STAIR_START_X, STAIR_START_Y)]
        + [(_step_center_x(i), STAIR_START_Y) for i in range(NUM_STEPS)],
        margin_m=approach_margin_m,
    )
    polygon = [
        (x0, STAIR_START_Y - half_w),
        (x1, STAIR_START_Y - half_w),
        (x1, STAIR_START_Y + half_w),
        (x0, STAIR_START_Y + half_w),
    ]
    return StairCorridor(
        polygon=polygon,
        centerline=centerline,
        safe_half_width=safe_half_width,
        ascending=True,
        axis_yaw=0.0,
        mean_riser=RISER_M,
        mean_tread=TREAD_M,
        riser_lines=[],
    )
