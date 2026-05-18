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

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


@dataclass(frozen=True)
class StairDetectionConfig:
    """Parameters for single-segment straight stair detection."""

    min_riser: float = 0.15
    max_riser: float = 0.20
    min_tread: float = 0.25
    min_steps: int = 3
    resolution: float = 0.05
    can_pass_under: float = 0.6


@dataclass(frozen=True)
class StairCandidate:
    """Detected straight stair segment in the map frame."""

    axis_yaw: float
    ascending_direction: tuple[float, float]
    centerline: list[tuple[float, float]]
    polygon: list[tuple[float, float]]
    mean_riser: float
    mean_tread: float
    confidence: float


@dataclass(frozen=True)
class StairCorridor:
    """Navigable corridor along a stair segment."""

    polygon: list[tuple[float, float]]
    centerline: list[tuple[float, float]]
    safe_half_width: float
    ascending: bool
    axis_yaw: float = 0.0
    mean_riser: float = 0.17
    mean_tread: float = 0.28
    riser_lines: list[tuple[tuple[float, float], tuple[float, float]]] = field(default_factory=list)


class StairPhase(str, Enum):
    IDLE = "idle"
    APPROACH = "approach"
    ALIGN = "align"
    ON_STAIR = "on_stair"
    EXIT = "exit"
