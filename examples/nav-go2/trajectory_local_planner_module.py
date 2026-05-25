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
from multi_waypoints_selector import MultiWaypointsSelector
import numpy as np
from reactivex.disposable import Disposable
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
from dimos.msgs.sensor_msgs.PointCloud2 import PointCloud2
from dimos.utils.logging_config import setup_logger

logger = setup_logger()

WaypointSelectionMode = Literal["navigation_map", "multi_waypoints"]


class TrajectoryLocalPlannerConfig(ModuleConfig):
    """Model-independent trajectory local planner parameters."""

    trajectory_frame_id: str = "base_link"

    # ``navigation_map``: global costmap + DimOS NavigationMap (needs mapping stack).
    # ``multi_waypoints``: K-Means + live lidar collision + temporal memory.
    waypoint_selection: WaypointSelectionMode = "navigation_map"

    # Parameters for navigation_map mode using LocalNavigationMapModule RPC.
    selected_trajectory_index: int = 4
    navigation_map_gradient_strategy: Literal["gradient", "voronoi"] = "gradient"
    navigation_map_robot_increase: float = 2.0
    local_map_max_age_s: float = 0.5

    # Parameters for multi_waypoints mode using MultiWaypointsSelector and lidar.
    collision_thresh: float = 0.25
    memory_decay: float = 0.8
    max_clusters: int = 2
    lidar_z_min: float = -0.5
    lidar_z_max: float = 0.5
    lidar_max_range_m: float = 5.0
    lidar_max_points: int = 2000
    lidar_max_age_s: float = 0.5

    publish_debug_traversability_map: bool = True
    grid_forward_m: float = 4.0
    grid_lateral_m: float = 3.0
    grid_resolution_m: float = 0.05

    waypoints_output_frame_id: str = "odom"
    min_inference_interval_s: float = 0.25


class TrajectoryLocalPlannerModule(Module):
    """Subscribe to camera frames, run a trajectory model, publish local waypoints.

    Route selection is controlled by ``config.waypoint_selection``:

    - ``navigation_map``: RPC to :class:`LocalNavigationMapModule` (costmap scoring).
    - ``multi_waypoints``: :class:`MultiWaypointsSelector` with live ``lidar`` input.
    """

    config: TrajectoryLocalPlannerConfig
    _local_navigation_map: LocalNavigationMapSpec | None = None
    color_image: In[Image]
    lidar: In[PointCloud2]
    local_waypoints: Out[Path]
    odom_waypoints: Out[Path]
    candidate_paths: Out[list[Path]]
    traversability_map: Out[OccupancyGrid]
    engine_name = "trajectory model"

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._engine = self._make_engine()
        self._multi_selector = MultiWaypointsSelector(
            collision_thresh=self.config.collision_thresh,
            memory_decay=self.config.memory_decay,
            max_clusters=self.config.max_clusters,
        )
        self._lidar_lock = threading.Lock()
        self._latest_lidar_xy = np.empty((0, 2), dtype=np.float64)
        self._latest_lidar_frame_id: str | None = None
        self._latest_lidar_time_s: float | None = None
        self._last_infer_monotonic = 0.0
        self._frame_count = 0

    def _make_engine(self) -> TrajectoryNavigationEngine:
        raise NotImplementedError

    def _uses_navigation_map(self) -> bool:
        return self.config.waypoint_selection == "navigation_map"

    @rpc
    def start(self) -> None:
        super().start()
        self.register_disposable(Disposable(self.color_image.subscribe(self._on_image)))
        if not self._uses_navigation_map():
            self.register_disposable(Disposable(self.lidar.subscribe(self._on_lidar)))
        try:
            self._engine.initialize()
            logger.info("%s engine initialized", self.engine_name)
        except TrajectoryNavigationRuntimeError as exc:
            logger.warning("%s not available at start: %s", self.engine_name, exc)
        logger.info(
            "%s started (interval=%.2fs, waypoint_selection=%s)",
            self.__class__.__name__,
            self.config.min_inference_interval_s,
            self.config.waypoint_selection,
        )

    @rpc
    def stop(self) -> None:
        if not self._uses_navigation_map():
            self._multi_selector.reset()
        super().stop()

    def _on_lidar(self, cloud: PointCloud2) -> None:
        if self._uses_navigation_map():
            return
        lidar_xy = self._pointcloud_to_lidar_xy(cloud)
        cloud_ts = getattr(cloud, "ts", None)
        stamp = cloud_ts if cloud_ts is not None else time.time()
        with self._lidar_lock:
            self._latest_lidar_xy = lidar_xy
            self._latest_lidar_frame_id = cloud.frame_id
            self._latest_lidar_time_s = stamp

    def _on_image(self, image: Image) -> None:
        self._frame_count += 1
        now = time.monotonic()
        if now - self._last_infer_monotonic < self.config.min_inference_interval_s:
            return

        try:
            pil = dimos_image_to_pil(image)
            self._engine.push_observation(pil)
            if not self._engine.context_ready():
                if self._frame_count % 20 == 1:
                    logger.info(
                        "%s warming up image context (frames before inference)",
                        self.engine_name,
                    )
                return
            if not self._engine.ready:
                self._engine.initialize()
            result = self._engine.infer_exploration()
            self._last_infer_monotonic = now
        except TrajectoryNavigationRuntimeError as exc:
            if self._frame_count % 30 == 1:
                logger.warning("%s inference skipped: %s", self.engine_name, exc)
            return

        try:
            self._validate_trajectories(result.trajectories)
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
            best_trajectory = self._select_best_trajectory(result.trajectories, image.ts)
            local_waypoints = self._trajectory_to_path(
                best_trajectory,
                frame_id=self.config.trajectory_frame_id,
                ts=image.ts,
            )
        except Exception as exc:
            logger.error(
                "Trajectory selection failed (%s): %s",
                self.config.waypoint_selection,
                exc,
                exc_info=True,
            )
            return

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
            goal_distance = (
                f"{result.goal_distance:.2f}" if result.goal_distance is not None else "n/a"
            )
            logger.info(
                "Published local_waypoints (%d poses, candidates=%d, free_cells=%d, "
                "selection=%s, goal_distance=%s, waypoint=%.2f, %.2f)",
                len(local_waypoints.poses),
                len(candidates),
                free_cells,
                self.config.waypoint_selection,
                goal_distance,
                result.chosen_waypoint[0],
                result.chosen_waypoint[1],
            )

    def _select_best_trajectory(
        self,
        trajectories: np.ndarray,
        time_point: float,
    ) -> np.ndarray:
        if self._uses_navigation_map():
            return self._select_via_navigation_map(trajectories, time_point)
        return self._select_via_multi_waypoints(trajectories, time_point)

    def _select_via_navigation_map(self, trajectories: np.ndarray, time_point: float) -> np.ndarray:
        if self._local_navigation_map is None:
            raise TrajectoryNavigationRuntimeError(
                "LocalNavigationMapModule is not connected "
                "(required for waypoint_selection='navigation_map')"
            )
        best_index = self._local_navigation_map.select_best_trajectory_index(
            trajectories,
            time_point,
        )
        return trajectories[best_index]

    def _select_via_multi_waypoints(
        self,
        trajectories: np.ndarray,
        time_point: float,
    ) -> np.ndarray:
        lidar_xy, path_to_lidar = self._lidar_for_selection(time_point)
        return self._multi_selector.select_best_trajectory(
            trajectories,
            lidar_xy,
            path_to_lidar=path_to_lidar,
        )

    def _pointcloud_to_lidar_xy(self, cloud: PointCloud2) -> np.ndarray:
        points = cloud.points_f32()
        if points.shape[0] == 0:
            return np.empty((0, 2), dtype=np.float64)

        z_mask = (points[:, 2] >= self.config.lidar_z_min) & (
            points[:, 2] <= self.config.lidar_z_max
        )
        xy = points[z_mask, :2].astype(np.float64)
        max_range = self.config.lidar_max_range_m
        if max_range > 0.0 and xy.shape[0] > 0:
            ranges = np.hypot(xy[:, 0], xy[:, 1])
            xy = xy[ranges <= max_range]
        max_points = self.config.lidar_max_points
        if max_points > 0 and xy.shape[0] > max_points:
            step = max(1, xy.shape[0] // max_points)
            xy = xy[::step, :][:max_points]
        return xy

    def _lidar_for_selection(
        self,
        time_point: float,
    ) -> tuple[np.ndarray, np.ndarray | None]:
        with self._lidar_lock:
            lidar_xy = self._latest_lidar_xy.copy()
            lidar_frame = self._latest_lidar_frame_id
            lidar_time = self._latest_lidar_time_s

        if lidar_xy.shape[0] == 0 or lidar_frame is None or lidar_time is None:
            return np.empty((0, 2), dtype=np.float64), None

        age = abs(time_point - lidar_time)
        if age > self.config.lidar_max_age_s:
            if self._frame_count % 30 == 1:
                logger.warning("Latest lidar too old (%.2fs); skipping collision check", age)
            return np.empty((0, 2), dtype=np.float64), None

        path_frame = self.config.trajectory_frame_id
        if lidar_frame == path_frame:
            return lidar_xy, None

        path_to_lidar = self._lookup_path_to_lidar(lidar_frame, path_frame, time_point)
        if path_to_lidar is None:
            if self._frame_count % 30 == 1:
                logger.warning(
                    "No TF from %s to %s; skipping lidar collision check",
                    path_frame,
                    lidar_frame,
                )
            return np.empty((0, 2), dtype=np.float64), None

        return lidar_xy, path_to_lidar

    def _lookup_path_to_lidar(
        self,
        lidar_frame: str,
        path_frame: str,
        time_point: float,
    ) -> np.ndarray | None:
        tf = self.tf.get(
            lidar_frame,
            path_frame,
            time_point=time_point,
            time_tolerance=1.0,
        )
        if tf is None:
            return None
        return tf.to_matrix()

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
