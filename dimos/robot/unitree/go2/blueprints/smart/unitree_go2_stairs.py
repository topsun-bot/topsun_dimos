#!/usr/bin/env python3
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

"""Go2 stack with straight-stair detection and corridor planning enabled.

Run with stair navigation (enabled by default in this blueprint)::

    dimos run unitree-go2-stairs
    dimos run unitree-go2-stairs --stair-navigation
    dimos --stair-navigation run unitree-go2
    dimos --replay run unitree-go2-stairs

Install once: ``uv sync --extra go2-sim`` (MuJoCo + torch for mapping).

DimOS MuJoCo (default): lidars, head camera, ``mujoco_room=stairs``, Sport API → SHM::

    dimos --simulation run unitree-go2-stairs
    # optional: --mujoco-start-pos 2.5, 0.0

Optional official ``unitree_mujoco`` (DDS, no DimOS lidar stack)::

    dimos --simulation --mujoco-backend unitree run unitree-go2-stairs
"""

from dimos.core.coordination.blueprints import autoconnect
from dimos.navigation.stairs.stair_navigator_module import StairNavigatorModule
from dimos.robot.unitree.go2.blueprints.smart.unitree_go2 import unitree_go2

unitree_go2_stairs = autoconnect(
    unitree_go2,
    StairNavigatorModule.blueprint(),
).global_config(
    stair_navigation=True,
    n_workers=10,
    mujoco_room="stairs",
    # Slightly closer to the first tread; idle rest is disabled when stair_navigation=True.
    mujoco_start_pos="2.35, 0.0",
)

__all__ = ["unitree_go2_stairs"]
