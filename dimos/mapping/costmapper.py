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
import threading
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
from dimos.msgs.geometry_msgs.Pose import Pose
from dimos.msgs.nav_msgs.OccupancyGrid import OccupancyGrid
from dimos.msgs.sensor_msgs.PointCloud2 import PointCloud2
from dimos.utils.logging_config import setup_logger

logger = setup_logger()


class Config(ModuleConfig):
    algo: str = "height_cost"
    config: OccupancyConfig = Field(default_factory=HeightCostConfig)
    persistent_map_dir: str | None = None
    load_persistent_map_on_start: bool = True
    publish_persistent_map_on_start: bool = True
    auto_save_interval_s: float | None = 30.0


class CostMapper(Module):
    config: Config
    global_map: In[PointCloud2]
    global_costmap: Out[OccupancyGrid]
    persistent_costmap: Out[OccupancyGrid]
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
                if self._persistent_costmap is not None and getattr(
                    self, "_merge_live_into_persistent", False
                ):
                    # Throttle merges — only when the robot has moved or 2 s have
                    # passed since the last merge.
                    now = time.monotonic()
                    if (
                        now - getattr(self, "_last_merge_time", 0.0) > 2.0
                        or self._has_moved_since_last_merge(grid)
                    ):
                        grid = self._merge_grids(self._persistent_costmap, grid)
                        with self._latest_lock:
                            self._persistent_costmap = grid
                        self._last_merge_time = now  # type: ignore[attr-defined]
                        self._last_merge_origin = (  # type: ignore[attr-defined]
                            grid.origin.position.x,
                            grid.origin.position.y,
                        )
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
            # Re-publish persistent map after the Rerun bridge LCM listener is ready.
            import threading as _threading

            _threading.Thread(
                target=lambda: (
                    time.sleep(4.0),
                    self.publish_persistent_map(),
                ),
                daemon=True,
            ).start()

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

    def _has_moved_since_last_merge(self, grid: OccupancyGrid) -> bool:
        origin_x = grid.origin.position.x
        origin_y = grid.origin.position.y
        last = getattr(self, "_last_merge_origin", None)
        if last is None:
            return True
        return math.hypot(origin_x - last[0], origin_y - last[1]) > 0.5

    def _auto_save_map(self) -> None:
        with self._latest_lock:
            has_costmap = self._latest_costmap is not None

        if not has_costmap:
            return

        try:
            self.save_map()
        except Exception:
            logger.exception("Failed to auto-save persistent costmap.")

    @staticmethod
    def _merge_grids(base: OccupancyGrid, overlay: OccupancyGrid) -> OccupancyGrid:
        """Return a new grid: *base* with known cells from *overlay* on top.

        If the overlay extends beyond the base bounds the result is enlarged.
        """
        import numpy as np

        from dimos.msgs.nav_msgs.OccupancyGrid import CostValues

        r = base.resolution
        if not np.isclose(base.resolution, overlay.resolution):
            return overlay

        b_x0 = base.origin.position.x
        b_y0 = base.origin.position.y
        b_x1 = b_x0 + base.width * r
        b_y1 = b_y0 + base.height * r
        o_x0 = overlay.origin.position.x
        o_y0 = overlay.origin.position.y
        o_x1 = o_x0 + overlay.width * r
        o_y1 = o_y0 + overlay.height * r

        u_x0 = min(b_x0, o_x0)
        u_y0 = min(b_y0, o_y0)
        u_x1 = max(b_x1, o_x1)
        u_y1 = max(b_y1, o_y1)

        u_w = int(np.ceil((u_x1 - u_x0) / r))
        u_h = int(np.ceil((u_y1 - u_y0) / r))
        merged_grid = np.full((u_h, u_w), CostValues.UNKNOWN, dtype=np.int8)

        b_col = int(round((b_x0 - u_x0) / r))
        b_row = int(round((b_y0 - u_y0) / r))
        bh, bw = base.grid.shape
        merged_grid[b_row : b_row + bh, b_col : b_col + bw] = base.grid

        o_col = int(round((o_x0 - u_x0) / r))
        o_row = int(round((o_y0 - u_y0) / r))
        oh, ow = overlay.grid.shape
        o_known = overlay.grid != CostValues.UNKNOWN
        target = merged_grid[o_row : o_row + oh, o_col : o_col + ow]
        target[o_known] = overlay.grid[o_known]

        return OccupancyGrid(
            grid=merged_grid,
            resolution=r,
            origin=Pose(u_x0, u_y0, 0.0),
            frame_id=base.frame_id,
            ts=time.time(),
        )

    @rpc
    def save_map(self, path: str | None = None) -> str:
        map_path = self._resolve_map_path(path)
        with self._latest_lock:
            latest = self._latest_costmap

        if latest is None:
            raise ValueError("No current global costmap available to save")

        merged = latest
        if map_path.exists():
            try:
                previous = OccupancyGrid.from_path(map_path)
                merged = self._merge_grids(previous, latest)
                logger.info(
                    "Merged costmap: previous %dx%d + latest %dx%d → union %dx%d",
                    previous.width, previous.height,
                    latest.width, latest.height,
                    merged.width, merged.height,
                )
            except Exception:
                logger.debug("Could not merge with previous map, overwriting", exc_info=True)

        if map_path.suffix == ".npz":
            merged.save_npz(map_path)
        else:
            merged.save_to_directory(map_path)

        self._persistent_costmap = merged
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

        self.persistent_costmap.publish(grid)
        logger.info("Loaded persistent costmap.", path=str(map_path))
        return True

    @rpc
    def publish_persistent_map(self, shift_x: float = 0.0, shift_y: float = 0.0) -> bool:
        with self._latest_lock:
            persistent = self._persistent_costmap

        if persistent is None:
            return False

        self._merge_live_into_persistent = True
        self.persistent_costmap.publish(persistent)

        if shift_x != 0.0 or shift_y != 0.0:
            shifted = OccupancyGrid(
                grid=persistent.grid,
                resolution=persistent.resolution,
                origin=Pose(
                    persistent.origin.position.x + shift_x,
                    persistent.origin.position.y + shift_y,
                    persistent.origin.position.z,
                ),
                frame_id=persistent.frame_id,
                ts=time.time(),
            )
            with self._latest_lock:
                self._persistent_costmap = shifted
            self.global_costmap.publish(shifted)
            logger.info(
                "Published shifted persistent costmap to global_costmap (shift %.2f, %.2f)",
                shift_x, shift_y,
            )
        else:
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
            yaw_candidates_deg=tuple(
                yaw_candidates_deg
                or [0.0, 30.0, 60.0, 90.0, 120.0, 150.0, 180.0, -30.0, -60.0, -90.0, -120.0, -150.0]
            ),
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
