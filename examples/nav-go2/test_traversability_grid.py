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

import numpy as np

from traversability_grid import rasterize_trajectories_to_costmap


def test_rasterize_fan_of_trajectories() -> None:
    # Eight straight-ahead trajectories -> center column should be free (0)
    angles = np.linspace(-0.3, 0.3, 8)
    steps = 5
    trajs = []
    for angle in angles:
        xs = np.linspace(0.2, 1.0, steps)
        ys = np.tan(angle) * xs
        trajs.append(np.stack([xs, ys], axis=1))
    trajectories = np.stack(trajs, axis=0)

    grid = rasterize_trajectories_to_costmap(
        trajectories,
        forward_m=2.0,
        lateral_m=2.0,
        resolution_m=0.1,
    )

    assert grid.frame_id == "base_link"
    assert grid.grid.shape == (20, 20)
    assert int(grid.grid.min()) == 0
    assert int(grid.grid[5, 10]) == 0
