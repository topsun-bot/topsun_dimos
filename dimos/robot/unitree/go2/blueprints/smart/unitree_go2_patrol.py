#!/usr/bin/env python3
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

from dimos.core.blueprints import autoconnect
from dimos.mapping.costmapper import cost_mapper
from dimos.mapping.patrol_map_manager import patrol_map_manager
from dimos.mapping.voxels import voxel_mapper
from dimos.navigation.patrol.patrol_executor import patrol_executor
from dimos.navigation.patrol.patrol_vis import patrol_vis
from dimos.navigation.replanning_a_star.module import (
    ReplanningAStarPlanner,
    replanning_a_star_planner,
)
from dimos.robot.unitree.go2.blueprints.basic.unitree_go2_basic import unitree_go2_basic
from dimos.slam.lidar_slam import lidar_slam

unitree_go2_patrol = autoconnect(
    unitree_go2_basic,
    lidar_slam(),
    voxel_mapper(voxel_size=0.1),
    cost_mapper(),
    patrol_map_manager(),
    replanning_a_star_planner(),
    patrol_executor(),
    patrol_vis(),
).remappings(
    [(ReplanningAStarPlanner, "odom", "slam_pose")]
).global_config(n_workers=10, robot_model="unitree_go2")

__all__ = ["unitree_go2_patrol"]
