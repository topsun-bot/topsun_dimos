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

"""Regression: corridor planning must not depend on costmap safe-goal snapping."""

from dimos.mapping.terrain.fixtures.synthetic import straight_stair_5_steps_17cm
from dimos.mapping.terrain.stair_detection import (
    candidates_to_corridors,
    detect_stairs_from_height_map,
)
from dimos.msgs.geometry_msgs.Vector3 import Vector3
from dimos.navigation.stairs.contracts import StairDetectionConfig
from dimos.navigation.stairs.geometry import snap_goal_to_corridor
from dimos.navigation.stairs.plan_in_corridor import plan_in_corridor


def test_plan_in_corridor_with_snapped_goal() -> None:
    synth = straight_stair_5_steps_17cm()
    cfg = StairDetectionConfig(resolution=synth.resolution)
    candidates = detect_stairs_from_height_map(
        synth.height_map,
        synth.origin_x,
        synth.origin_y,
        cfg,
    )
    corridor = candidates_to_corridors(candidates)[0]
    cl = corridor.centerline
    start = Vector3(cl[0][0] - 0.5, cl[0][1], 0.0)
    # Goal offset laterally (safe_goal would snap away from the corridor).
    raw_goal = Vector3(cl[-1][0], cl[-1][1] + 1.5, 0.0)
    corridor_goal = snap_goal_to_corridor(raw_goal, corridor)
    path = plan_in_corridor(start, corridor_goal, corridor)
    assert path is not None
    assert len(path.poses) >= 2
