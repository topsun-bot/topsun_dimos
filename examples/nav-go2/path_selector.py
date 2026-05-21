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

from __future__ import annotations

import math

import numpy as np

from dimos.msgs.nav_msgs.OccupancyGrid import CostValues, OccupancyGrid
from dimos.navigation.replanning_a_star.navigation_map import NavigationMap


class TrajectoryPathSelector:
    """Select the best trajectory from sampled paths using traversability cost."""

    def __init__(
        self,
        preferred_index: int = 0,
        navigation_map_robot_increase: float = 2.0,
        **_: object,
    ) -> None:
        self._preferred_index = preferred_index
        self._navigation_map_robot_increase = navigation_map_robot_increase

    def select_best_index(
        self,
        trajectories: np.ndarray,
        navigation_map: NavigationMap,
    ) -> int:
        self._validate_trajectories(trajectories)
        costmap = navigation_map.make_gradient_costmap(self._navigation_map_robot_increase)
        costs = [
            self._score_trajectory(trajectory, costmap, index)
            for index, trajectory in enumerate(trajectories)
        ]
        return int(np.argmin(costs))

    def score_trajectory(
        self,
        trajectory: np.ndarray,
        navigation_map: NavigationMap,
    ) -> float:
        costmap = navigation_map.make_gradient_costmap(self._navigation_map_robot_increase)
        return self._trajectory_traversability_cost(
            trajectory, costmap
        ) + self._trajectory_goal_cost(trajectory)

    def is_high_quality(
        self,
        trajectory: np.ndarray,
        navigation_map: NavigationMap,
        threshold: float | None = None,
    ) -> bool:
        score = self.score_trajectory(trajectory, navigation_map)
        return threshold is None or score <= threshold

    def _score_trajectory(
        self,
        trajectory: np.ndarray,
        costmap: OccupancyGrid,
        index: int,
    ) -> float:
        cost = self._trajectory_traversability_cost(
            trajectory, costmap
        ) + self._trajectory_goal_cost(trajectory)
        return cost + self._preferred_index_bias(index)

    def _trajectory_traversability_cost(
        self,
        trajectory: np.ndarray,
        costmap: OccupancyGrid,
    ) -> float:
        height, width = costmap.grid.shape
        total_cost = 0.0

        for x_fwd, y_lat in trajectory:
            row, col = self._costmap_cell(float(x_fwd), float(y_lat), costmap)
            if row < 0 or row >= height or col < 0 or col >= width:
                total_cost += float(CostValues.OCCUPIED)
                continue

            cell = int(costmap.grid[row, col])
            total_cost += float(CostValues.OCCUPIED) if cell == CostValues.UNKNOWN else float(cell)

        return total_cost / max(len(trajectory), 1)

    @staticmethod
    def _costmap_cell(x_fwd: float, y_lat: float, costmap: OccupancyGrid) -> tuple[int, int]:
        origin = costmap.origin.position
        col = math.floor((x_fwd - origin.x) / costmap.resolution)
        row = math.floor((y_lat - origin.y) / costmap.resolution)
        return row, col

    def _trajectory_goal_cost(self, trajectory: np.ndarray) -> float:
        """Placeholder for future goal-based path scoring.

        This method currently returns 0.0 but can be extended to bias selection
        toward target positions or heading preferences.
        """
        return 0.0

    def _preferred_index_bias(self, index: int) -> float:
        return 0.001 * abs(index - self._preferred_index)

    @staticmethod
    def _validate_trajectories(trajectories: np.ndarray) -> None:
        if trajectories.ndim != 3 or trajectories.shape[-1] != 2:
            raise ValueError(f"Expected (N, T, 2) trajectories, got {trajectories.shape}")
        if trajectories.shape[0] == 0:
            raise ValueError("No candidate trajectories available")
