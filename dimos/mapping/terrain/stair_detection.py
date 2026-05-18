# Copyright 2026 Dimensional Inc.
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

"""Detect single-segment straight stairs from height maps or point clouds.

v0 scope: one straight run, map-frame axis aligned or inferred via PCA on occupied cells.
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING, Any

import numpy as np
from numpy.typing import NDArray

from dimos.mapping.pointclouds.occupancy import _height_map_kernel
from dimos.navigation.stairs.contracts import (
    StairCandidate,
    StairCorridor,
    StairDetectionConfig,
)

if TYPE_CHECKING:
    from dimos.msgs.sensor_msgs.PointCloud2 import PointCloud2


def build_height_map_from_points(
    points: NDArray[np.floating[Any]],
    resolution: float,
    can_pass_under: float = 0.6,
) -> tuple[NDArray[np.float64], float, float]:
    """Build effective height map (gy, gx) from Nx3 points. Returns (height_map, min_x, min_y)."""
    if len(points) == 0:
        return np.zeros((1, 1), dtype=np.float64), 0.0, 0.0

    min_x = float(np.min(points[:, 0])) - 1.0
    max_x = float(np.max(points[:, 0])) + 1.0
    min_y = float(np.min(points[:, 1])) - 1.0
    max_y = float(np.max(points[:, 1])) + 1.0

    width = int(np.ceil((max_x - min_x) / resolution))
    height = int(np.ceil((max_y - min_y) / resolution))

    min_h = np.full((height, width), np.nan, dtype=np.float32)
    max_h = np.full((height, width), np.nan, dtype=np.float32)

    _height_map_kernel(
        points.astype(np.float64),
        min_h,
        max_h,
        min_x,
        min_y,
        1.0 / resolution,
        width,
        height,
    )

    gap = max_h - min_h
    effective = np.where(gap > can_pass_under, min_h, max_h)
    return effective.astype(np.float64), min_x, min_y


def _profile_along_axis(
    height_map: NDArray[np.float64],
    origin_x: float,
    origin_y: float,
    resolution: float,
    axis_yaw: float,
) -> tuple[NDArray[np.float64], NDArray[np.float64]]:
    """Sample mean height along axis through grid center."""
    gy, gx = height_map.shape
    cy, cx = gy // 2, gx // 2
    center_x = origin_x + cx * resolution
    center_y = origin_y + cy * resolution

    cos_a = math.cos(axis_yaw)
    sin_a = math.sin(axis_yaw)
    length_m = max(gx, gy) * resolution
    n = max(gx, gy)
    distances = np.linspace(-length_m / 2, length_m / 2, n)
    heights: list[float] = []

    for d in distances:
        wx = center_x + d * cos_a
        wy = center_y + d * sin_a
        ix = int((wx - origin_x) / resolution + 0.5)
        iy = int((wy - origin_y) / resolution + 0.5)
        if 0 <= ix < gx and 0 <= iy < gy:
            val = height_map[iy, ix]
            heights.append(float(val) if not np.isnan(val) else np.nan)
        else:
            heights.append(np.nan)

    return distances, np.array(heights, dtype=np.float64)


def _find_periodic_steps(
    distances: NDArray[np.float64],
    heights: NDArray[np.float64],
    config: StairDetectionConfig,
) -> tuple[list[float], list[float], float, float] | None:
    """Find riser jumps in 1D height profile."""
    valid = ~np.isnan(heights)
    if np.sum(valid) < config.min_steps * 2:
        return None

    d_valid = distances[valid]
    h_valid = heights[valid]
    diffs = np.diff(h_valid)
    d_diffs = (d_valid[1:] + d_valid[:-1]) / 2.0

    riser_mask = (diffs >= config.min_riser * 0.85) & (diffs <= config.max_riser * 1.15)
    if np.sum(riser_mask) < config.min_steps - 1:
        return None

    riser_heights = diffs[riser_mask].tolist()
    riser_positions = d_diffs[riser_mask].tolist()

    mean_riser = float(np.mean(riser_heights))
    if not (config.min_riser <= mean_riser <= config.max_riser):
        return None

    # estimate tread from median spacing along axis between risers
    if len(riser_positions) >= 2:
        spacings = np.diff(sorted(riser_positions))
        mean_tread = float(np.median(spacings))
    else:
        mean_tread = config.min_tread

    if mean_tread < config.min_tread * 0.7:
        return None

    return riser_heights, riser_positions, mean_riser, mean_tread


def _infer_axis_yaw(height_map: NDArray[np.float64]) -> float:
    occupied = np.argwhere(~np.isnan(height_map))
    if len(occupied) < 10:
        return 0.0
    coords = occupied.astype(np.float64)
    coords -= coords.mean(axis=0)
    cov = coords.T @ coords / len(coords)
    eigvals, eigvecs = np.linalg.eigh(cov)
    principal = eigvecs[:, int(np.argmax(eigvals))]
    # grid index y,x -> map x,y
    return math.atan2(principal[0], principal[1])


def detect_stairs_from_height_map(
    height_map: NDArray[np.float64],
    origin_x: float,
    origin_y: float,
    config: StairDetectionConfig | None = None,
) -> list[StairCandidate]:
    """Detect straight stair candidates from a 2D height field."""
    cfg = config or StairDetectionConfig()
    if height_map.size == 0 or np.all(np.isnan(height_map)):
        return []

    axis_yaw = _infer_axis_yaw(height_map)
    distances, heights = _profile_along_axis(
        height_map, origin_x, origin_y, cfg.resolution, axis_yaw
    )
    periodic = _find_periodic_steps(distances, heights, cfg)
    if periodic is None:
        return []

    _risers, riser_positions, mean_riser, mean_tread = periodic
    cos_a = math.cos(axis_yaw)
    sin_a = math.sin(axis_yaw)

    gy, gx = height_map.shape
    cy, cx = gy // 2, gx // 2
    center_x = origin_x + cx * cfg.resolution
    center_y = origin_y + cy * cfg.resolution

    d_min = min(riser_positions) - mean_tread
    d_max = max(riser_positions) + mean_tread

    centerline: list[tuple[float, float]] = []
    n_pts = max(3, int((d_max - d_min) / (cfg.resolution * 2)))
    for i in range(n_pts + 1):
        t = i / n_pts
        d = d_min + t * (d_max - d_min)
        centerline.append((center_x + d * cos_a, center_y + d * sin_a))

    half_w = 0.5
    polygon = [
        (center_x + d_min * cos_a - half_w * sin_a, center_y + d_min * sin_a + half_w * cos_a),
        (center_x + d_max * cos_a - half_w * sin_a, center_y + d_max * sin_a + half_w * cos_a),
        (center_x + d_max * cos_a + half_w * sin_a, center_y + d_max * sin_a - half_w * cos_a),
        (center_x + d_min * cos_a + half_w * sin_a, center_y + d_min * sin_a - half_w * cos_a),
    ]

    confidence = min(1.0, len(riser_positions) / (cfg.min_steps + 1))

    return [
        StairCandidate(
            axis_yaw=axis_yaw,
            ascending_direction=(cos_a, sin_a),
            centerline=centerline,
            polygon=polygon,
            mean_riser=mean_riser,
            mean_tread=mean_tread,
            confidence=confidence,
        )
    ]


def detect_stairs_from_pointcloud(
    cloud: PointCloud2,
    config: StairDetectionConfig | None = None,
) -> list[StairCandidate]:
    cfg = config or StairDetectionConfig()
    points, _ = cloud.as_numpy()
    if len(points) == 0:
        return []
    height_map, min_x, min_y = build_height_map_from_points(
        points.astype(np.float64),
        cfg.resolution,
        cfg.can_pass_under,
    )
    return detect_stairs_from_height_map(height_map, min_x, min_y, cfg)


def candidates_to_corridors(
    candidates: list[StairCandidate],
    safe_half_width: float = 0.25,
    ascending: bool = True,
) -> list[StairCorridor]:
    corridors: list[StairCorridor] = []
    for c in candidates:
        riser_lines: list[tuple[tuple[float, float], tuple[float, float]]] = []
        cos_a, sin_a = c.ascending_direction
        perp = (-sin_a, cos_a)
        for i, pt in enumerate(c.centerline[1:], start=1):
            if i > len(c.centerline) - 1:
                break
            prev = c.centerline[i - 1]
            mid = ((pt[0] + prev[0]) / 2, (pt[1] + prev[1]) / 2)
            half = safe_half_width * 1.5
            riser_lines.append(
                (
                    (mid[0] - perp[0] * half, mid[1] - perp[1] * half),
                    (mid[0] + perp[0] * half, mid[1] + perp[1] * half),
                )
            )

        corridors.append(
            StairCorridor(
                polygon=c.polygon,
                centerline=c.centerline,
                safe_half_width=safe_half_width,
                ascending=ascending,
                axis_yaw=c.axis_yaw,
                mean_riser=c.mean_riser,
                mean_tread=c.mean_tread,
                riser_lines=riser_lines,
            )
        )
    return corridors
