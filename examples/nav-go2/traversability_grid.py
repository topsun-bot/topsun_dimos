# Copyright 2025-2026 Dimensional Inc.
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

"""Build a robot-centric traversability OccupancyGrid from trajectory samples."""

from __future__ import annotations

import time

import numpy as np

from dimos.msgs.geometry_msgs.Pose import Pose
from dimos.msgs.nav_msgs.OccupancyGrid import CostValues, OccupancyGrid


def rasterize_trajectories_to_costmap(
    trajectories: np.ndarray,
    *,
    forward_m: float = 4.0,
    lateral_m: float = 3.0,
    resolution_m: float = 0.05,
    frame_id: str = "base_link",
    ts: float | None = None,
) -> OccupancyGrid:
    """Convert sampled body-frame trajectories into a traversability costmap.

    Args:
        trajectories: Array shaped (num_samples, num_steps, 2). Each step is
            (x_forward, y_lateral) in meters relative to the robot.
        forward_m: Grid extent along +x (forward).
        lateral_m: Grid width along y (robot left is +y).
        resolution_m: Cell size in meters.
        frame_id: Output frame (typically ``base_link``).
        ts: Message timestamp; defaults to ``time.time()``.

    Returns:
        OccupancyGrid where lower values mean more traversable:
        - 0: many diffusion samples pass through the cell
        - 1-99: partial consensus
        - 100: never visited by any sample (in-bounds only)
        - -1: outside the modeled forward/lateral window
    """
    if trajectories.ndim != 3 or trajectories.shape[-1] != 2:
        raise ValueError(f"Expected (N, T, 2) trajectories, got {trajectories.shape}")

    num_samples = max(trajectories.shape[0], 1)
    width = max(round(lateral_m / resolution_m), 1)
    height = max(round(forward_m / resolution_m), 1)

    votes = np.zeros((height, width), dtype=np.int32)
    half_lateral = lateral_m / 2.0

    for traj in trajectories:
        visited_cells: set[tuple[int, int]] = set()
        for x_fwd, y_lat in traj:
            if x_fwd < 0 or x_fwd > forward_m:
                continue
            if y_lat < -half_lateral or y_lat > half_lateral:
                continue
            row = int(x_fwd / resolution_m)
            col = int((y_lat + half_lateral) / resolution_m)
            if 0 <= row < height and 0 <= col < width:
                visited_cells.add((row, col))
        for row, col in visited_cells:
            votes[row, col] += 1

    grid = np.full((height, width), CostValues.UNKNOWN, dtype=np.int8)

    for row in range(height):
        for col in range(width):
            if votes[row, col] == 0:
                grid[row, col] = CostValues.OCCUPIED
                continue
            ratio = min(1.0, votes[row, col] / num_samples)
            # ROS convention: 0 = free, 100 = lethal
            grid[row, col] = np.int8(round(99 * (1.0 - ratio)))

    origin = Pose(
        position=[-half_lateral, 0.0, 0.0],
        orientation=[0.0, 0.0, 0.0, 1.0],
    )

    return OccupancyGrid(
        grid=grid,
        resolution=resolution_m,
        origin=origin,
        frame_id=frame_id,
        ts=ts if ts is not None else time.time(),
    )
