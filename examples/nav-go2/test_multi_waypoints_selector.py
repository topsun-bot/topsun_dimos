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

from pathlib import Path
import sys

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))

from multi_waypoints_selector import MultiWaypointsSelector


def _make_bundle(
    left_y: float,
    right_y: float,
    n_paths: int = 5,
    n_points: int = 8,
) -> np.ndarray:
    """Several noisy paths ending left or right."""
    xs = np.linspace(0.2, 2.0, n_points)
    paths = []
    for i in range(n_paths):
        y_end = left_y if i % 2 == 0 else right_y
        ys = np.linspace(0.0, y_end, n_points) + np.random.default_rng(i).normal(0, 0.02, n_points)
        paths.append(np.column_stack([xs, ys]))
    return np.stack(paths, axis=0)


def _base_to_lidar_tf() -> np.ndarray:
    """90° CCW: (x, y)_base -> (-y, x)_lidar."""
    return np.array(
        [
            [0.0, -1.0, 0.0, 0.0],
            [1.0, 0.0, 0.0, 0.0],
            [0.0, 0.0, 1.0, 0.0],
            [0.0, 0.0, 0.0, 1.0],
        ],
        dtype=np.float64,
    )


def test_single_trajectory_passthrough() -> None:
    path = np.array([[[0.0, 0.0], [1.0, 0.0], [2.0, 0.0]]], dtype=np.float64)
    selector = MultiWaypointsSelector()
    out = selector.select_best_trajectory(path, np.empty((0, 2)))
    np.testing.assert_allclose(out, path[0])
    np.testing.assert_allclose(selector.last_selected_endpoint, path[0, -1])


def test_collision_rejects_left_prefers_right() -> None:
    bundle = _make_bundle(left_y=0.6, right_y=-0.6, n_paths=6)
    lidar_left = np.array([[1.5, 0.55], [1.8, 0.5]], dtype=np.float64)
    selector = MultiWaypointsSelector(collision_thresh=0.25, max_clusters=2)
    out = selector.select_best_trajectory(bundle, lidar_left)
    assert out[-1, 1] < 0.0


def test_memory_locks_direction() -> None:
    bundle = _make_bundle(left_y=0.5, right_y=-0.5, n_paths=6)
    selector = MultiWaypointsSelector(collision_thresh=0.1, memory_decay=2.0, max_clusters=2)
    first = selector.select_best_trajectory(bundle, np.empty((0, 2)))
    selector.last_selected_endpoint = first[-1].copy()
    second = selector.select_best_trajectory(bundle, np.empty((0, 2)))
    np.testing.assert_allclose(second[-1], first[-1], atol=0.15)


def test_fallback_when_all_collide() -> None:
    xs = np.linspace(0.5, 2.0, 6)
    left = np.column_stack([xs, np.full(6, 0.4)])
    right = np.column_stack([xs, np.full(6, -0.4)])
    bundle = np.stack([left, right], axis=0)
    lidar_block = np.array([[1.0, 0.38], [1.5, 0.38], [1.0, -0.38], [1.5, -0.38]])
    selector = MultiWaypointsSelector(collision_thresh=0.25, max_clusters=2)
    out = selector.select_best_trajectory(bundle, lidar_block)
    assert out.shape == (6, 2)


def test_path_to_lidar_returns_path_frame() -> None:
    """Lidar in laser frame; collision via path transform; output stays in base."""
    bundle = _make_bundle(left_y=0.6, right_y=-0.6, n_paths=6)
    tf = _base_to_lidar_tf()
    # (1.5, 0.55)_base -> (-0.55, 1.5)_lidar
    lidar_laser = np.array([[-0.55, 1.5], [-0.5, 1.8]], dtype=np.float64)
    selector = MultiWaypointsSelector(collision_thresh=0.25, max_clusters=2)
    out = selector.select_best_trajectory(bundle, lidar_laser, path_to_lidar=tf)
    assert out[-1, 1] < 0.0
    assert np.max(np.abs(out[:, 0])) > 0.5


def test_path_to_lidar_matches_same_frame_baseline() -> None:
    bundle = _make_bundle(left_y=0.6, right_y=-0.6, n_paths=4)
    tf = _base_to_lidar_tf()
    lidar_base = np.array([[1.5, 0.55], [1.8, 0.5]], dtype=np.float64)
    lidar_laser = MultiWaypointsSelector._transform_points_2d(lidar_base, tf)

    selector_same = MultiWaypointsSelector(collision_thresh=0.25, max_clusters=2)
    selector_xform = MultiWaypointsSelector(collision_thresh=0.25, max_clusters=2)
    out_same = selector_same.select_best_trajectory(bundle, lidar_base)
    out_xform = selector_xform.select_best_trajectory(bundle, lidar_laser, path_to_lidar=tf)
    np.testing.assert_allclose(out_xform, out_same, atol=1e-6)


def test_reset_clears_memory() -> None:
    selector = MultiWaypointsSelector()
    path = np.array([[[0.0, 0.0], [1.0, 0.2]]], dtype=np.float64)
    selector.select_best_trajectory(path, np.empty((0, 2)))
    assert selector.last_selected_endpoint is not None
    selector.reset()
    assert selector.last_selected_endpoint is None
