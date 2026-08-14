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

from dimos.mapping.costmapper import CostMapper
from dimos.msgs.geometry_msgs.PoseStamped import PoseStamped
from dimos.msgs.sensor_msgs.PointCloud2 import PointCloud2

# Point-LIO world 原点在 IMU 启动位姿, 站立启动时地面约在 z=-0.42
_FLOOR_Z = -0.42
_STANDING_HEIGHT = 0.30


def _mid360_like_cloud() -> PointCloud2:
    """近处地面 + 远处仅天花板回波的合成 Mid360 场景."""
    xs = np.arange(-2.0, 2.01, 0.05)
    xx, yy = np.meshgrid(xs, xs)
    floor = np.stack([xx.ravel(), yy.ravel(), np.full(xx.size, _FLOOR_Z)], axis=1)
    # 天花板环: 这些 XY 格子里没有任何地面点
    theta = np.linspace(0, 2 * np.pi, 200, endpoint=False)
    radii = np.linspace(1.5, 1.9, 5)
    rr, tt = np.meshgrid(radii, theta)
    ceiling = np.stack(
        [
            rr.ravel() * np.cos(tt.ravel()) + 0.02,
            rr.ravel() * np.sin(tt.ravel()) + 0.02,
            np.full(rr.size, _FLOOR_Z + 2.4),
        ],
        axis=1,
    )
    return PointCloud2.from_numpy(
        np.vstack([floor, ceiling]).astype(np.float32), frame_id="world"
    )


def _close(mapper: CostMapper) -> None:
    mapper._navigation_trace.close()
    mapper._close_module()


def test_band_clip_removes_ceiling_and_keeps_costmap_navigable() -> None:
    mapper = CostMapper(
        projection_band_below_m=0.10,
        projection_band_above_m=0.50,
        ground_reference="static",
        ground_static_z=_FLOOR_Z,
    )
    try:
        cloud = _mid360_like_cloud()
        clipped = mapper._clip_to_travel_band(cloud)
        points = clipped.points_f32()
        assert len(points) < len(cloud.points_f32())
        assert float(points[:, 2].max()) <= _FLOOR_Z + 0.50 + 1e-6

        grid = mapper._calculate_costmap(cloud)
        assert np.count_nonzero(grid.grid >= 100) == 0
        assert np.count_nonzero(grid.grid == 0) > 0
    finally:
        _close(mapper)


def test_band_clip_uses_odom_ground_reference() -> None:
    mapper = CostMapper(
        projection_band_below_m=0.10,
        projection_band_above_m=0.50,
        ground_reference="odom",
        robot_standing_height_m=_STANDING_HEIGHT,
    )
    try:
        # base_link z = 地面 + 站高
        mapper._on_odom(PoseStamped(position=[0.0, 0.0, _FLOOR_Z + _STANDING_HEIGHT]))
        grid = mapper._calculate_costmap(_mid360_like_cloud())
        assert np.count_nonzero(grid.grid >= 100) == 0
        assert np.count_nonzero(grid.grid == 0) > 0
    finally:
        _close(mapper)


def test_band_clip_fails_open_without_odom() -> None:
    mapper = CostMapper(
        projection_band_below_m=0.10,
        projection_band_above_m=0.50,
        ground_reference="odom",
    )
    try:
        cloud = _mid360_like_cloud()
        clipped = mapper._clip_to_travel_band(cloud)
        # 没有 odom 时不裁切(fail-open), 不得发布空投影
        assert len(clipped.points_f32()) == len(cloud.points_f32())
    finally:
        _close(mapper)


def test_band_disabled_by_default_keeps_old_behavior() -> None:
    mapper = CostMapper()
    try:
        cloud = _mid360_like_cloud()
        clipped = mapper._clip_to_travel_band(cloud)
        assert clipped is cloud
    finally:
        _close(mapper)
