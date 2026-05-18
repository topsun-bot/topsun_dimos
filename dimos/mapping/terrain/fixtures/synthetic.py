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

from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray


@dataclass(frozen=True)
class SyntheticHeightMap:
    height_map: NDArray[np.float64]
    origin_x: float
    origin_y: float
    resolution: float
    expected_axis_yaw: float
    expected_mean_riser: float


def straight_stair_height_map(
    *,
    num_steps: int,
    riser_m: float,
    tread_m: float = 0.28,
    width_m: float = 1.2,
    resolution: float = 0.05,
    axis_yaw: float = 0.0,
) -> SyntheticHeightMap:
    """Build a straight stair along +x in map frame (axis_yaw=0)."""
    length_m = num_steps * tread_m
    width_cells = int(np.ceil(width_m / resolution))
    length_cells = int(np.ceil(length_m / resolution))
    height = width_cells
    width = length_cells + 2

    grid = np.zeros((height, width), dtype=np.float64)
    y_center = height // 2
    half_w = max(1, int((width_m / resolution) / 2))

    for step in range(num_steps):
        x0 = int(step * tread_m / resolution) + 1
        x1 = int((step + 1) * tread_m / resolution) + 1
        z = step * riser_m
        y0 = max(0, y_center - half_w)
        y1 = min(height, y_center + half_w + 1)
        grid[y0:y1, x0:x1] = z

    return SyntheticHeightMap(
        height_map=grid,
        origin_x=0.0,
        origin_y=-width_m / 2,
        resolution=resolution,
        expected_axis_yaw=axis_yaw,
        expected_mean_riser=riser_m,
    )


def straight_stair_5_steps_17cm(resolution: float = 0.05) -> SyntheticHeightMap:
    return straight_stair_height_map(num_steps=5, riser_m=0.17, resolution=resolution)


def straight_stair_8_steps_20cm(resolution: float = 0.05) -> SyntheticHeightMap:
    return straight_stair_height_map(num_steps=8, riser_m=0.20, resolution=resolution)


def straight_stair_5_steps_15cm(resolution: float = 0.05) -> SyntheticHeightMap:
    return straight_stair_height_map(num_steps=5, riser_m=0.15, resolution=resolution)


def negative_ramp_height_map(resolution: float = 0.05) -> SyntheticHeightMap:
    """Smooth ramp — should not be classified as periodic stairs."""
    width_cells = int(np.ceil(1.2 / resolution))
    length_cells = int(np.ceil(4.0 / resolution))
    grid = np.zeros((width_cells, length_cells), dtype=np.float64)
    for x in range(length_cells):
        grid[:, x] = (x / length_cells) * 0.8
    return SyntheticHeightMap(
        height_map=grid,
        origin_x=0.0,
        origin_y=-0.6,
        resolution=resolution,
        expected_axis_yaw=0.0,
        expected_mean_riser=0.0,
    )


def negative_flat_height_map(resolution: float = 0.05) -> SyntheticHeightMap:
    width_cells = int(np.ceil(1.2 / resolution))
    length_cells = int(np.ceil(3.0 / resolution))
    grid = np.zeros((width_cells, length_cells), dtype=np.float64)
    return SyntheticHeightMap(
        height_map=grid,
        origin_x=0.0,
        origin_y=-0.6,
        resolution=resolution,
        expected_axis_yaw=0.0,
        expected_mean_riser=0.0,
    )
