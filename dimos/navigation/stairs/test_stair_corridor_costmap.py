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

import numpy as np

from dimos.mapping.terrain.fixtures.synthetic import straight_stair_5_steps_17cm
from dimos.mapping.terrain.stair_detection import (
    candidates_to_corridors,
    detect_stairs_from_height_map,
)
from dimos.msgs.geometry_msgs.Pose import Pose
from dimos.msgs.geometry_msgs.Vector3 import Vector3
from dimos.msgs.nav_msgs.OccupancyGrid import CostValues, OccupancyGrid
from dimos.navigation.stairs.contracts import StairDetectionConfig
from dimos.navigation.stairs.geometry import (
    clear_corridor_in_costmap,
    point_in_stair_corridor,
    snap_goal_to_corridor,
)


def _corridor():
    synth = straight_stair_5_steps_17cm()
    cfg = StairDetectionConfig(resolution=synth.resolution)
    candidates = detect_stairs_from_height_map(
        synth.height_map,
        synth.origin_x,
        synth.origin_y,
        cfg,
    )
    return candidates_to_corridors(candidates)[0]


def test_point_in_stair_corridor_on_centerline() -> None:
    corridor = _corridor()
    mid = corridor.centerline[len(corridor.centerline) // 2]
    assert point_in_stair_corridor(Vector3(mid[0], mid[1], 0.0), corridor)


def test_clear_corridor_marks_centerline_free() -> None:
    corridor = _corridor()
    mid = corridor.centerline[len(corridor.centerline) // 2]
    resolution = 0.05
    width = height = 80
    origin = Pose()
    origin.position.x = mid[0] - width * resolution / 2
    origin.position.y = mid[1] - height * resolution / 2
    origin.orientation.w = 1.0
    grid = OccupancyGrid(
        grid=np.full((height, width), CostValues.OCCUPIED, dtype=np.int8),
        resolution=resolution,
        origin=origin,
        frame_id="map",
    )
    cleared = clear_corridor_in_costmap(grid, corridor)
    assert cleared.cell_value(Vector3(mid[0], mid[1], 0.0)) == CostValues.FREE


def test_snap_goal_inside_corridor() -> None:
    corridor = _corridor()
    cl = corridor.centerline
    raw = Vector3(cl[-1][0], cl[-1][1] + 0.1, 0.0)
    assert point_in_stair_corridor(raw, corridor)
    snapped = snap_goal_to_corridor(raw, corridor)
    assert snapped.distance(raw) < 0.5
