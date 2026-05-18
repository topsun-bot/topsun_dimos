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

from dimos.msgs.geometry_msgs.Vector3 import Vector3
from dimos.navigation.stairs.fixtures_corridor import (
    synthetic_stair_corridor_ascending,
    synthetic_stair_corridor_descending,
)
from dimos.navigation.stairs.geometry import max_lateral_offset, path_crosses_riser
from dimos.navigation.stairs.plan_in_corridor import plan_in_corridor


def test_plan_in_corridor_stays_within_lateral_band() -> None:
    corridor = synthetic_stair_corridor_ascending()
    cl = corridor.centerline
    start = Vector3(cl[0][0], cl[0][1], 0.0)
    goal = Vector3(cl[-1][0], cl[-1][1], 0.0)

    path = plan_in_corridor(start, goal, corridor, step_m=0.08)
    assert path is not None
    assert len(path.poses) >= 3
    assert max_lateral_offset(path, corridor.centerline) <= corridor.safe_half_width + 0.05
    assert not path_crosses_riser(path, corridor)


def test_plan_in_corridor_descending() -> None:
    corridor = synthetic_stair_corridor_descending()
    cl = corridor.centerline
    start = Vector3(cl[-1][0], cl[-1][1], 0.0)
    goal = Vector3(cl[0][0], cl[0][1], 0.0)

    path = plan_in_corridor(start, goal, corridor, step_m=0.08)
    assert path is not None
    assert len(path.poses) >= 3
    assert max_lateral_offset(path, corridor.centerline) <= corridor.safe_half_width + 0.05


def test_plan_in_corridor_long_run() -> None:
    corridor = synthetic_stair_corridor_ascending()
    cl = corridor.centerline
    start = Vector3(cl[0][0], cl[0][1], 0.0)
    goal = Vector3(cl[-1][0], cl[-1][1], 0.0)
    path = plan_in_corridor(start, goal, corridor, step_m=0.1)
    assert path is not None
    assert not path_crosses_riser(path, corridor)
