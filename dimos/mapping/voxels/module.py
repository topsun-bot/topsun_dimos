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

from __future__ import annotations

import threading
import time
from typing import TYPE_CHECKING, Any

from dimos_lcm.std_msgs import Bool  # type: ignore[import-untyped]
from reactivex.disposable import Disposable

from dimos.core.core import rpc
from dimos.core.module import Module, ModuleConfig
from dimos.core.stream import In, Out
from dimos.mapping.voxels.grid import VoxelGrid
from dimos.memory2.module import StreamModule
from dimos.memory2.stream import Stream
from dimos.memory2.transform import Transformer
from dimos.msgs.sensor_msgs.PointCloud2 import PointCloud2
from dimos.utils.logging_config import setup_logger

logger = setup_logger()

if TYPE_CHECKING:
    from collections.abc import Iterator

    from dimos.memory2.type.observation import Observation


class VoxelMapTransformer(Transformer[PointCloud2, PointCloud2]):
    """Accumulate PointCloud2 observations into a global voxel map.

    Assumes input clouds are already in world frame.
    All keyword arguments except ``emit_every`` are forwarded to
    :class:`VoxelGrid`.

    Args:
        emit_every: Yield the current accumulated map every *n* frames.
            ``1`` (default) = yield after every frame (live-compatible).
            ``0`` = yield only when upstream exhausts (batch mode).
        **grid_kwargs: Forwarded to ``VoxelGrid()``.
    """

    def __init__(self, *, emit_every: int = 1, **grid_kwargs: Any) -> None:
        self.emit_every = emit_every
        self._grid_kwargs = grid_kwargs

    def _make_obs(
        self, grid: VoxelGrid, last_obs: Observation[PointCloud2], count: int
    ) -> Observation[PointCloud2]:
        # pose=None: the global map is in world frame, per-observation pose is meaningless
        return last_obs.derive(
            data=grid.get_global_pointcloud2(),
            pose=None,
            tags={**last_obs.tags, "frame_count": count},
        )

    def __call__(
        self, upstream: Iterator[Observation[PointCloud2]]
    ) -> Iterator[Observation[PointCloud2]]:
        grid = VoxelGrid(**self._grid_kwargs)
        try:
            last_obs: Observation[PointCloud2] | None = None
            count = 0

            for obs in upstream:
                grid.add_frame(obs.data)
                last_obs = obs
                count += 1

                if self.emit_every > 0 and count % self.emit_every == 0:
                    yield self._make_obs(grid, last_obs, count)

            # Yield on exhaustion: always in batch mode, or if there are un-emitted frames
            if last_obs is not None and (self.emit_every == 0 or count % self.emit_every != 0):
                yield self._make_obs(grid, last_obs, count)
        finally:
            grid.dispose()


class VoxelGridMapperConfig(ModuleConfig):
    """Configuration for VoxelGridMapper."""

    voxel_size: float = 0.05
    block_count: int = 2_000_000
    device: str = "CUDA:0"
    carve_columns: bool = True
    frame_id: str = "world"
    emit_every: int = 1


class VoxelGridMapper(StreamModule[PointCloud2, PointCloud2]):
    """Accumulate lidar point clouds into a global voxel map."""

    config: VoxelGridMapperConfig

    def pipeline(self, stream: Stream[PointCloud2]) -> Stream[PointCloud2]:
        cfg = self.config.model_dump(
            include=set(VoxelGridMapperConfig.model_fields) - set(ModuleConfig.model_fields)
        )
        return stream.transform(VoxelMapTransformer(**cfg))

    lidar: In[PointCloud2]
    global_map: Out[PointCloud2]

    @rpc
    def start(self) -> None:
        super().start()

    @rpc
    def stop(self) -> None:
        super().stop()


class HealthGatedVoxelGridMapper(VoxelGridMapper):
    """Clear run-local voxels permanently when an external source faults.

    This variant is used only when the point cloud and pose share one external
    coordinate source. A false health event makes every accumulated voxel
    untrustworthy, so the map cannot be reused until the stack restarts.
    """

    navigation_source_healthy: In[Bool]

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._grid_lock = threading.Lock()
        self._grid: VoxelGrid | None = None
        self._frame_count = 0
        self._source_healthy = False
        self._source_fault_latched = False

    def _make_grid(self) -> VoxelGrid:
        fields = set(VoxelGridMapperConfig.model_fields) - set(ModuleConfig.model_fields)
        fields.discard("emit_every")
        return VoxelGrid(**self.config.model_dump(include=fields))

    @rpc
    def start(self) -> None:
        # StreamModule requires exactly one input. This safety variant has a
        # second health input and therefore owns the grid subscriptions itself.
        Module.start(self)
        if self.navigation_source_healthy.transport is None:
            raise RuntimeError("HealthGatedVoxelGridMapper requires navigation_source_healthy")
        with self._grid_lock:
            self._grid = self._make_grid()
        self.register_disposable(
            Disposable(self.navigation_source_healthy.subscribe(self._on_navigation_source_health))
        )
        self.register_disposable(Disposable(self.lidar.subscribe(self._on_lidar)))

    @rpc
    def stop(self) -> None:
        with self._grid_lock:
            grid, self._grid = self._grid, None
        if grid is not None:
            grid.dispose()
        Module.stop(self)

    def _on_navigation_source_health(self, msg: Bool) -> None:
        if msg.data:
            with self._grid_lock:
                if not self._source_fault_latched:
                    self._source_healthy = True
            return

        with self._grid_lock:
            if self._source_fault_latched:
                return
            self._source_healthy = False
            self._source_fault_latched = True
            cleared_frame_count = self._frame_count
            self._frame_count = 0
            grid, self._grid = self._grid, None
            cleared_voxel_count = grid.size() if grid is not None else 0
        if grid is not None:
            grid.dispose()

        # One terminal empty update invalidates cached map samples. No later
        # cloud is accepted in this process, even if health turns true again.
        self.global_map.publish(PointCloud2(frame_id=self.config.frame_id, ts=time.time()))
        logger.error(
            "Navigation source fault latched; cleared run-local voxel map "
            "(frames=%d, voxels=%d). Restart is required.",
            cleared_frame_count,
            cleared_voxel_count,
        )

    def _on_lidar(self, msg: PointCloud2) -> None:
        output: PointCloud2 | None = None
        with self._grid_lock:
            if not self._source_healthy or self._source_fault_latched or self._grid is None:
                return
            self._grid.add_frame(msg)
            self._frame_count += 1
            emit_every = self.config.emit_every
            if emit_every > 0 and self._frame_count % emit_every == 0:
                output = self._grid.get_global_pointcloud2()
        if output is not None:
            self.global_map.publish(output)
