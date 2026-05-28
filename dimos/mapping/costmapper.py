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
from pathlib import Path
from threading import RLock
import time
from typing import Any

from pydantic import Field
from reactivex import interval, operators as ops

from dimos.constants import STATE_DIR
from dimos.core.core import rpc
from dimos.core.module import Module, ModuleConfig
from dimos.core.stream import In, Out
from dimos.mapping.occupancy.relocalization import match_occupancy_se2
from dimos.mapping.pointclouds.occupancy import (
    OCCUPANCY_ALGOS,
    HeightCostConfig,
    OccupancyConfig,
)
from dimos.msgs.nav_msgs.OccupancyGrid import OccupancyGrid
from dimos.msgs.sensor_msgs.PointCloud2 import PointCloud2
from dimos.utils.logging_config import setup_logger

logger = setup_logger()


class Config(ModuleConfig):
    algo: str = "height_cost"
    config: OccupancyConfig = Field(default_factory=HeightCostConfig)
    persistent_map_dir: str | None = None
    load_persistent_map_on_start: bool = True
    publish_persistent_map_on_start: bool = False
    auto_save_interval_s: float | None = 30.0


class CostMapper(Module):
    config: Config
    global_map: In[PointCloud2]
    global_costmap: Out[OccupancyGrid]
    _latest_costmap: OccupancyGrid | None = None
    _persistent_costmap: OccupancyGrid | None = None
    _latest_lock: RLock

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._latest_lock = RLock()

    @rpc
    def start(self) -> None:
        super().start()

        def _publish_costmap(grid: OccupancyGrid, calc_time_ms: float, rx_monotonic: float) -> None:
            with self._latest_lock:
                self._latest_costmap = grid
            self.global_costmap.publish(grid)

        def _calculate_and_time(
            msg: PointCloud2,
        ) -> tuple[OccupancyGrid, float, float]:
            rx_monotonic = time.monotonic()  # Capture receipt time
            start = time.perf_counter()
            grid = self._calculate_costmap(msg)
            elapsed_ms = (time.perf_counter() - start) * 1000
            return grid, elapsed_ms, rx_monotonic

        self.register_disposable(
            self.global_map.observable()  # type: ignore[no-untyped-call]
            .pipe(ops.map(_calculate_and_time))
            .subscribe(lambda result: _publish_costmap(result[0], result[1], result[2]))
        )

        if self.config.load_persistent_map_on_start:
            self.load_map(publish=self.config.publish_persistent_map_on_start)

        if self.config.auto_save_interval_s is not None:
            self.register_disposable(
                interval(self.config.auto_save_interval_s).subscribe(lambda _: self._auto_save_map())
            )

    @rpc
    def stop(self) -> None:
        super().stop()

    # @timed()  # TODO: fix thread leak in timed decorator
    def _calculate_costmap(self, msg: PointCloud2) -> OccupancyGrid:
        fn = OCCUPANCY_ALGOS[self.config.algo]
        return fn(msg, **asdict(self.config.config))

    def _auto_save_map(self) -> None:
        with self._latest_lock:
            has_costmap = self._latest_costmap is not None

        if not has_costmap:
            return

        try:
            self.save_map()
        except Exception:
            logger.exception("Failed to auto-save persistent costmap.")

    @rpc
    def save_map(self, path: str | None = None) -> str:
        map_path = self._resolve_map_path(path)
        with self._latest_lock:
            latest = self._latest_costmap

        if latest is None:
            raise ValueError("No current global costmap available to save")

        if map_path.suffix == ".npz":
            latest.save_npz(map_path)
        else:
            latest.save_to_directory(map_path)

        logger.info("Saved persistent costmap.", path=str(map_path))
        return str(map_path)

    @rpc
    def load_map(self, path: str | None = None, publish: bool = True) -> bool:
        map_path = self._resolve_map_path(path)
        if not map_path.exists():
            logger.info("No persistent costmap found.", path=str(map_path))
            return False

        grid = OccupancyGrid.from_path(map_path)
        with self._latest_lock:
            self._persistent_costmap = grid

        if publish:
            self.global_costmap.publish(grid)

        logger.info("Loaded persistent costmap.", path=str(map_path))
        return True

    @rpc
    def publish_persistent_map(self) -> bool:
        with self._latest_lock:
            persistent = self._persistent_costmap

        if persistent is None:
            return False

        self.global_costmap.publish(persistent)
        return True

    @rpc
    def relocalize_current_map(
        self,
        search_radius_m: float = 2.0,
        stride_m: float | None = None,
        yaw_candidates_deg: list[float] | None = None,
        min_compared_cells: int = 20,
        min_occupied_cells: int = 1,
        min_confidence: float = 0.55,
    ) -> dict[str, float | int | bool]:
        with self._latest_lock:
            persistent = self._persistent_costmap
            current = self._latest_costmap

        if persistent is None:
            raise ValueError("No persistent costmap loaded")
        if current is None:
            raise ValueError("No current global costmap available")

        result = match_occupancy_se2(
            persistent,
            current,
            search_radius_m=search_radius_m,
            stride_m=stride_m,
            yaw_candidates_deg=tuple(yaw_candidates_deg or [0.0, -90.0, 90.0, 180.0]),
            min_compared_cells=min_compared_cells,
            min_occupied_cells=min_occupied_cells,
            min_confidence=min_confidence,
        )
        transform_x = result.x - current.origin.position.x
        transform_y = result.y - current.origin.position.y
        return {
            "success": result.success,
            "x": result.x,
            "y": result.y,
            "yaw": result.yaw,
            "map_from_current_x": transform_x,
            "map_from_current_y": transform_y,
            "map_from_current_yaw": result.yaw,
            "score": result.score,
            "confidence": result.confidence,
            "compared_cells": result.compared_cells,
            "occupied_compared_cells": result.occupied_compared_cells,
        }

    def _resolve_map_path(self, path: str | None) -> Path:
        if path is not None:
            return Path(path).expanduser()
        if self.config.persistent_map_dir is not None:
            return Path(self.config.persistent_map_dir).expanduser()
        return STATE_DIR / "maps" / "default" / "costmap"
