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
import open3d as o3d  # type: ignore[import-untyped]
import pytest

from dimos.mapping.relocalization.relocalize import _GLOBAL_CACHE, relocalize_with_initial


@pytest.fixture(autouse=True)
def clear_global_map_cache():
    # 该缓存按进程共享；测试前后清理，避免点数相同的合成地图互相污染。
    _GLOBAL_CACHE.clear()
    yield
    _GLOBAL_CACHE.clear()


def test_relocalize_with_initial_refines_without_global_ransac() -> None:
    rng = np.random.default_rng(7)
    point_count = 1_200
    yz = rng.uniform(-2.0, 2.0, (point_count, 2))
    xz = rng.uniform(-2.0, 2.0, (point_count, 2))
    xy = rng.uniform(-2.0, 2.0, (point_count, 2))
    target_points = np.vstack(
        (
            np.column_stack((np.zeros(point_count), yz)),
            np.column_stack((xz[:, 0], np.zeros(point_count), xz[:, 1])),
            np.column_stack((xy, np.zeros(point_count))),
        )
    )
    expected = np.eye(4)
    expected[:3, 3] = [0.4, -0.25, 0.0]
    source_points = target_points - expected[:3, 3]
    target = o3d.geometry.PointCloud(o3d.utility.Vector3dVector(target_points))
    source = o3d.geometry.PointCloud(o3d.utility.Vector3dVector(source_points))
    initial = expected.copy()
    initial[:3, 3] += [0.05, -0.04, 0.02]

    result, fitness = relocalize_with_initial(
        target,
        source,
        initial,
        max_iteration=50,
        crop_radius=2.0,
    )

    assert fitness == pytest.approx(1.0)
    assert result[:3, 3] == pytest.approx(expected[:3, 3], abs=0.02)
