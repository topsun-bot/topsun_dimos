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

from pathlib import Path
import sys

import numpy as np

from dimos.msgs.geometry_msgs.Pose import Pose
from dimos.msgs.nav_msgs.OccupancyGrid import OccupancyGrid

sys.path.insert(0, str(Path(__file__).parent))

from path_selector import TrajectoryPathSelector


def test_trajectory_cost_uses_costmap_origin() -> None:
    grid = np.zeros((4, 4), dtype=np.int8)
    grid[1, 2] = 100
    costmap = OccupancyGrid(
        grid=grid,
        resolution=0.5,
        origin=Pose(position=[-1.0, -0.5, 0.0]),
        frame_id="base_link",
    )
    selector = TrajectoryPathSelector(
        grid_lateral_m=3.0,
        grid_resolution_m=0.5,
    )

    obstacle_trajectory = np.array([[0.0, 0.0]], dtype=np.float64)
    free_trajectory = np.array([[-0.5, 0.0]], dtype=np.float64)
    outside_trajectory = np.array([[-1.01, 0.0]], dtype=np.float64)

    assert selector._trajectory_traversability_cost(obstacle_trajectory, costmap) == 100.0
    assert selector._trajectory_traversability_cost(free_trajectory, costmap) == 0.0
    assert selector._trajectory_traversability_cost(outside_trajectory, costmap) == 100.0
