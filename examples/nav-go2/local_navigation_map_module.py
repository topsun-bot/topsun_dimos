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

"""Global costmap -> NavigationMap module for nav-go2."""

from __future__ import annotations

import threading
import time
from typing import Literal, Protocol

import numpy as np
from path_selector import TrajectoryPathSelector
from reactivex.disposable import Disposable

from dimos.core.core import rpc
from dimos.core.module import Module, ModuleConfig
from dimos.core.stream import In, Out
from dimos.msgs.nav_msgs.OccupancyGrid import OccupancyGrid
from dimos.navigation.replanning_a_star.navigation_map import NavigationMap
from dimos.spec.utils import Spec
from dimos.utils.logging_config import setup_logger

logger = setup_logger()


class LocalNavigationMapConfig(ModuleConfig):
    """Configuration for querying a NavigationMap from the latest costmap."""

    trajectory_frame_id: str = "base_link"
    selected_trajectory_index: int = 4
    navigation_map_gradient_strategy: Literal["gradient", "voronoi"] = "gradient"
    navigation_map_robot_increase: float = 2.0
    local_map_max_age_s: float = 0.5


class LocalNavigationMapSpec(Spec, Protocol):
    def select_best_trajectory_index(self, trajectories: np.ndarray, time_point: float) -> int: ...
    def get_last_local_occupancy_grid(self) -> OccupancyGrid | None: ...


class LocalNavigationMapModule(Module):
    """Query sampled trajectories against the latest global costmap.

    Candidate trajectories are transformed from ``trajectory_frame_id`` into
    the costmap frame before scoring with DimOS ``NavigationMap``.
    """

    config: LocalNavigationMapConfig
    global_costmap: In[OccupancyGrid]
    local_occupancy_grid: Out[OccupancyGrid]

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._lock = threading.RLock()
        self._last_costmap_time_s: float | None = None
        self._last_map_frame_id: str | None = None
        self._last_occupancy_grid: OccupancyGrid | None = None
        self._navigation_map = NavigationMap(
            self.config.g,
            self.config.navigation_map_gradient_strategy,
        )
        self._selector = TrajectoryPathSelector(
            preferred_index=self.config.selected_trajectory_index,
            navigation_map_robot_increase=self.config.navigation_map_robot_increase,
        )
        self._costmap_count = 0

    @rpc
    def start(self) -> None:
        super().start()
        self.register_disposable(Disposable(self.global_costmap.subscribe(self._on_global_costmap)))
        logger.info(
            "%s started (source=global_costmap, max_age=%.2fs, robot_increase=%.2fx)",
            self.__class__.__name__,
            self.config.local_map_max_age_s,
            self.config.navigation_map_robot_increase,
        )

    @rpc
    def stop(self) -> None:
        super().stop()

    def _on_global_costmap(self, occupancy_grid: OccupancyGrid) -> None:
        self._costmap_count += 1
        costmap_time = occupancy_grid.ts if getattr(occupancy_grid, "ts", None) else time.time()
        with self._lock:
            self._last_costmap_time_s = costmap_time
            self._last_map_frame_id = occupancy_grid.frame_id
            self._last_occupancy_grid = occupancy_grid
            self._navigation_map.update(occupancy_grid)
        self.local_occupancy_grid.publish(occupancy_grid)

        if self._costmap_count % 10 == 1:
            self._log_map_diagnostics(occupancy_grid)

    @rpc
    def select_best_trajectory_index(self, trajectories: np.ndarray, time_point: float) -> int:
        """Return the best trajectory index using the latest local NavigationMap."""
        with self._lock:
            self._ensure_recent_map_locked(time_point)
            navigation_map = self._navigation_map_snapshot_locked()
            map_frame_id = self._last_map_frame_id

        try:
            if map_frame_id is None:
                raise RuntimeError("No local map frame available")
            map_frame_trajectories = self._trajectories_in_map_frame(
                trajectories,
                map_frame_id,
                time_point,
            )
            return self._selector.select_best_index(map_frame_trajectories, navigation_map)
        except Exception as exc:
            raise RuntimeError(f"Path selection failed: {exc}") from exc

    @rpc
    def get_last_local_occupancy_grid(self) -> OccupancyGrid | None:
        """Return the most recently received costmap."""
        with self._lock:
            return self._last_occupancy_grid

    def _ensure_recent_map_locked(self, time_point: float) -> None:
        if self._last_costmap_time_s is None or self._last_occupancy_grid is None:
            raise RuntimeError("No recent global_costmap available for navigation map")

        age = abs(time_point - self._last_costmap_time_s)
        if age > self.config.local_map_max_age_s:
            raise RuntimeError(f"Latest global_costmap too far from selection time: {age:.2f}s")

    def _navigation_map_snapshot_locked(self) -> NavigationMap:
        if self._last_occupancy_grid is None:
            raise RuntimeError("No recent global_costmap available for navigation map")

        snapshot = NavigationMap(
            self.config.g,
            self.config.navigation_map_gradient_strategy,
        )
        snapshot.update(self._last_occupancy_grid.copy())
        return snapshot

    def _trajectories_in_map_frame(
        self,
        trajectories: np.ndarray,
        map_frame_id: str,
        time_point: float,
    ) -> np.ndarray:
        if map_frame_id == self.config.trajectory_frame_id:
            return trajectories

        tf = self.tf.get(
            map_frame_id,
            self.config.trajectory_frame_id,
            time_point=time_point,
            time_tolerance=1.0,
        )
        if tf is None:
            raise RuntimeError(
                f"No TF from {self.config.trajectory_frame_id} to {map_frame_id} "
                "for trajectory scoring"
            )

        original_shape = trajectories.shape
        points_xy = trajectories.reshape(-1, 2)
        points_h = np.column_stack(
            [
                points_xy[:, 0],
                points_xy[:, 1],
                np.zeros(points_xy.shape[0], dtype=points_xy.dtype),
                np.ones(points_xy.shape[0], dtype=points_xy.dtype),
            ]
        )
        transformed = (tf.to_matrix() @ points_h.T).T
        return transformed[:, :2].reshape(original_shape)

    def _log_map_diagnostics(self, occupancy_grid: OccupancyGrid) -> None:
        origin = occupancy_grid.origin.position
        grid_max_x = origin.x + occupancy_grid.width * occupancy_grid.resolution
        grid_max_y = origin.y + occupancy_grid.height * occupancy_grid.resolution
        logger.info(
            "Navigation map diagnostics: costmap_frame=%s "
            "grid_origin=(%.2f, %.2f) grid_max=(%.2f, %.2f) "
            "grid=%dx%d res=%.3f occ/free/unk=%d/%d/%d",
            occupancy_grid.frame_id,
            origin.x,
            origin.y,
            grid_max_x,
            grid_max_y,
            occupancy_grid.width,
            occupancy_grid.height,
            occupancy_grid.resolution,
            occupancy_grid.occupied_cells,
            occupancy_grid.free_cells,
            occupancy_grid.unknown_cells,
        )
