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

"""Shared configuration for trajectory-model local planner modules."""

from __future__ import annotations

from dimos.core.module import ModuleConfig


class TrajectoryLocalPlannerConfig(ModuleConfig):
    """Model-independent trajectory local planner parameters."""

    # Candidate trajectory selected for the main local_waypoints output.
    selected_trajectory_index: int = 0

    # Publish traversability_map as a debug artifact derived from candidate paths.
    publish_debug_traversability_map: bool = True

    # Local traversability grid in base_link (meters)
    grid_forward_m: float = 4.0
    grid_lateral_m: float = 3.0
    grid_resolution_m: float = 0.05

    min_inference_interval_s: float = 0.25
