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

"""K-Means trajectory clustering + lidar screening + temporal memory for NoMaD paths."""

from __future__ import annotations

import numpy as np
from numpy.typing import NDArray
from sklearn.cluster import KMeans


class MultiWaypointsSelector:
    """Select one representative trajectory from a bundle of NoMaD candidate paths.

    Pipeline:
    1. Cluster trajectory endpoints (K-Means) and average each cluster into a
       representative path.
    2. Hard-reject paths whose waypoints come within ``collision_thresh`` of lidar.
    3. Score survivors by temporal continuity against the previous frame's endpoint.
    4. If every representative path collides, fall back to the path with the
       largest minimum clearance from lidar (for downstream APF to push away).
    """

    def __init__(
        self,
        collision_thresh: float = 0.25,
        memory_decay: float = 0.8,
        max_clusters: int = 2,
        kmeans_n_init: int = 10,
    ) -> None:
        self.collision_thresh = collision_thresh
        self.memory_decay = memory_decay
        self.max_clusters = max(1, max_clusters)
        self.kmeans_n_init = kmeans_n_init
        self.last_selected_endpoint: NDArray[np.floating] | None = None

    def reset(self) -> None:
        """Clear the temporal memory anchor (e.g. on mode switch or estop)."""
        self.last_selected_endpoint = None

    def select_best_trajectory(
        self,
        multi_waypoints: NDArray[np.floating],
        lidar_points: NDArray[np.floating],
        *,
        path_to_lidar: NDArray[np.floating] | None = None,
    ) -> NDArray[np.floating]:
        """Return the best single trajectory from ``multi_waypoints``.

        Clustering, memory scoring, and the returned path stay in the path frame
        (e.g. ``base_link``). When ``path_to_lidar`` is set, only the few
        representative trajectories (``M×N`` waypoints, not the full lidar scan)
        are transformed into the lidar frame for collision checks; the returned
        array is always the untransformed path-frame trajectory.

        Args:
            multi_waypoints: Candidate paths, shape ``(M, N, 2)`` in the path
                frame (typically ``base_link``).
            lidar_points: 2D lidar hits, shape ``(L, 2)`` in the lidar frame.
            path_to_lidar: Optional ``4×4`` or ``3×3`` rigid transform
                ``T_lidar←path`` (``tf.get(lidar_frame, path_frame).to_matrix()``).
                Omit when paths and lidar are already in the same frame.

        Returns:
            Selected path, shape ``(N, 2)`` in the path frame (unchanged coords).
        """
        trajectories = np.asarray(multi_waypoints, dtype=np.float64)
        lidar = np.asarray(lidar_points, dtype=np.float64)
        self._validate_trajectories(trajectories)
        if lidar.ndim != 2 or lidar.shape[1] != 2:
            raise ValueError(f"Expected lidar_points shape (L, 2), got {lidar.shape}")

        lidar_xy = lidar if lidar.size > 0 else np.empty((0, 2), dtype=np.float64)
        m_count = trajectories.shape[0]
        if m_count == 1:
            best = trajectories[0]
            self.last_selected_endpoint = best[-1].copy()
            return best

        representative_paths = self._cluster_representative_paths(trajectories)

        safe_paths: list[NDArray[np.floating]] = []
        for path in representative_paths:
            if not self._path_collides(path, lidar_xy, path_to_lidar):
                safe_paths.append(path)

        if safe_paths:
            best_path = self._select_by_memory(safe_paths)
        else:
            best_path = self._fallback_farthest_from_lidar(
                representative_paths, lidar_xy, path_to_lidar
            )

        self.last_selected_endpoint = best_path[-1].copy()
        return best_path

    def _cluster_representative_paths(
        self,
        multi_waypoints: NDArray[np.floating],
    ) -> list[NDArray[np.floating]]:
        """Cluster by endpoint and return per-cluster mean trajectories."""
        m_count, _, _ = multi_waypoints.shape
        n_clusters = min(self.max_clusters, m_count)
        endpoints = multi_waypoints[:, -1, :]

        if n_clusters == 1:
            return [np.mean(multi_waypoints, axis=0)]

        kmeans = KMeans(n_clusters=n_clusters, n_init=self.kmeans_n_init, random_state=0)
        labels = kmeans.fit_predict(endpoints)

        representatives: list[NDArray[np.floating]] = []
        for cluster_idx in range(n_clusters):
            cluster_mask = labels == cluster_idx
            cluster_paths = multi_waypoints[cluster_mask]
            representatives.append(np.mean(cluster_paths, axis=0))
        return representatives

    def _path_for_collision(
        self,
        path: NDArray[np.floating],
        path_to_lidar: NDArray[np.floating] | None,
    ) -> NDArray[np.floating]:
        if path_to_lidar is None:
            return path
        return self._transform_points_2d(path, path_to_lidar)

    def _path_collides(
        self,
        path: NDArray[np.floating],
        lidar_points: NDArray[np.floating],
        path_to_lidar: NDArray[np.floating] | None = None,
    ) -> bool:
        if lidar_points.shape[0] == 0:
            return False
        path_lidar = self._path_for_collision(path, path_to_lidar)
        min_dists = self._waypoint_min_lidar_distances(path_lidar, lidar_points)
        return bool(np.any(min_dists < self.collision_thresh))

    @staticmethod
    def _waypoint_min_lidar_distances(
        path: NDArray[np.floating],
        lidar_points: NDArray[np.floating],
    ) -> NDArray[np.floating]:
        diff = path[:, None, :] - lidar_points[None, :, :]
        return np.hypot(diff[..., 0], diff[..., 1]).min(axis=1)

    def _path_clearance(
        self,
        path: NDArray[np.floating],
        lidar_points: NDArray[np.floating],
        path_to_lidar: NDArray[np.floating] | None = None,
    ) -> float:
        """Minimum distance from any waypoint on the path to the nearest lidar hit."""
        if lidar_points.shape[0] == 0:
            return float("inf")
        path_lidar = self._path_for_collision(path, path_to_lidar)
        return float(np.min(self._waypoint_min_lidar_distances(path_lidar, lidar_points)))

    def _select_by_memory(
        self,
        candidate_paths: list[NDArray[np.floating]],
    ) -> NDArray[np.floating]:
        best_path: NDArray[np.floating] | None = None
        best_score = -float("inf")

        for path in candidate_paths:
            score = 0.0
            if self.last_selected_endpoint is not None:
                end_dist = float(
                    np.hypot(
                        path[-1, 0] - self.last_selected_endpoint[0],
                        path[-1, 1] - self.last_selected_endpoint[1],
                    )
                )
                score -= end_dist * self.memory_decay

            if score > best_score:
                best_score = score
                best_path = path

        assert best_path is not None
        return best_path

    def _fallback_farthest_from_lidar(
        self,
        candidate_paths: list[NDArray[np.floating]],
        lidar_points: NDArray[np.floating],
        path_to_lidar: NDArray[np.floating] | None = None,
    ) -> NDArray[np.floating]:
        return max(
            candidate_paths,
            key=lambda p: self._path_clearance(p, lidar_points, path_to_lidar),
        )

    @staticmethod
    def _transform_points_2d(
        points_xy: NDArray[np.floating],
        transform: NDArray[np.floating],
    ) -> NDArray[np.floating]:
        """Map ``(L, 2)`` points from source frame into target frame."""
        points = np.asarray(points_xy, dtype=np.float64)
        matrix = np.asarray(transform, dtype=np.float64)
        if points.ndim != 2 or points.shape[1] != 2:
            raise ValueError(f"Expected points_xy shape (L, 2), got {points.shape}")

        if matrix.shape == (4, 4):
            homogeneous = np.column_stack(
                [
                    points[:, 0],
                    points[:, 1],
                    np.zeros(points.shape[0], dtype=np.float64),
                    np.ones(points.shape[0], dtype=np.float64),
                ]
            )
            transformed = (matrix @ homogeneous.T).T
            return transformed[:, :2]

        if matrix.shape == (3, 3):
            rotation = matrix[:2, :2]
            translation = matrix[:2, 2]
            return points @ rotation.T + translation

        raise ValueError(f"path_to_lidar must be 4x4 or 3x3, got shape {matrix.shape}")

    @staticmethod
    def _validate_trajectories(trajectories: NDArray[np.floating]) -> None:
        if trajectories.ndim != 3 or trajectories.shape[-1] != 2:
            raise ValueError(f"Expected (M, N, 2) multi_waypoints, got {trajectories.shape}")
        if trajectories.shape[0] == 0:
            raise ValueError("No candidate trajectories available")
