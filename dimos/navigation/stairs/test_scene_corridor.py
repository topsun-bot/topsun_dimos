# Copyright 2026 Dimensional Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

from dimos.msgs.geometry_msgs import Vector3
from dimos.navigation.stairs.geometry import goal_requests_stair_corridor, snap_goal_to_corridor
from dimos.navigation.stairs.scene_corridor import mujoco_scene_stairs_corridor
from dimos.simulation.mujoco.build_scene_stairs import stair_top_goal_xy


def test_scene_corridor_accepts_climb_goal() -> None:
    corridor = mujoco_scene_stairs_corridor()
    gx, gy = stair_top_goal_xy()
    goal = Vector3(gx, gy, 0.0)
    assert goal_requests_stair_corridor(goal, corridor)
    snapped = snap_goal_to_corridor(goal, corridor)
    assert abs(snapped.y - gy) < 0.05
