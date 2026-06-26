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
    persistent_map_yaml: str | None = None
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
            publish_grid = grid
            with self._latest_lock:
                self._latest_costmap = grid
                if self._persistent_costmap is not None:
                    if getattr(self, "_merge_live_into_persistent", False):
                        # Throttle merges — only when the robot has moved or 2 s have
                        # passed since the last merge.
                        now = time.monotonic()
                        if (
                            now - getattr(self, "_last_merge_time", 0.0) > 2.0
                            or self._has_moved_since_last_merge(grid)
                        ):
                            merged = self._merge_grids(self._persistent_costmap, grid)
                            self._persistent_costmap = merged
                            publish_grid = merged
                            self._last_merge_time = now  # type: ignore[attr-defined]
                            self._last_merge_origin = (  # type: ignore[attr-defined]
                                merged.origin.position.x,
                                merged.origin.position.y,
                            )
                    else:
                        # Before relocalization, still publish the live costmap so
                        # that the A* planner uses real-time obstacle data.
                        # The persistent map is only merged after
                        # _merge_live_into_persistent is explicitly set to True
                        # (post-relocalization).
                        pass
            self.global_costmap.publish(publish_grid)

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
            self.load_map(publish=False)

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
    def publish_persistent_map(
        self, shift_x: float = 0.0, shift_y: float = 0.0, rotate_deg: float = 0.0
    ) -> bool:
        """Publish the persistent map transformed into the odom frame.

        The caller passes the *inverse* of map_from_current as shift + rotate:
          shift_x = -tx, shift_y = -ty, rotate_deg = -tyaw_deg
        This function applies: for each world point P_map in the grid,
          P_odom = R(rotate_deg) * (P_map - grid_center) + grid_center + (shift_x, shift_y)
        However we implement this as a grid-level remap for efficiency.
        """
        with self._latest_lock:
            persistent = self._persistent_costmap

        if persistent is None:
            return False

        self._merge_live_into_persistent = True

        needs_shift = shift_x != 0.0 or shift_y != 0.0
        needs_rotate = abs(rotate_deg) > 0.5

        if needs_shift or needs_rotate:
            import numpy as np

            grid = persistent.grid
            res = persistent.resolution
            old_H, old_W = grid.shape
            ox = persistent.origin.position.x
            oy = persistent.origin.position.y

            if needs_rotate:
                angle_rad = math.radians(rotate_deg)
                cos_a = math.cos(angle_rad)
                sin_a = math.sin(angle_rad)

                # The transform for each map point P_map to odom:
                #   P_odom = R(angle) * (P_map + (shift_x, shift_y))
                # where R is rotation around the WORLD ORIGIN (0,0).
                # Equivalently: shift first, then rotate around origin.

                # Compute rotated bounding box of the shifted grid
                shifted_corners = [
                    (ox + shift_x, oy + shift_y),
                    (ox + old_W * res + shift_x, oy + shift_y),
                    (ox + old_W * res + shift_x, oy + old_H * res + shift_y),
                    (ox + shift_x, oy + old_H * res + shift_y),
                ]
                rotated_corners = []
                for wx, wy in shifted_corners:
                    rx = cos_a * wx - sin_a * wy
                    ry = sin_a * wx + cos_a * wy
                    rotated_corners.append((rx, ry))

                new_min_x = min(c[0] for c in rotated_corners)
                new_min_y = min(c[1] for c in rotated_corners)
                new_max_x = max(c[0] for c in rotated_corners)
                new_max_y = max(c[1] for c in rotated_corners)

                new_ox = new_min_x
                new_oy = new_min_y
                new_W = int(math.ceil((new_max_x - new_min_x) / res))
                new_H = int(math.ceil((new_max_y - new_min_y) / res))

                # Inverse-map each new cell back to old grid (vectorized).
                cols = np.arange(new_W, dtype=np.float32)
                rows = np.arange(new_H, dtype=np.float32)
                cc, rr = np.meshgrid(cols, rows)

                # New world coords (cell centers) = odom coords
                new_wx = new_ox + cc * res + res * 0.5
                new_wy = new_oy + rr * res + res * 0.5

                # Inverse: P_map = R(-angle) * P_odom - (shift_x, shift_y)
                inv_cos = math.cos(-angle_rad)
                inv_sin = math.sin(-angle_rad)
                map_wx = inv_cos * new_wx - inv_sin * new_wy - shift_x
                map_wy = inv_sin * new_wx + inv_cos * new_wy - shift_y

                # Map to old grid indices
                old_col = ((map_wx - ox) / res).astype(np.intp)
                old_row = ((map_wy - oy) / res).astype(np.intp)

                valid = (old_col >= 0) & (old_col < old_W) & (old_row >= 0) & (old_row < old_H)
                new_grid = np.full((new_H, new_W), -1, dtype=np.int8)
                new_grid[valid] = grid[old_row[valid], old_col[valid]]

                origin_x = new_ox
                origin_y = new_oy
                result_grid = new_grid
            else:
                origin_x = ox + shift_x
                origin_y = oy + shift_y
                result_grid = grid

            shifted = OccupancyGrid(
                grid=result_grid,
                resolution=res,
                origin=Pose(origin_x, origin_y, persistent.origin.position.z),
                frame_id=persistent.frame_id,
                ts=time.time(),
            )
            with self._latest_lock:
                self._persistent_costmap = shifted
            self.global_costmap.publish(shifted)
            logger.info(
                "Published transformed persistent costmap to global_costmap "
                "(shift %.2f, %.2f, rotate %.1f°, size %dx%d)",
                shift_x, shift_y, rotate_deg,
                result_grid.shape[1], result_grid.shape[0],
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
        hint_x: float | None = None,
        hint_y: float | None = None,
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
            hint_x=hint_x,
            hint_y=hint_y,
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
        if self.config.persistent_map_yaml is not None:
            return Path(self.config.persistent_map_yaml).expanduser()
        if self.config.persistent_map_dir is not None:
            return Path(self.config.persistent_map_dir).expanduser()
        return STATE_DIR / "maps" / "default" / "costmap"
