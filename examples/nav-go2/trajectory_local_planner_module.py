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
from typing import Literal

from local_navigation_map_module import LocalNavigationMapSpec
import numpy as np
from trajectory_inference import (
    TrajectoryNavigationEngine,
    TrajectoryNavigationRuntimeError,
    dimos_image_to_pil,
)
from traversability_grid import rasterize_trajectories_to_costmap

from dimos.core.core import rpc
from dimos.core.module import Module, ModuleConfig
from dimos.core.stream import In, Out
from dimos.msgs.geometry_msgs.PoseStamped import PoseStamped
from dimos.msgs.nav_msgs.OccupancyGrid import OccupancyGrid
from dimos.msgs.nav_msgs.Path import Path
from dimos.msgs.sensor_msgs.Image import Image
from dimos.utils.logging_config import setup_logger

logger = setup_logger()


class TrajectoryLocalPlannerConfig(ModuleConfig):
    """Model-independent trajectory local planner parameters."""

    trajectory_frame_id: str = "base_link"
    selected_trajectory_index: int = 4

    navigation_map_gradient_strategy: Literal["gradient", "voronoi"] = "gradient"
    navigation_map_robot_increase: float = 2.0
    local_map_max_age_s: float = 0.5

    publish_debug_traversability_map: bool = True
    grid_forward_m: float = 4.0
    grid_lateral_m: float = 3.0
    grid_resolution_m: float = 0.05

    waypoints_output_frame_id: str = "odom"
    min_inference_interval_s: float = 0.25


class TrajectoryLocalPlannerModule(Module):
    """Subscribe to camera frames, run a trajectory model, publish local waypoints.

    Subclasses provide the concrete model engine. Route selection uses
    :class:`LocalNavigationMapModule` (global costmap + DimOS ``NavigationMap``).
    """

    config: TrajectoryLocalPlannerConfig
    _local_navigation_map: LocalNavigationMapSpec | None = None
    color_image: In[Image]
    local_waypoints: Out[Path]
    odom_waypoints: Out[Path]
    candidate_paths: Out[list[Path]]
    traversability_map: Out[OccupancyGrid]
    engine_name = "trajectory model"

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._engine = self._make_engine()
        self._lock = threading.Lock()
        self._last_infer_monotonic = 0.0
        self._frame_count = 0

    def _make_engine(self) -> TrajectoryNavigationEngine:
        raise NotImplementedError

    @rpc
    def start(self) -> None:
        super().start()
        self.color_image.subscribe(self._on_image)
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
                frame_id=self.config.trajectory_frame_id,
                ts=image.ts,
            )
            if self.config.publish_debug_traversability_map:
                self.traversability_map.publish(grid)

            try:
                if self._local_navigation_map is None:
                    raise TrajectoryNavigationRuntimeError(
                        "LocalNavigationMapModule is not connected"
                    )
                best_index = self._local_navigation_map.select_best_trajectory_index(
                    result.trajectories
                )
                local_waypoints = self._trajectory_to_path(
                    result.trajectories[best_index],
                    frame_id=self.config.trajectory_frame_id,
                    ts=image.ts,
                )
            except Exception as exc:
                logger.error("Local navigation map failed: %s", exc)
                return

            self._last_infer_monotonic = now
            self.local_waypoints.publish(local_waypoints)
            transformed_waypoints = self._path_to_frame(
                local_waypoints,
                self.config.waypoints_output_frame_id,
            )
            if transformed_waypoints is not None:
                self.odom_waypoints.publish(transformed_waypoints)

            candidates = self._trajectories_to_paths(
                result.trajectories,
                frame_id=self.config.trajectory_frame_id,
                ts=image.ts,
            )
            self.candidate_paths.publish(candidates)

            if self._frame_count % 20 == 0:
                free_cells = int((grid.grid == 0).sum()) if grid is not None else -1
                logger.info(
                    "Published local_waypoints (%d poses, candidates=%d, free_cells=%d, "
                    "waypoint=%.2f, %.2f)",
                    len(local_waypoints.poses),
                    len(candidates),
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

    def _path_to_frame(self, path: Path, target_frame_id: str) -> Path | None:
        if path.frame_id == target_frame_id:
            return path

        tf = self.tf.get(
            target_frame_id,
            path.frame_id,
            time_point=path.ts,
            time_tolerance=1.0,
        )
        if tf is None:
            if self._frame_count % 30 == 1:
                logger.warning(
                    "No TF from %s to %s; skipping %s waypoints output.",
                    path.frame_id,
                    target_frame_id,
                    target_frame_id,
                )
            return None

        matrix = tf.to_matrix()
        transformed_poses = []
        for pose in path.poses:
            point = np.array([pose.x, pose.y, pose.z, 1.0], dtype=np.float64)
            transformed = matrix @ point
            transformed_poses.append(
                PoseStamped(
                    ts=pose.ts,
                    frame_id=target_frame_id,
                    position=[
                        float(transformed[0]),
                        float(transformed[1]),
                        float(transformed[2]),
                    ],
                )
            )
        return Path(ts=path.ts, frame_id=target_frame_id, poses=transformed_poses)
