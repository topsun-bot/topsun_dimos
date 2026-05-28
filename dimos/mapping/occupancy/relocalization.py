from __future__ import annotations

from dataclasses import dataclass
import math

import numpy as np

from dimos.msgs.nav_msgs.OccupancyGrid import CostValues, OccupancyGrid


@dataclass(frozen=True)
class OccupancyMatchResult:
    success: bool
    x: float
    y: float
    yaw: float
    score: float
    confidence: float
    compared_cells: int
    occupied_compared_cells: int


def match_occupancy_translation(
    persistent_map: OccupancyGrid,
    local_map: OccupancyGrid,
    *,
    search_radius_m: float = 2.0,
    stride_m: float | None = None,
    min_compared_cells: int = 20,
    min_occupied_cells: int = 1,
    min_confidence: float = 0.55,
) -> OccupancyMatchResult:
    """Estimate where a local occupancy patch fits in a persistent map."""
    return match_occupancy_se2(
        persistent_map,
        local_map,
        search_radius_m=search_radius_m,
        stride_m=stride_m,
        yaw_candidates_deg=(0.0,),
        min_compared_cells=min_compared_cells,
        min_occupied_cells=min_occupied_cells,
        min_confidence=min_confidence,
    )


def match_occupancy_se2(
    persistent_map: OccupancyGrid,
    local_map: OccupancyGrid,
    *,
    search_radius_m: float = 2.0,
    stride_m: float | None = None,
    yaw_candidates_deg: tuple[float, ...] = (0.0, -90.0, 90.0, 180.0),
    min_compared_cells: int = 20,
    min_occupied_cells: int = 1,
    min_confidence: float = 0.55,
) -> OccupancyMatchResult:
    """Estimate where a local occupancy patch fits in a persistent map.

    The first implementation supports right-angle yaw candidates. That keeps the
    transform deterministic for grid cells while still handling common startup
    heading ambiguity. Arbitrary yaw can be added later with image resampling.
    """
    _validate_match_inputs(persistent_map, local_map)

    resolution = persistent_map.resolution
    stride_cells = max(1, round((stride_m or resolution) / resolution))
    radius_cells = max(0, round(search_radius_m / resolution))

    initial_dx_cells = round(
        (local_map.origin.position.x - persistent_map.origin.position.x) / resolution
    )
    initial_dy_cells = round(
        (local_map.origin.position.y - persistent_map.origin.position.y) / resolution
    )

    best_score = -math.inf
    best_compared = 0
    best_occupied_compared = 0
    best_dx_cells = initial_dx_cells
    best_dy_cells = initial_dy_cells
    best_yaw = 0.0

    for yaw_deg in yaw_candidates_deg:
        candidate_grid = _rotate_grid_right_angle(local_map.grid, yaw_deg)
        for dy_cells in range(
            initial_dy_cells - radius_cells,
            initial_dy_cells + radius_cells + 1,
            stride_cells,
        ):
            for dx_cells in range(
                initial_dx_cells - radius_cells,
                initial_dx_cells + radius_cells + 1,
                stride_cells,
            ):
                score, compared, occupied_compared = _score_translation(
                    persistent_map.grid,
                    candidate_grid,
                    dx_cells,
                    dy_cells,
                )
                if compared < min_compared_cells or occupied_compared < min_occupied_cells:
                    continue
                if (score, occupied_compared, compared) > (
                    best_score,
                    best_occupied_compared,
                    best_compared,
                ):
                    best_score = score
                    best_compared = compared
                    best_occupied_compared = occupied_compared
                    best_dx_cells = dx_cells
                    best_dy_cells = dy_cells
                    best_yaw = math.radians(yaw_deg)

    if best_score == -math.inf:
        return OccupancyMatchResult(
            success=False,
            x=0.0,
            y=0.0,
            yaw=0.0,
            score=0.0,
            confidence=0.0,
            compared_cells=0,
            occupied_compared_cells=0,
        )

    confidence = max(0.0, min(1.0, best_score))
    x = persistent_map.origin.position.x + best_dx_cells * resolution
    y = persistent_map.origin.position.y + best_dy_cells * resolution
    return OccupancyMatchResult(
        success=confidence >= min_confidence,
        x=x,
        y=y,
        yaw=best_yaw,
        score=best_score,
        confidence=confidence,
        compared_cells=best_compared,
        occupied_compared_cells=best_occupied_compared,
    )


def _validate_match_inputs(persistent_map: OccupancyGrid, local_map: OccupancyGrid) -> None:
    if persistent_map.grid.ndim != 2 or local_map.grid.ndim != 2:
        raise ValueError("Occupancy maps must be 2D")
    if persistent_map.grid.size == 0 or local_map.grid.size == 0:
        raise ValueError("Occupancy maps must not be empty")
    if not math.isclose(persistent_map.resolution, local_map.resolution, rel_tol=1e-6):
        raise ValueError(
            "Occupancy map resolutions must match: "
            f"{persistent_map.resolution} vs {local_map.resolution}"
        )


def _score_translation(
    persistent_grid: np.ndarray,
    local_grid: np.ndarray,
    dx_cells: int,
    dy_cells: int,
) -> tuple[float, int, int]:
    persistent_slice, local_slice = _overlap_slices(
        persistent_grid.shape,
        local_grid.shape,
        dx_cells,
        dy_cells,
    )
    if persistent_slice is None or local_slice is None:
        return 0.0, 0, 0

    persistent_patch = persistent_grid[persistent_slice]
    local_patch = local_grid[local_slice]

    known = (persistent_patch != CostValues.UNKNOWN) & (local_patch != CostValues.UNKNOWN)
    compared = int(np.count_nonzero(known))
    if compared == 0:
        return 0.0, 0, 0

    persistent_occupied = persistent_patch >= CostValues.OCCUPIED
    local_occupied = local_patch >= CostValues.OCCUPIED
    occupied_compared = int(np.count_nonzero(known & (persistent_occupied | local_occupied)))
    occupied_match = persistent_occupied == local_occupied

    return float(np.count_nonzero(known & occupied_match) / compared), compared, occupied_compared


def _rotate_grid_right_angle(grid: np.ndarray, yaw_deg: float) -> np.ndarray:
    normalized = yaw_deg % 360.0
    nearest_quarter_turn = round(normalized / 90.0)
    if not math.isclose(normalized, nearest_quarter_turn * 90.0, abs_tol=1e-6):
        raise ValueError("Only right-angle yaw candidates are supported")
    return np.rot90(grid, k=nearest_quarter_turn)


def _overlap_slices(
    persistent_shape: tuple[int, int],
    local_shape: tuple[int, int],
    dx_cells: int,
    dy_cells: int,
) -> tuple[tuple[slice, slice] | None, tuple[slice, slice] | None]:
    persistent_h, persistent_w = persistent_shape
    local_h, local_w = local_shape

    persistent_x0 = max(0, dx_cells)
    persistent_y0 = max(0, dy_cells)
    persistent_x1 = min(persistent_w, dx_cells + local_w)
    persistent_y1 = min(persistent_h, dy_cells + local_h)

    if persistent_x0 >= persistent_x1 or persistent_y0 >= persistent_y1:
        return None, None

    local_x0 = max(0, -dx_cells)
    local_y0 = max(0, -dy_cells)
    local_x1 = local_x0 + (persistent_x1 - persistent_x0)
    local_y1 = local_y0 + (persistent_y1 - persistent_y0)

    return (
        (slice(persistent_y0, persistent_y1), slice(persistent_x0, persistent_x1)),
        (slice(local_y0, local_y1), slice(local_x0, local_x1)),
    )
