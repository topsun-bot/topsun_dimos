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


def test_overhead_cutoff_keeps_ceiling_only_neighbor_cells_passable() -> None:
    """邻格只有天花板回波时不得形成假坡墙, 把可行域切碎.

    can_pass_under 只覆盖"同一格里同时有地面和天花板"的情况. Mid360 常见的
    是天花板点落在没有任何地面回波的格子里: 该格 min==max, 高度被当成 2.4m,
    平滑再在其周围插出陡坡, 投影后变成致命障碍.
    """
    floor: list[list[float]] = []
    for x in np.arange(-1.0, 1.01, 0.05):
        for y in np.arange(-1.0, 1.01, 0.05):
            floor.append([float(x), float(y), 0.0])
    # 天花板环放在地面覆盖区之外, 制造"仅天花板"格子(Mid360 近场地面盲区,
    # 远处天花板/高物先于地面进图, 就是这种几何)
    theta = np.linspace(0, 2 * np.pi, 240, endpoint=False)
    ceiling = [
        [r * float(np.cos(t)), r * float(np.sin(t)), 2.4]
        for t in theta
        for r in (1.3, 1.4, 1.5)
    ]
    cloud = PointCloud2.from_numpy(
        np.asarray(floor + ceiling, dtype=np.float32), frame_id="world"
    )

    buggy = height_cost_occupancy(cloud)
    assert np.count_nonzero(buggy.grid >= 100) > 0  # 旧行为: 假坡墙存在

    fixed = height_cost_occupancy(cloud, overhead_cutoff=0.6)
    assert np.count_nonzero(fixed.grid >= 100) == 0
    assert np.count_nonzero(fixed.grid == 0) > 0
