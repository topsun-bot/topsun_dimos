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

from unittest.mock import patch

import numpy as np

from dimos.core.global_config import GlobalConfig
from dimos.experimental.navigation_obstacle_mvp.forward_obstacle_height import MVP_SILLS
from dimos.experimental.navigation_obstacle_mvp.mvp_costmap import apply_mvp_sill_lethal_zones
from dimos.msgs.geometry_msgs.Pose import Pose
from dimos.msgs.geometry_msgs.Vector3 import Vector3
from dimos.msgs.nav_msgs.OccupancyGrid import OccupancyGrid
from dimos.navigation.replanning_a_star.global_planner import GlobalPlanner


def _sim_mvp_costmap() -> OccupancyGrid:
    grid = np.zeros((250, 250), dtype=np.int8)
    costmap = OccupancyGrid(
        grid=grid,
        resolution=0.08,
        origin=Pose(position=Vector3(-5.0, -5.0, 0.0)),
        frame_id="world",
    )
    return apply_mvp_sill_lethal_zones(
        costmap,
        obstacle_crossing_mvp=True,
        simulation="mujoco",
    )


def test_handle_obstacle_found_sets_mvp_detour_flag() -> None:
    cfg = GlobalConfig(_env_file=None, obstacle_crossing_mvp=True, simulation="mujoco")
    gp = GlobalPlanner(cfg)
    with patch.object(gp, "_replan_path") as mock_replan:
        gp._handle_stop_message("obstacle_found")
        assert gp._last_stop_message == "obstacle_found"
        mock_replan.assert_called_once()


def test_find_mvp_detour_path_avoids_sill_center() -> None:
    cfg = GlobalConfig(_env_file=None, obstacle_crossing_mvp=True, simulation="mujoco")
    gp = GlobalPlanner(cfg)
    costmap = _sim_mvp_costmap()
    gp.handle_global_costmap(costmap)

    robot = Vector3(-1.12, 2.5, 0.0)
    goal = Vector3(-1.12, 4.0, 0.0)
    path = gp._find_mvp_detour_path(goal, robot)

    assert path is not None
    assert path.poses
    medium = MVP_SILLS[1]
    center_hits = [
        pose
        for pose in path.poses
        if abs(pose.position.x - medium.x) < 0.3
        and abs(pose.position.y - medium.y) < 0.15
    ]
    assert not center_hits
