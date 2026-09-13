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

import numpy as np

from dimos.eval.codegen_loop.metrics import align, ate, first_divergence, hit_rate, rpe


def _line_trajectory(timestamp_offset: float = 0.0, *, reverse: bool = False) -> np.ndarray:
    xy = np.column_stack((np.arange(5, dtype=np.float64), np.zeros(5)))
    if reverse:
        xy = xy[::-1]
    return np.column_stack(
        (
            timestamp_offset + np.arange(5, dtype=np.float64),
            xy,
            np.zeros(5),
        )
    )


def test_shifted_timestamps_align_identical_trajectories() -> None:
    gt = _line_trajectory(1_000.0)
    sim = _line_trajectory(9_000.0)

    aligned_gt, aligned_sim, kept = align(gt, sim)

    assert len(aligned_gt) == len(aligned_sim) == 5
    assert kept.tolist() == [True] * 5
    assert ate(gt, sim) == 0.0
    assert rpe(gt, sim) == 0.0
    assert first_divergence(gt, sim) is None


def test_rpe_uses_nearest_delta_sample_on_irregular_timestamps() -> None:
    timestamps = np.array([0.0, 0.9, 1.3])
    trajectory = np.column_stack((timestamps, timestamps, np.zeros(3), np.zeros(3)))

    assert rpe(trajectory, trajectory, delta_s=1.0, tolerance_s=0.2) == 0.0


def test_empty_sim_trajectory_returns_explicit_failure_metrics() -> None:
    gt = _line_trajectory()
    empty = np.empty((0, 4), dtype=np.float64)

    aligned_gt, aligned_sim, kept = align(gt, empty)

    assert aligned_gt.shape == aligned_sim.shape == (0, 4)
    assert kept.tolist() == [False] * len(gt)
    assert np.isinf(ate(gt, empty))
    assert np.isinf(rpe(gt, empty))
    assert hit_rate(gt, empty, k=5) == 0.0
    assert first_divergence(gt, empty) is None


def test_hit_rate_requires_waypoints_in_route_order() -> None:
    gt = _line_trajectory()

    assert hit_rate(gt, _line_trajectory(), k=5, tolerance_m=0.01) == 1.0
    assert hit_rate(gt, _line_trajectory(reverse=True), k=5, tolerance_m=0.01) == 0.2


def test_hit_rate_samples_waypoints_by_path_length_not_row_index() -> None:
    gt = np.column_stack(
        (
            np.arange(5, dtype=np.float64),
            np.array([0.0, 0.1, 0.2, 0.3, 10.0]),
            np.zeros(5),
            np.zeros(5),
        )
    )
    sim = np.column_stack(
        (
            np.arange(3, dtype=np.float64),
            np.array([0.0, 5.0, 10.0]),
            np.zeros(3),
            np.zeros(3),
        )
    )

    assert hit_rate(gt, sim, k=3, tolerance_m=0.01) == 1.0
