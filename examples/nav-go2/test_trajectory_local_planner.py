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

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent))

from engine.nomad.config import NoMaDConfig
from trajectory_local_planner_module import TrajectoryLocalPlannerConfig


def test_nomad_config_loads_waypoint_selection() -> None:
    cfg = NoMaDConfig.load_default()
    assert cfg.waypoint_selection in ("navigation_map", "multi_waypoints")


def test_planner_config_accepts_multi_waypoints_fields() -> None:
    cfg = TrajectoryLocalPlannerConfig(
        waypoint_selection="multi_waypoints",
        collision_thresh=0.3,
        max_clusters=3,
    )
    assert cfg.waypoint_selection == "multi_waypoints"
    assert cfg.collision_thresh == 0.3
