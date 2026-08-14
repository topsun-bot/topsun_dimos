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

from dimos_lcm.std_msgs import Bool  # type: ignore[import-untyped]

from dimos.mapping.voxels.module import HealthGatedVoxelGridMapper
from dimos.msgs.sensor_msgs.PointCloud2 import PointCloud2


class _FakeGrid:
    def __init__(self) -> None:
        self.frames: list[PointCloud2] = []
        self.disposed = False

    def add_frame(self, frame: PointCloud2) -> None:
        self.frames.append(frame)

    def get_global_pointcloud2(self) -> PointCloud2:
        return PointCloud2(frame_id="world", ts=self.frames[-1].ts)

    def size(self) -> int:
        return len(self.frames)

    def dispose(self) -> None:
        self.disposed = True


def test_health_fault_clears_voxels_and_latches_until_restart() -> None:
    mapper = HealthGatedVoxelGridMapper(emit_every=2, device="CPU:0")
    fake_grid = _FakeGrid()
    mapper._grid = fake_grid  # type: ignore[assignment]
    published: list[PointCloud2] = []
    unsubscribe = mapper.global_map.subscribe(published.append)
    try:
        # Clouds arriving before the source declares itself ready are ignored.
        mapper._on_lidar(PointCloud2(ts=1.0))
        assert fake_grid.frames == []

        mapper._on_navigation_source_health(Bool(data=True))
        mapper._on_lidar(PointCloud2(ts=2.0))
        mapper._on_lidar(PointCloud2(ts=3.0))
        assert len(fake_grid.frames) == 2
        assert len(published) == 1

        mapper._on_navigation_source_health(Bool(data=False))
        assert fake_grid.disposed is True
        assert mapper._grid is None
        assert mapper._frame_count == 0
        assert len(published) == 2
        assert len(published[-1]) == 0
        assert published[-1].frame_id == "world"

        # A later true message cannot revive the old coordinate state.
        mapper._on_navigation_source_health(Bool(data=True))
        mapper._on_lidar(PointCloud2(ts=4.0))
        mapper._on_navigation_source_health(Bool(data=False))
        assert len(fake_grid.frames) == 2
        assert len(published) == 2
    finally:
        unsubscribe()
        mapper._close_module()
