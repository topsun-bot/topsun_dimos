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

"""DimOS module: RGB observations -> trajectory model -> local waypoints."""

from __future__ import annotations

import threading
import time

import numpy as np

from dimos.core.core import rpc
from dimos.core.global_config import global_config
from dimos.core.module import Module
from dimos.core.stream import In, Out
from dimos.msgs.geometry_msgs.PoseStamped import PoseStamped
from dimos.msgs.nav_msgs.OccupancyGrid import OccupancyGrid
from dimos.msgs.nav_msgs.Path import Path
from dimos.msgs.sensor_msgs.Image import Image
from dimos.navigation.replanning_a_star.navigation_map import NavigationMap
from dimos.utils.logging_config import setup_logger

from path_selector import TrajectoryPathSelector
from trajectory_inference import (
    TrajectoryNavigationEngine,
    TrajectoryNavigationRuntimeError,
    dimos_image_to_pil,
)
from trajectory_planner_config import TrajectoryLocalPlannerConfig
from traversability_grid import rasterize_trajectories_to_costmap

logger = setup_logger()


class TrajectoryLocalPlannerModule(Module):
    """Subscribe to camera frames, run a trajectory model, publish local waypoints.

    Subclasses provide the concrete model engine. The shared module owns DimOS
    stream wiring, inference throttling, route selection, debug map rasterization,
    and RPC state.

    Outputs:
        local_waypoints: Selected robot-centric route in ``base_link``.
        candidate_paths: All sampled trajectories as separate paths in ``base_link``.
        traversability_map: Robot-centric grid in ``base_link``. Value 0 = highly
            traversable (many sampled trajectories agree); 100 = no sample passed through.
            Intended as a debug artifact.
    """

    config: TrajectoryLocalPlannerConfig
    color_image: In[Image]
    navigation_costmap: In[OccupancyGrid]
    local_waypoints: Out[Path]
    candidate_paths: Out[list[Path]]
    traversability_map: Out[OccupancyGrid]
    engine_name = "trajectory model"

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._engine = self._make_engine()
        self._lock = threading.Lock()
        self._last_infer_monotonic = 0.0
        self._frame_count = 0
        self._last_waypoints: Path | None = None
        self._last_candidate_paths: list[Path] | None = None
        self._last_map: OccupancyGrid | None = None
        self._latest_navigation_costmap: OccupancyGrid | None = None
        self._selector = TrajectoryPathSelector(
            grid_lateral_m=self.config.grid_lateral_m,
            grid_resolution_m=self.config.grid_resolution_m,
            preferred_index=self.config.selected_trajectory_index,
        )
        self._navigation_map = NavigationMap(
            global_config,
            self.config.navigation_map_gradient_strategy,
        )

    def _make_engine(self) -> TrajectoryNavigationEngine:
        raise NotImplementedError

    @rpc
    def start(self) -> None:
        super().start()
        self.color_image.subscribe(self._on_image)
        self.navigation_costmap.subscribe(self._on_navigation_costmap)
        try:
            self._engine.initialize()
            logger.info("%s engine initialized", self.engine_name)
        except TrajectoryNavigationRuntimeError as exc:
            logger.warning("%s not available at start: %s", self.engine_name, exc)
        logger.info(
            "%s started (interval=%.2fs)",
            self.__class__.__name__,
            self.config.min_inference_interval_s,
        )

    @rpc
    def stop(self) -> None:
        super().stop()

    def _on_image(self, image: Image) -> None:
        self._frame_count += 1
        now = time.monotonic()
        if now - self._last_infer_monotonic < self.config.min_inference_interval_s:
            return

        with self._lock:
            try:
                pil = dimos_image_to_pil(image)
                self._engine.push_observation(pil)
                if not self._engine.context_ready():
                    return
                if not self._engine.ready:
                    self._engine.initialize()
                result = self._engine.infer_exploration()
            except TrajectoryNavigationRuntimeError as exc:
                if self._frame_count % 30 == 1:
                    logger.warning("%s inference skipped: %s", self.engine_name, exc)
                return

            grid = rasterize_trajectories_to_costmap(
                result.trajectories,
                forward_m=self.config.grid_forward_m,
                lateral_m=self.config.grid_lateral_m,
                resolution_m=self.config.grid_resolution_m,
                frame_id="base_link",
                ts=image.ts,
            )
            if self.config.publish_debug_traversability_map:
                self._last_map = grid
                self.traversability_map.publish(grid)

            if self._latest_navigation_costmap is None:
                logger.warning(
                    "%s path selection skipped: no latest navigation costmap available",
                    self.engine_name,
                )
                return

            try:
                local_waypoints = self._select_local_waypoints(
                    result.trajectories,
                    frame_id="base_link",
                    ts=image.ts,
                    navigation_map=self._navigation_map,
                )
            except TrajectoryNavigationRuntimeError as exc:
                logger.warning("%s route selection skipped: %s", self.engine_name, exc)
                return

            self._last_waypoints = local_waypoints
            self._last_infer_monotonic = now
            self.local_waypoints.publish(local_waypoints)

            candidates = self._trajectories_to_paths(
                result.trajectories,
                frame_id="base_link",
                ts=image.ts,
            )
            self._last_candidate_paths = candidates
            self.candidate_paths.publish(candidates)

            if self._frame_count % 20 == 0:
                free_cells = int((grid.grid == 0).sum()) if grid is not None else -1
                n_candidates = len(self._last_candidate_paths or [])
                logger.info(
                    "Published local_waypoints (%d poses, candidates=%d, free_cells=%d, "
                    "waypoint=%.2f, %.2f)",
                    len(local_waypoints.poses),
                    n_candidates,
                    free_cells,
                    result.chosen_waypoint[0],
                    result.chosen_waypoint[1],
                )

    def _validate_trajectories(self, trajectories: np.ndarray) -> None:
        if trajectories.ndim != 3 or trajectories.shape[-1] != 2:
            raise TrajectoryNavigationRuntimeError(
                f"Expected (N, T, 2) trajectories, got {trajectories.shape}"
            )
        if trajectories.shape[0] == 0:
            raise TrajectoryNavigationRuntimeError("No candidate trajectories available")

    def _trajectory_to_path(
        self,
        trajectory: np.ndarray,
        *,
        frame_id: str,
        ts: float,
    ) -> Path:
        poses = [
            PoseStamped(ts=ts, frame_id=frame_id, position=[float(x), float(y), 0.0])
            for x, y in trajectory
        ]
        return Path(ts=ts, frame_id=frame_id, poses=poses)

    def _trajectories_to_paths(
        self,
        trajectories: np.ndarray,
        *,
        frame_id: str,
        ts: float,
    ) -> list[Path]:
        self._validate_trajectories(trajectories)
        return [
            self._trajectory_to_path(trajectories[i], frame_id=frame_id, ts=ts)
            for i in range(trajectories.shape[0])
        ]

    def _select_local_waypoints(
        self,
        trajectories: np.ndarray,
        *,
        frame_id: str,
        ts: float,
        navigation_map: NavigationMap,
    ) -> Path:
        self._validate_trajectories(trajectories)
        best_index = self._selector.select_best_index(
            trajectories,
            navigation_map,
        )
        return self._trajectory_to_path(
            trajectories[best_index],
            frame_id=frame_id,
            ts=ts,
        )

    def _on_navigation_costmap(self, costmap: OccupancyGrid) -> None:
        if costmap.frame_id != "base_link":
            logger.warning(
                "Received navigation costmap in %s, expected base_link; skipping.",
                costmap.frame_id,
            )
            # return
        self._latest_navigation_costmap = costmap
        self._navigation_map.update(costmap)

    @rpc
    def get_last_local_waypoints(self) -> Path | None:
        """Return the most recently published local waypoint path."""
        return self._last_waypoints

    @rpc
    def get_last_candidate_paths(self) -> list[Path] | None:
        """Return the most recently published candidate trajectory paths."""
        return self._last_candidate_paths

    @rpc
    def get_last_traversability_map(self) -> OccupancyGrid | None:
        """Return the most recently published debug traversability grid."""
        return self._last_map
