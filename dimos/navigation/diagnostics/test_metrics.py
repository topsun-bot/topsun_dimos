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
import pytest

from dimos.navigation.diagnostics.metrics import (
    calculate_trajectory_metrics,
    detect_snake_candidates,
    estimate_closed_loop_response_lag,
    project_to_polyline,
)


def test_continuous_projection_on_straight_line_and_signed_offsets() -> None:
    path = np.array([[0.0, 0.0], [10.0, 0.0]])
    samples = np.array([[1.0, 0.0], [5.0, 1.0], [7.0, -2.0]])

    result = project_to_polyline(samples, path)

    assert result.distance_m == pytest.approx([0.0, 1.0, 2.0])
    assert result.signed_cross_track_m == pytest.approx([0.0, 1.0, -2.0])
    assert result.progress_m == pytest.approx([1.0, 5.0, 7.0])


def test_continuous_projection_beats_discrete_nearest_vertex() -> None:
    path = np.array([[0.0, 0.0], [2.0, 0.0]])
    sample = np.array([[1.0, 0.0]])

    result = project_to_polyline(sample, path)
    discrete_distance = float(np.min(np.linalg.norm(path - sample[0], axis=1)))

    assert result.distance_m[0] == pytest.approx(0.0)
    assert discrete_distance == pytest.approx(1.0)


def test_projection_on_curve_selects_nearest_segment() -> None:
    path = np.array([[0.0, 0.0], [1.0, 0.0], [1.0, 1.0]])
    samples = np.array([[0.5, 0.2], [0.8, 0.7]])

    result = project_to_polyline(samples, path)

    assert result.segment_index.tolist() == [0, 1]
    assert result.distance_m == pytest.approx([0.2, 0.2])
    assert result.signed_cross_track_m == pytest.approx([0.2, 0.2])


def test_trajectory_metrics_cover_sine_error_length_and_overshoot() -> None:
    path = np.array([[0.0, 0.0], [2.0, 0.0]])
    timestamps = np.linspace(0.0, 4.0, 9)
    x = np.linspace(0.0, 2.4, 9)
    y = 0.1 * np.sin(np.linspace(0.0, 4.0 * np.pi, 9))
    odom = np.column_stack((x, y))
    angular = np.array([0.2, 0.2, -0.2, -0.2, 0.2, 0.2, -0.2, -0.2, 0.2])

    metrics = calculate_trajectory_metrics(
        path,
        odom,
        timestamps=timestamps,
        angular_z=angular,
    )

    assert metrics.planned_path_length_m == pytest.approx(2.0)
    assert metrics.odom_estimated_path_length_m > 2.4
    assert metrics.overshoot_m == pytest.approx(0.4)
    assert metrics.cross_track_max_m == pytest.approx(0.1)
    assert metrics.angular_flip_count == 4


def test_snake_candidate_reports_amplitude_period_and_control_correlation() -> None:
    ts = np.arange(0.0, 8.0, 0.25)
    cte = 0.15 * np.sin(2.0 * np.pi * ts / 2.0)
    angular = 0.3 * np.sin(2.0 * np.pi * ts / 2.0)

    (candidate,) = detect_snake_candidates(ts, cte, angular)

    assert candidate.amplitude_m == pytest.approx(0.15, rel=0.05)
    assert candidate.period_sec == pytest.approx(2.0, abs=0.3)
    assert candidate.angular_flips >= 3
    assert candidate.cte_command_correlation == pytest.approx(1.0)
    assert candidate.evidence == "CORRELATED"


def test_normal_curve_is_not_reported_as_snake_candidate() -> None:
    ts = np.linspace(0.0, 5.0, 51)
    angle = np.linspace(0.0, np.pi / 2.0, 51)
    path = np.column_stack((2.0 * np.cos(angle), 2.0 * np.sin(angle)))
    odom = path + np.column_stack((0.01 * np.cos(angle), 0.01 * np.sin(angle)))
    angular = np.full(len(ts), 0.2)

    metrics = calculate_trajectory_metrics(
        path,
        odom,
        timestamps=ts,
        angular_z=angular,
    )

    assert metrics.cross_track_max_m < 0.02
    assert metrics.snake_candidates == ()


def test_closed_loop_response_lag_is_correlated_not_network_latency() -> None:
    command_ts = np.array([0.0, 1.0, 2.0, 3.0])
    command = np.array([0.2, -0.2, 0.2, -0.2])
    odom_ts = np.arange(0.0, 4.0, 0.1)
    yaw_rate = np.where(
        odom_ts < 1.2,
        0.2,
        np.where(odom_ts < 2.2, -0.2, np.where(odom_ts < 3.2, 0.2, -0.2)),
    )

    result = estimate_closed_loop_response_lag(
        command_ts,
        command,
        odom_ts,
        yaw_rate,
    )

    assert result.lags_sec == pytest.approx((0.2, 0.2, 0.2))
    assert result.median_sec == pytest.approx(0.2)
    assert result.evidence == "CORRELATED"
