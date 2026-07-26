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

from dataclasses import dataclass
from typing import Literal, TypeAlias

import numpy as np
from numpy.typing import NDArray

EvidenceLevel: TypeAlias = Literal["OBSERVED", "CORRELATED", "INFERRED", "UNKNOWN"]


@dataclass(frozen=True, slots=True)
class ProjectionResult:
    """Continuous closest-point projection of samples onto a polyline."""

    signed_cross_track_m: NDArray[np.float64]
    distance_m: NDArray[np.float64]
    progress_m: NDArray[np.float64]
    segment_index: NDArray[np.int64]
    projected_xy: NDArray[np.float64]


@dataclass(frozen=True, slots=True)
class SnakeCandidate:
    """One interval with repeated lateral and angular oscillation."""

    start_ts: float
    end_ts: float
    amplitude_m: float
    period_sec: float | None
    cross_track_zero_crossings: int
    angular_flips: int
    cte_command_correlation: float | None
    evidence: EvidenceLevel = "CORRELATED"


@dataclass(frozen=True, slots=True)
class ResponseLagResult:
    """Closed-loop command-to-odom-yaw response lag summary."""

    lags_sec: tuple[float, ...]
    median_sec: float | None
    p95_sec: float | None
    evidence: EvidenceLevel


@dataclass(frozen=True, slots=True)
class TrajectoryMetrics:
    """Offline trajectory metrics for one navigation plan/session."""

    cross_track_rms_m: float
    cross_track_p95_m: float
    cross_track_max_m: float
    planned_path_length_m: float
    odom_estimated_path_length_m: float
    overshoot_m: float
    angular_flip_count: int
    snake_candidates: tuple[SnakeCandidate, ...]
    evidence: EvidenceLevel = "OBSERVED"


def project_to_polyline(
    samples_xy: NDArray[np.floating],
    path_xy: NDArray[np.floating],
) -> ProjectionResult:
    """Project each 2D sample onto the closest continuous path segment.

    Positive signed cross-track error means the sample lies to the left of
    the selected segment direction; negative means right.
    """
    samples = _as_xy(samples_xy, name="samples_xy", allow_empty=True)
    path = _as_xy(path_xy, name="path_xy", allow_empty=False)
    if len(path) < 2:
        raise ValueError("path_xy must contain at least two points")

    segment_start = path[:-1]
    segment_vectors = path[1:] - path[:-1]
    segment_length_sq = np.einsum("ij,ij->i", segment_vectors, segment_vectors)
    segment_lengths = np.sqrt(segment_length_sq)
    cumulative = np.concatenate((np.array([0.0], dtype=np.float64), np.cumsum(segment_lengths)))

    distances = np.empty(len(samples), dtype=np.float64)
    signed = np.empty(len(samples), dtype=np.float64)
    progress = np.empty(len(samples), dtype=np.float64)
    indices = np.empty(len(samples), dtype=np.int64)
    projected = np.empty((len(samples), 2), dtype=np.float64)

    valid = segment_length_sq > 0.0
    if not bool(np.any(valid)):
        raise ValueError("path_xy must contain at least one non-degenerate segment")

    for sample_index, sample in enumerate(samples):
        relative = sample - segment_start
        t = np.zeros(len(segment_vectors), dtype=np.float64)
        t[valid] = (
            np.einsum("ij,ij->i", relative[valid], segment_vectors[valid])
            / segment_length_sq[valid]
        )
        np.clip(t, 0.0, 1.0, out=t)
        candidates = segment_start + t[:, None] * segment_vectors
        deltas = sample - candidates
        squared = np.einsum("ij,ij->i", deltas, deltas)
        squared[~valid] = np.inf
        segment_index = int(np.argmin(squared))
        delta = deltas[segment_index]
        cross_z = (
            segment_vectors[segment_index, 0] * delta[1]
            - segment_vectors[segment_index, 1] * delta[0]
        )
        signed_distance = float(cross_z / segment_lengths[segment_index])
        distance = abs(signed_distance)
        line_t = float(
            np.dot(sample - segment_start[segment_index], segment_vectors[segment_index])
            / segment_length_sq[segment_index]
        )

        distances[sample_index] = distance
        signed[sample_index] = signed_distance
        progress[sample_index] = (
            cumulative[segment_index] + t[segment_index] * segment_lengths[segment_index]
        )
        indices[sample_index] = segment_index
        projected[sample_index] = (
            segment_start[segment_index] + line_t * segment_vectors[segment_index]
        )

    return ProjectionResult(
        signed_cross_track_m=signed,
        distance_m=distances,
        progress_m=progress,
        segment_index=indices,
        projected_xy=projected,
    )


def polyline_length(points_xy: NDArray[np.floating]) -> float:
    """Return total Euclidean length of a 2D polyline."""
    points = _as_xy(points_xy, name="points_xy", allow_empty=True)
    if len(points) < 2:
        return 0.0
    return float(np.sum(np.linalg.norm(np.diff(points, axis=0), axis=1)))


def calculate_trajectory_metrics(
    path_xy: NDArray[np.floating],
    odom_xy: NDArray[np.floating],
    *,
    timestamps: NDArray[np.floating] | None = None,
    angular_z: NDArray[np.floating] | None = None,
) -> TrajectoryMetrics:
    """Calculate path-following metrics from odom estimates, not ground truth."""
    path = _as_xy(path_xy, name="path_xy", allow_empty=False)
    odom = _as_xy(odom_xy, name="odom_xy", allow_empty=True)
    projection = project_to_polyline(odom, path)
    absolute_cte = np.abs(projection.signed_cross_track_m)
    if len(absolute_cte) == 0:
        rms = p95 = maximum = 0.0
    else:
        rms = float(np.sqrt(np.mean(np.square(absolute_cte))))
        p95 = float(np.percentile(absolute_cte, 95))
        maximum = float(np.max(absolute_cte))

    angular_values = (
        np.asarray(angular_z, dtype=np.float64)
        if angular_z is not None
        else np.array([], dtype=np.float64)
    )
    flip_count = count_sign_flips(angular_values)
    snake_candidates: tuple[SnakeCandidate, ...] = ()
    if timestamps is not None and angular_z is not None:
        snake_candidates = detect_snake_candidates(
            np.asarray(timestamps, dtype=np.float64),
            projection.signed_cross_track_m,
            angular_values,
        )

    return TrajectoryMetrics(
        cross_track_rms_m=rms,
        cross_track_p95_m=p95,
        cross_track_max_m=maximum,
        planned_path_length_m=polyline_length(path),
        odom_estimated_path_length_m=polyline_length(odom),
        overshoot_m=calculate_overshoot(path, odom),
        angular_flip_count=flip_count,
        snake_candidates=snake_candidates,
    )


def calculate_overshoot(
    path_xy: NDArray[np.floating],
    odom_xy: NDArray[np.floating],
) -> float:
    """Measure maximum travel beyond the goal along the final path tangent."""
    path = _as_xy(path_xy, name="path_xy", allow_empty=False)
    odom = _as_xy(odom_xy, name="odom_xy", allow_empty=True)
    if len(path) < 2 or len(odom) == 0:
        return 0.0
    final_vector = path[-1] - path[-2]
    length = float(np.linalg.norm(final_vector))
    if length == 0.0:
        return 0.0
    tangent = final_vector / length
    beyond = (odom - path[-1]) @ tangent
    return max(0.0, float(np.max(beyond)))


def count_sign_flips(values: NDArray[np.floating], *, deadband: float = 0.05) -> int:
    """Count sign changes while ignoring values inside a deadband."""
    array = np.asarray(values, dtype=np.float64)
    significant = array[np.abs(array) > deadband]
    if len(significant) < 2:
        return 0
    return int(np.count_nonzero(np.sign(significant[1:]) != np.sign(significant[:-1])))


def detect_snake_candidates(
    timestamps: NDArray[np.floating],
    signed_cross_track_m: NDArray[np.floating],
    angular_z: NDArray[np.floating],
    *,
    minimum_amplitude_m: float = 0.05,
    minimum_zero_crossings: int = 3,
    minimum_angular_flips: int = 3,
) -> tuple[SnakeCandidate, ...]:
    """Detect session intervals that look like repeated left-right weaving."""
    ts = np.asarray(timestamps, dtype=np.float64)
    cte = np.asarray(signed_cross_track_m, dtype=np.float64)
    angular = np.asarray(angular_z, dtype=np.float64)
    if not (len(ts) == len(cte) == len(angular)):
        raise ValueError("timestamps, cross-track error, and angular_z must have equal length")
    if len(ts) < 4 or np.any(np.diff(ts) < 0):
        return ()

    amplitude = float(np.percentile(np.abs(cte), 95))
    cte_crossings = _sign_change_indices(cte, deadband=minimum_amplitude_m / 4)
    angular_flips = _sign_change_indices(angular, deadband=0.05)
    if (
        amplitude < minimum_amplitude_m
        or len(cte_crossings) < minimum_zero_crossings
        or len(angular_flips) < minimum_angular_flips
    ):
        return ()

    period: float | None = None
    if len(cte_crossings) >= 3:
        full_cycle_durations = ts[cte_crossings[2:]] - ts[cte_crossings[:-2]]
        if len(full_cycle_durations) > 0:
            period = float(np.median(full_cycle_durations))

    correlation: float | None = None
    if np.std(cte) > 0.0 and np.std(angular) > 0.0:
        correlation = float(np.corrcoef(cte, angular)[0, 1])

    return (
        SnakeCandidate(
            start_ts=float(ts[cte_crossings[0]]),
            end_ts=float(ts[cte_crossings[-1]]),
            amplitude_m=amplitude,
            period_sec=period,
            cross_track_zero_crossings=len(cte_crossings),
            angular_flips=len(angular_flips),
            cte_command_correlation=correlation,
        ),
    )


def estimate_closed_loop_response_lag(
    command_ts: NDArray[np.floating],
    command_angular_z: NDArray[np.floating],
    odom_ts: NDArray[np.floating],
    odom_yaw_rate: NDArray[np.floating],
    *,
    deadband: float = 0.05,
    maximum_lag_sec: float = 2.0,
) -> ResponseLagResult:
    """Correlate command direction flips with later odom yaw-rate flips."""
    cmd_ts = np.asarray(command_ts, dtype=np.float64)
    commands = np.asarray(command_angular_z, dtype=np.float64)
    measured_ts = np.asarray(odom_ts, dtype=np.float64)
    measured = np.asarray(odom_yaw_rate, dtype=np.float64)
    if len(cmd_ts) != len(commands) or len(measured_ts) != len(measured):
        raise ValueError("timestamp and value arrays must have equal lengths")

    flip_indices = _sign_change_indices(commands, deadband=deadband)
    lags: list[float] = []
    for flip_index in flip_indices:
        target_sign = np.sign(commands[flip_index])
        after = np.flatnonzero(
            (measured_ts >= cmd_ts[flip_index])
            & (measured_ts <= cmd_ts[flip_index] + maximum_lag_sec)
            & (np.abs(measured) > deadband)
            & (np.sign(measured) == target_sign)
        )
        if len(after) > 0:
            lags.append(float(measured_ts[int(after[0])] - cmd_ts[flip_index]))

    if not lags:
        return ResponseLagResult((), None, None, "UNKNOWN")
    lag_array = np.asarray(lags, dtype=np.float64)
    return ResponseLagResult(
        lags_sec=tuple(lags),
        median_sec=float(np.median(lag_array)),
        p95_sec=float(np.percentile(lag_array, 95)),
        evidence="CORRELATED",
    )


def _sign_change_indices(values: NDArray[np.float64], *, deadband: float) -> NDArray[np.int64]:
    signs = np.sign(values)
    signs[np.abs(values) <= deadband] = 0.0
    result: list[int] = []
    last_sign = 0.0
    for index, sign in enumerate(signs):
        if sign == 0.0:
            continue
        if last_sign != 0.0 and sign != last_sign:
            result.append(index)
        last_sign = sign
    return np.asarray(result, dtype=np.int64)


def _as_xy(
    value: NDArray[np.floating],
    *,
    name: str,
    allow_empty: bool,
) -> NDArray[np.float64]:
    array = np.asarray(value, dtype=np.float64)
    if array.ndim != 2 or array.shape[1:] != (2,):
        raise ValueError(f"{name} must have shape (N, 2)")
    if not allow_empty and len(array) == 0:
        raise ValueError(f"{name} must not be empty")
    if not bool(np.all(np.isfinite(array))):
        raise ValueError(f"{name} must contain only finite values")
    return array
