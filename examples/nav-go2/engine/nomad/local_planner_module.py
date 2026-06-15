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

"""NoMaD adapter for trajectory-model local planning."""

from __future__ import annotations

from engine.nomad.config import NoMaDConfig
from engine.nomad.inference import NoMaDEngine
from trajectory_inference import TrajectoryNavigationEngine
from trajectory_local_planner_module import TrajectoryLocalPlannerModule


class NoMaDTrajectoryLocalPlannerModule(TrajectoryLocalPlannerModule):
    """Trajectory local planner backed by NoMaD exploration diffusion."""

    config: NoMaDConfig
    engine_name = "NoMaD"

    def _make_engine(self) -> TrajectoryNavigationEngine:
        return NoMaDEngine(self.config)
