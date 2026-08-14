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

import numpy as np

from dimos.mapping.pointclouds.occupancy import height_cost_occupancy
from dimos.msgs.sensor_msgs.PointCloud2 import PointCloud2


def test_mid360_height_cost_ignores_ceiling_but_keeps_low_obstacle_edges() -> None:
    """The unchanged Go2 height-cost projection must handle Mid360 vertical returns."""
    points: list[list[float]] = []
    for x in np.arange(-0.5, 0.51, 0.1):
        for y in np.arange(-0.5, 0.51, 0.1):
            points.append([float(x), float(y), 0.0])
            if abs(x) <= 0.1 and abs(y) <= 0.1:
                points.append([float(x), float(y), 0.30])

    baseline_cloud = PointCloud2.from_numpy(
        np.asarray(points, dtype=np.float32),
        frame_id="world",
    )
    baseline = height_cost_occupancy(baseline_cloud)

    # The ceiling return shares a floor cell. Height-cost should use the floor
    # because the vertical gap is larger than the robot's pass-under threshold.
    points.append([-0.4, -0.4, 2.4])
    cloud = PointCloud2.from_numpy(np.asarray(points, dtype=np.float32), frame_id="world")
    costmap = height_cost_occupancy(cloud)

    np.testing.assert_array_equal(costmap.grid, baseline.grid)
    assert np.max(costmap.grid) >= 80
    assert np.count_nonzero(costmap.grid >= 80) > 0
