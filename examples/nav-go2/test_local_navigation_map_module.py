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
import pytest

sys.path.insert(0, str(Path(__file__).parent))

from local_navigation_map_module import LocalNavigationMapConfig, LocalNavigationMapModule

from dimos.msgs.nav_msgs.OccupancyGrid import OccupancyGrid


def test_costmap_freshness_uses_selection_time_not_wall_clock(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = LocalNavigationMapModule.__new__(LocalNavigationMapModule)
    module.config = LocalNavigationMapConfig(local_map_max_age_s=1.0)
    module._last_costmap_time_s = 100.0
    module._last_occupancy_grid = object()

    monkeypatch.setattr("local_navigation_map_module.time.time", lambda: 10_000.0)

    module._ensure_recent_map_locked(100.5)

    with pytest.raises(RuntimeError, match="too far from selection time"):
        module._ensure_recent_map_locked(102.0)


def test_trajectory_transform_lookup_uses_selection_time() -> None:
    class IdentityTransform:
        def to_matrix(self) -> np.ndarray:
            return np.eye(4)

    class RecordingTF:
        def __init__(self) -> None:
            self.calls: list[dict[str, object]] = []

        def get(self, parent_frame: str, child_frame: str, **kwargs: object) -> IdentityTransform:
            self.calls.append(
                {
                    "parent_frame": parent_frame,
                    "child_frame": child_frame,
                    **kwargs,
                }
            )
            return IdentityTransform()

    module = LocalNavigationMapModule.__new__(LocalNavigationMapModule)
    module.config = LocalNavigationMapConfig(trajectory_frame_id="base_link")
    module._tf = RecordingTF()
    trajectories = np.array([[[1.0, 0.0]]], dtype=np.float64)

    transformed = module._trajectories_in_map_frame(
        trajectories,
        map_frame_id="odom",
        time_point=123.4,
    )

    assert np.array_equal(transformed, trajectories)
    assert module.tf.calls == [
        {
            "parent_frame": "odom",
            "child_frame": "base_link",
            "time_point": 123.4,
            "time_tolerance": 1.0,
        }
    ]


def test_navigation_map_snapshot_copies_costmap_grid() -> None:
    module = LocalNavigationMapModule.__new__(LocalNavigationMapModule)
    module.config = LocalNavigationMapConfig()
    grid = OccupancyGrid(grid=np.array([[0, 100]], dtype=np.int8), ts=42.0)
    module._last_occupancy_grid = grid

    snapshot = module._navigation_map_snapshot_locked()
    grid.grid[0, 0] = 100

    assert snapshot.binary_costmap.grid.tolist() == [[0, 100]]
