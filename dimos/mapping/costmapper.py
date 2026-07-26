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

from dataclasses import asdict
import math
import time
from typing import Any

import numpy as np
from pydantic import Field
from reactivex import combine_latest, operators as ops

from dimos.core.core import rpc
from dimos.core.module import Module, ModuleConfig
from dimos.core.stream import In, Out
from dimos.mapping.pointclouds.occupancy import (
    OCCUPANCY_ALGOS,
    HeightCostConfig,
    OccupancyConfig,
)
from dimos.msgs.nav_msgs.OccupancyGrid import OccupancyGrid
from dimos.msgs.sensor_msgs.PointCloud2 import PointCloud2
from dimos.navigation.diagnostics.sink import TraceSink, isolate_trace_failure
from dimos.utils.logging_config import setup_logger

logger = setup_logger()


class Config(ModuleConfig):
    algo: str = "height_cost"
    config: OccupancyConfig = Field(default_factory=HeightCostConfig)
    # for robots that cant see directly below themself
    initial_safe_radius_meters: float = 0.0


class CostMapper(Module):
    config: Config
    global_map: In[PointCloud2]
    merged_map: In[PointCloud2]
    global_costmap: Out[OccupancyGrid]

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._navigation_trace = TraceSink("costmapper", config=self.config.g)
        self._trace_costmap_seq = 0
        self._trace_first_blob_saved = False

    @rpc
    def start(self) -> None:
        super().start()

        def _select_map(
            pair: tuple[PointCloud2, PointCloud2 | None],
        ) -> tuple[PointCloud2, str]:
            gmap, merged = pair
            return (merged, "merged_map") if merged is not None else (gmap, "global_map")

        def _publish_costmap(
            grid: OccupancyGrid,
            calc_time_ms: float,
            rx_monotonic: float,
            source: str,
            source_ts: float,
            source_point_count: int,
        ) -> None:
            self.global_costmap.publish(grid)
            self._trace_costmap_published(
                grid,
                calc_time_ms,
                rx_monotonic,
                source,
                source_ts,
                source_point_count,
            )

        def _calculate_and_time(
            selected: tuple[PointCloud2, str],
        ) -> tuple[OccupancyGrid, float, float, str, float, int]:
            msg, source = selected
            rx_monotonic = time.monotonic()  # Capture receipt time
            start = time.perf_counter()
            grid = self._calculate_costmap(msg)
            elapsed_ms = (time.perf_counter() - start) * 1000
            return grid, elapsed_ms, rx_monotonic, source, float(msg.ts), len(msg)

        self.register_disposable(
            combine_latest(
                self.global_map.observable(),  # type: ignore[no-untyped-call]
                self.merged_map.observable().pipe(ops.start_with(None)),  # type: ignore[no-untyped-call,arg-type]
            )
            .pipe(ops.map(_select_map))
            .pipe(ops.map(_calculate_and_time))
            .subscribe(
                lambda result: _publish_costmap(
                    result[0],
                    result[1],
                    result[2],
                    result[3],
                    result[4],
                    result[5],
                )
            )
        )

    @rpc
    def stop(self) -> None:
        super().stop()
        self._navigation_trace.close()

    # @timed()  # TODO: fix thread leak in timed decorator
    def _calculate_costmap(self, msg: PointCloud2) -> OccupancyGrid:
        occupancy_function = OCCUPANCY_ALGOS[self.config.algo]
        grid = occupancy_function(msg, **asdict(self.config.config))
        self._apply_initial_safe_radius(grid)
        return grid

    def _apply_initial_safe_radius(self, grid: OccupancyGrid) -> None:
        radius_meters = self.config.initial_safe_radius_meters
        if radius_meters <= 0 or grid.grid.size == 0:
            return

        resolution = grid.resolution
        origin_x = grid.origin.position.x
        origin_y = grid.origin.position.y

        rows, columns = np.ogrid[: grid.grid.shape[0], : grid.grid.shape[1]]
        cell_world_x = columns * resolution + origin_x
        cell_world_y = rows * resolution + origin_y
        distance_squared_meters = cell_world_x**2 + cell_world_y**2

        # Half-cell tolerance: a cell counts as inside if any part of it overlaps
        # the disc. Avoids floating-point boundary flakiness from radius/resolution.
        effective_radius_meters = radius_meters + resolution * 0.5
        safe_mask = distance_squared_meters <= effective_radius_meters**2
        grid.grid[safe_mask] = 0

    def _trace_costmap_published(
        self,
        grid: OccupancyGrid,
        calc_time_ms: float,
        rx_monotonic: float,
        source: str,
        source_ts: float,
        source_point_count: int,
    ) -> None:
        if not self._navigation_trace.accepts("summary"):
            return
        try:
            self._trace_costmap_seq += 1
            costmap_id = f"costmap-{self._trace_costmap_seq:06d}"
            metadata = _costmap_trace_metadata(
                self.config,
                grid,
                costmap_id=costmap_id,
                costmap_sequence=self._trace_costmap_seq,
                source=source,
                source_ts=source_ts,
                source_point_count=source_point_count,
                source_rx_monotonic_sec=rx_monotonic,
                calculation_time_ms=calc_time_ms,
            )
            self._navigation_trace.record(
                "costmap_published",
                metadata,
                estimated_bytes=1280,
            )
            if self._navigation_trace.accepts("full") and not self._trace_first_blob_saved:
                accepted = self._navigation_trace.record_blob(
                    "costmap",
                    grid.grid,
                    {
                        **metadata,
                        "snapshot_reason": "first_costmap_in_worker",
                        "planner_association": "UNKNOWN",
                    },
                    stem=costmap_id,
                )
                if accepted:
                    self._trace_first_blob_saved = True
        except Exception as exc:
            isolate_trace_failure(self._navigation_trace, exc)


def _costmap_trace_metadata(
    config: Config,
    grid: OccupancyGrid,
    *,
    costmap_id: str,
    costmap_sequence: int,
    source: str,
    source_ts: float,
    source_point_count: int,
    source_rx_monotonic_sec: float,
    calculation_time_ms: float,
) -> dict[str, object]:
    return {
        "costmap_id": costmap_id,
        "costmap_sequence": costmap_sequence,
        "source": source,
        "source_ts": source_ts,
        "source_point_count": source_point_count,
        "source_rx_monotonic_sec": source_rx_monotonic_sec,
        "output_ts": float(grid.ts),
        "calculation_time_ms": calculation_time_ms,
        "occupancy_algorithm": config.algo,
        "occupancy_config": asdict(config.config),
        "initial_safe_radius_meters": config.initial_safe_radius_meters,
        "publish_completed": True,
        "frame_id": grid.frame_id,
        "shape": [grid.height, grid.width],
        "dtype": str(grid.grid.dtype),
        "resolution": float(grid.resolution),
        "origin": {
            "x": float(grid.origin.position.x),
            "y": float(grid.origin.position.y),
            "z": float(grid.origin.position.z),
            "yaw": _quaternion_yaw(grid.origin.orientation),
        },
        "origin_rotation_applied_by_grid_helpers": False,
        "fingerprint": {
            "source_ts": source_ts,
            "shape": [grid.height, grid.width],
            "resolution": float(grid.resolution),
            "origin_x": float(grid.origin.position.x),
            "origin_y": float(grid.origin.position.y),
        },
    }


def _quaternion_yaw(orientation: Any) -> float:
    x = float(orientation.x)
    y = float(orientation.y)
    z = float(orientation.z)
    w = float(orientation.w)
    return math.atan2(
        2.0 * (w * z + x * y),
        1.0 - 2.0 * (y * y + z * z),
    )
