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
from threading import RLock

import numpy as np
from numpy.typing import NDArray

from dimos.core.global_config import GlobalConfig
from dimos.mapping.occupancy.path_mask import make_path_mask
from dimos.msgs.nav_msgs.OccupancyGrid import CostValues, OccupancyGrid
from dimos.msgs.nav_msgs.Path import Path


@dataclass(frozen=True, slots=True)
class PathClearanceDiagnostic:
    """Read-only details for one obstacle-ahead decision."""

    obstacle_ahead: bool
    reason: str
    costmap_source_ts: float | None
    path_lookup_distance_m: float
    pose_index: int
    mask_reused_for_decision: bool
    mask_source_pose_index: int | None
    path_start_index: int
    path_end_index: int
    effective_lookahead_m: float
    mask_cell_count: int
    free_cell_count: int
    unknown_cell_count: int
    occupied_hit_count: int
    first_occupied_cells: tuple[tuple[int, int], ...]
    nearest_occupied_path_progress_m: float | None
    hits_truncated: bool


class PathClearance:
    _costmap: OccupancyGrid | None = None
    _last_costmap: OccupancyGrid | None = None
    _path_lookup_distance: float = 3.0
    _max_distance_cache: float = 1.0
    _last_used_shape: tuple[int, ...] | None = None
    _last_mask: NDArray[np.bool_] | None = None
    _last_used_pose: int | None = None
    _global_config: GlobalConfig
    _lock: RLock
    _path: Path
    _pose_index: int

    def __init__(self, global_config: GlobalConfig, path: Path) -> None:
        self._global_config = global_config
        self._path = path
        self._pose_index = 0
        self._lock = RLock()
        self._last_used_shape = None
        self._last_mask = None
        self._last_used_pose = None
        self._last_mask_reused = False
        self._decision_mask_reused = False

    def update_costmap(self, costmap: OccupancyGrid) -> None:
        with self._lock:
            self._costmap = costmap

    def update_pose_index(self, index: int) -> None:
        with self._lock:
            self._pose_index = index

    @property
    def current_costmap(self) -> OccupancyGrid | None:
        """Return the current immutable-by-convention map reference."""
        with self._lock:
            return self._costmap

    @property
    def mask(self) -> NDArray[np.bool_]:
        with self._lock:
            costmap = self._costmap
            pose_index = self._pose_index

        assert costmap is not None

        if (
            self._last_mask is not None
            and self._last_used_pose is not None
            and costmap.grid.shape == self._last_used_shape
            and self._pose_distance(self._last_used_pose, pose_index) < self._max_distance_cache
        ):
            self._last_mask_reused = True
            return self._last_mask

        self._last_mask_reused = False
        self._last_mask = make_path_mask(
            occupancy_grid=costmap,
            path=self._path,
            robot_width=self._global_config.robot_width,
            pose_index=pose_index,
            max_length=self._path_lookup_distance,
        )

        self._last_used_shape = costmap.grid.shape
        self._last_used_pose = pose_index

        return self._last_mask

    def is_obstacle_ahead(self) -> bool:
        with self._lock:
            costmap = self._costmap

        if costmap is None:
            return True

        mask = self.mask
        self._decision_mask_reused = self._last_mask_reused
        return bool(np.any(costmap.grid[mask] == CostValues.OCCUPIED))

    def diagnostic(self, *, max_hit_cells: int = 64) -> PathClearanceDiagnostic:
        """Describe the current decision without changing the boolean contract."""
        with self._lock:
            costmap = self._costmap
            pose_index = self._pose_index

        if costmap is None:
            return PathClearanceDiagnostic(
                obstacle_ahead=True,
                reason="costmap_missing",
                costmap_source_ts=None,
                path_lookup_distance_m=self._path_lookup_distance,
                pose_index=pose_index,
                mask_reused_for_decision=False,
                mask_source_pose_index=self._last_used_pose,
                path_start_index=pose_index,
                path_end_index=pose_index,
                effective_lookahead_m=0.0,
                mask_cell_count=0,
                free_cell_count=0,
                unknown_cell_count=0,
                occupied_hit_count=0,
                first_occupied_cells=(),
                nearest_occupied_path_progress_m=None,
                hits_truncated=False,
            )

        mask = self.mask
        masked_values = costmap.grid[mask]
        occupied_hits = np.argwhere(mask & (costmap.grid == CostValues.OCCUPIED))
        path_start, path_end, effective_lookahead = self._path_window(pose_index)
        limit = max(0, max_hit_cells)
        first_cells = tuple((int(cell[0]), int(cell[1])) for cell in occupied_hits[:limit])
        hit_count = int(occupied_hits.shape[0])
        return PathClearanceDiagnostic(
            obstacle_ahead=hit_count > 0,
            reason="occupied_cells_in_forward_path" if hit_count > 0 else "path_clear",
            costmap_source_ts=float(costmap.ts),
            path_lookup_distance_m=self._path_lookup_distance,
            pose_index=pose_index,
            mask_reused_for_decision=self._decision_mask_reused,
            mask_source_pose_index=self._last_used_pose,
            path_start_index=path_start,
            path_end_index=path_end,
            effective_lookahead_m=effective_lookahead,
            mask_cell_count=int(np.count_nonzero(mask)),
            free_cell_count=int(np.count_nonzero(masked_values == CostValues.FREE)),
            unknown_cell_count=int(np.count_nonzero(masked_values == CostValues.UNKNOWN)),
            occupied_hit_count=hit_count,
            first_occupied_cells=first_cells,
            nearest_occupied_path_progress_m=self._nearest_occupied_progress(
                costmap,
                occupied_hits,
                path_start,
                path_end,
            ),
            hits_truncated=hit_count > len(first_cells),
        )

    def _pose_distance(self, index1: int, index2: int) -> float:
        p1 = self._path.poses[index1].position
        p2 = self._path.poses[index2].position
        return p1.distance(p2)

    def _path_window(self, pose_index: int) -> tuple[int, int, float]:
        if not self._path.poses:
            return 0, 0, 0.0
        start = min(max(0, pose_index), len(self._path.poses) - 1)
        end = start
        distance = 0.0
        for index in range(start + 1, len(self._path.poses)):
            segment = self._path.poses[index - 1].position.distance(
                self._path.poses[index].position
            )
            if distance + segment > self._path_lookup_distance:
                break
            distance += segment
            end = index
        return start, end, distance

    def _nearest_occupied_progress(
        self,
        costmap: OccupancyGrid,
        occupied_hits: NDArray[np.int64],
        path_start: int,
        path_end: int,
    ) -> float | None:
        if len(occupied_hits) == 0 or path_end < path_start:
            return None
        poses = self._path.poses[path_start : path_end + 1]
        if not poses:
            return None
        path_xy = np.asarray(
            [[pose.position.x, pose.position.y] for pose in poses],
            dtype=np.float64,
        )
        cumulative = np.concatenate(
            (
                np.array([0.0]),
                np.cumsum(np.linalg.norm(np.diff(path_xy, axis=0), axis=1)),
            )
        )
        closest_progress = float("inf")
        for row, column in occupied_hits:
            world = costmap.grid_to_world((int(column), int(row)))
            index = int(
                np.argmin(np.square(path_xy[:, 0] - world.x) + np.square(path_xy[:, 1] - world.y))
            )
            closest_progress = min(closest_progress, float(cumulative[index]))
        return closest_progress if np.isfinite(closest_progress) else None
