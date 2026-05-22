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

"""Map path costmap samples to simulated foot-raise height (meters)."""

from __future__ import annotations

LOW_OBSTACLE_COST_THRESHOLD = 50
# Costmap cost at which we treat the sill as ~15 cm (full raise).
HIGH_SILL_COST_THRESHOLD = 80
# Minimum commanded foot clearance for 15 cm door sills (meters).
MIN_RAISE_FOR_15CM_SILL_M = 0.15
# Cross gait: fixed clearance (no ONNX normal stride during cross).
CROSS_FOOT_RAISE_M = 0.20
CROSS_FRONT_REACH_M = 0.05
MAX_FOOT_RAISE_M = CROSS_FOOT_RAISE_M
MAX_FRONT_REACH_M = CROSS_FRONT_REACH_M


def foot_raise_height_for_cost(
    max_cost: int,
    max_raise_m: float,
    *,
    cost_threshold: int = LOW_OBSTACLE_COST_THRESHOLD,
) -> float:
    """Scale foot raise for costs in [1, cost_threshold).

    Cost 0 / unknown are treated as flat ground. Costs >= threshold use normal gait.
    """
    if max_cost < 1 or max_cost >= cost_threshold or max_raise_m <= 0.0:
        return 0.0
    span = max(1, cost_threshold - 1)
    capped_max = min(max_raise_m, MAX_FOOT_RAISE_M)
    return min(MAX_FOOT_RAISE_M, capped_max * (max_cost / span))


def front_reach_for_cost(
    max_cost: int,
    max_reach_m: float,
    *,
    cost_threshold: int = LOW_OBSTACLE_COST_THRESHOLD,
) -> float:
    """Forward probe distance for front feet when crossing low obstacles (meters)."""
    if max_cost < 1 or max_cost >= cost_threshold or max_reach_m <= 0.0:
        return 0.0
    span = max(1, cost_threshold - 1)
    capped_max = min(max_reach_m, MAX_FRONT_REACH_M)
    return min(MAX_FRONT_REACH_M, capped_max * (max_cost / span))


def cross_threshold_gait(
    max_cost: int,
    cross_raise_m: float,
    cross_reach_m: float,
) -> tuple[float, float]:
    """Fixed 20 cm raise / 5 cm reach when crossing; no cost scaling or normal stride."""
    if max_cost < 1:
        return 0.0, 0.0
    _ = cross_raise_m, cross_reach_m
    return CROSS_FOOT_RAISE_M, CROSS_FRONT_REACH_M
