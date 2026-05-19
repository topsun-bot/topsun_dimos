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

Samples multiple height profiles (not only the map center) so stairs on a large
flat floor — e.g. MuJoCo ``scene_stairs`` — still trigger detection.
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

PeriodicSteps = tuple[list[float], list[float], float, float]
ProfileHit = tuple[float, float, float, PeriodicSteps]


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


def _profile_along_axis_at(
    height_map: NDArray[np.float64],
    origin_x: float,
    origin_y: float,
    resolution: float,
    axis_yaw: float,
    center_x: float,
    center_y: float,
) -> tuple[NDArray[np.float64], NDArray[np.float64]]:
    """Sample height along ``axis_yaw`` through ``(center_x, center_y)``."""
    gy, gx = height_map.shape
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
) -> PeriodicSteps | None:
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

    if len(riser_positions) >= 2:
        spacings = np.diff(sorted(riser_positions))
        mean_tread = float(np.median(spacings))
    else:
        mean_tread = config.min_tread

    if mean_tread < config.min_tread * 0.7:
        return None

    return riser_heights, riser_positions, mean_riser, mean_tread


def _grid_center_world(
    height_map: NDArray[np.float64],
    origin_x: float,
    origin_y: float,
    resolution: float,
) -> tuple[float, float]:
    gy, gx = height_map.shape
    return origin_x + (gx / 2.0) * resolution, origin_y + (gy / 2.0) * resolution


def _occupied_centroid_world(
    height_map: NDArray[np.float64],
    origin_x: float,
    origin_y: float,
    resolution: float,
) -> tuple[float, float] | None:
    occupied = np.argwhere(~np.isnan(height_map))
    if len(occupied) < 10:
        return None
    cy_mean = float(np.mean(occupied[:, 0]))
    cx_mean = float(np.mean(occupied[:, 1]))
    return origin_x + cx_mean * resolution, origin_y + cy_mean * resolution


def _step_gradient_centroid_world(
    height_map: NDArray[np.float64],
    origin_x: float,
    origin_y: float,
    resolution: float,
    min_step_m: float,
) -> tuple[float, float] | None:
    """Centroid of cells with a neighbor height jump typical of a stair riser."""
    gy, gx = height_map.shape
    threshold = min_step_m * 0.55
    iys: list[float] = []
    ixs: list[float] = []
    weights: list[float] = []

    for iy in range(1, gy - 1):
        for ix in range(1, gx - 1):
            center = height_map[iy, ix]
            if np.isnan(center):
                continue
            max_jump = 0.0
            for dy, dx in ((-1, 0), (1, 0), (0, -1), (0, 1)):
                neighbor = height_map[iy + dy, ix + dx]
                if np.isnan(neighbor):
                    continue
                max_jump = max(max_jump, abs(center - neighbor))
            if max_jump >= threshold:
                iys.append(float(iy))
                ixs.append(float(ix))
                weights.append(max_jump)

    if len(weights) < 5:
        return None

    cy = float(np.average(iys, weights=weights))
    cx = float(np.average(ixs, weights=weights))
    return origin_x + cx * resolution, origin_y + cy * resolution


def _infer_axis_yaw(height_map: NDArray[np.float64], *, step_biased: bool = True) -> float:
    """PCA on occupied cells; optionally weight cells near stair-like height jumps."""
    gy, gx = height_map.shape
    coords_list: list[tuple[float, float, float]] = []

    for iy in range(gy):
        for ix in range(gx):
            if np.isnan(height_map[iy, ix]):
                continue
            weight = 1.0
            if step_biased and 0 < iy < gy - 1 and 0 < ix < gx - 1:
                center = height_map[iy, ix]
                max_jump = 0.0
                for dy, dx in ((-1, 0), (1, 0), (0, -1), (0, 1)):
                    neighbor = height_map[iy + dy, ix + dx]
                    if not np.isnan(neighbor):
                        max_jump = max(max_jump, abs(center - neighbor))
                if max_jump >= 0.08:
                    weight = 1.0 + max_jump * 8.0
            coords_list.append((float(iy), float(ix), weight))

    if len(coords_list) < 10:
        return 0.0

    coords = np.array([(c[0], c[1]) for c in coords_list], dtype=np.float64)
    weights = np.array([c[2] for c in coords_list], dtype=np.float64)
    centroid = np.average(coords, axis=0, weights=weights)
    centered = coords - centroid
    cov = (centered * weights[:, None]).T @ centered / max(np.sum(weights), 1e-9)
    eigvals, eigvecs = np.linalg.eigh(cov)
    principal = eigvecs[:, int(np.argmax(eigvals))]
    return math.atan2(principal[0], principal[1])


def _search_best_profile(
    height_map: NDArray[np.float64],
    origin_x: float,
    origin_y: float,
    config: StairDetectionConfig,
) -> ProfileHit | None:
    """Try multiple axes and profile anchors; return the strongest periodic-stair match."""
    resolution = config.resolution
    primary_yaw = _infer_axis_yaw(height_map, step_biased=True)
    axis_candidates = [primary_yaw, primary_yaw + math.pi / 2.0]

    anchor_candidates: list[tuple[float, float]] = []
    grid_center = _grid_center_world(height_map, origin_x, origin_y, resolution)
    anchor_candidates.append(grid_center)

    occupied = _occupied_centroid_world(height_map, origin_x, origin_y, resolution)
    if occupied is not None:
        anchor_candidates.append(occupied)

    step_center = _step_gradient_centroid_world(
        height_map, origin_x, origin_y, resolution, config.min_riser
    )
    if step_center is not None:
        anchor_candidates.append(step_center)

    # Deduplicate anchors (~10 cm)
    unique_anchors: list[tuple[float, float]] = []
    for ax, ay in anchor_candidates:
        if all(math.hypot(ax - ux, ay - uy) > 0.1 for ux, uy in unique_anchors):
            unique_anchors.append((ax, ay))

    perp_offsets_m = np.linspace(-2.5, 2.5, 11)
    best: ProfileHit | None = None
    best_riser_count = 0

    for axis_yaw in axis_candidates:
        cos_a = math.cos(axis_yaw)
        sin_a = math.sin(axis_yaw)
        perp = (-sin_a, cos_a)

        for center_x, center_y in unique_anchors:
            for off in perp_offsets_m:
                wx = center_x + off * perp[0]
                wy = center_y + off * perp[1]
                distances, heights = _profile_along_axis_at(
                    height_map,
                    origin_x,
                    origin_y,
                    resolution,
                    axis_yaw,
                    wx,
                    wy,
                )
                periodic = _find_periodic_steps(distances, heights, config)
                if periodic is None:
                    continue
                _risers, riser_positions, _mean_riser, _mean_tread = periodic
                count = len(riser_positions)
                if count > best_riser_count:
                    best_riser_count = count
                    best = (axis_yaw, wx, wy, periodic)

    return best


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

    hit = _search_best_profile(height_map, origin_x, origin_y, cfg)
    if hit is None:
        return []

    axis_yaw, anchor_x, anchor_y, periodic = hit
    _risers, riser_positions, mean_riser, mean_tread = periodic
    cos_a = math.cos(axis_yaw)
    sin_a = math.sin(axis_yaw)

    d_min = min(riser_positions) - mean_tread
    d_max = max(riser_positions) + mean_tread

    centerline: list[tuple[float, float]] = []
    n_pts = max(3, int((d_max - d_min) / (cfg.resolution * 2)))
    for i in range(n_pts + 1):
        t = i / n_pts
        d = d_min + t * (d_max - d_min)
        centerline.append((anchor_x + d * cos_a, anchor_y + d * sin_a))

    half_w = 0.5
    polygon = [
        (
            anchor_x + d_min * cos_a - half_w * sin_a,
            anchor_y + d_min * sin_a + half_w * cos_a,
        ),
        (
            anchor_x + d_max * cos_a - half_w * sin_a,
            anchor_y + d_max * sin_a + half_w * cos_a,
        ),
        (
            anchor_x + d_max * cos_a + half_w * sin_a,
            anchor_y + d_max * sin_a - half_w * cos_a,
        ),
        (
            anchor_x + d_min * cos_a + half_w * sin_a,
            anchor_y + d_min * sin_a - half_w * cos_a,
        ),
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
