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
"""

from dimos.core.coordination.blueprints import autoconnect
from dimos.navigation.stairs.stair_navigator_module import StairNavigatorModule
from dimos.robot.unitree.go2.blueprints.smart.unitree_go2 import unitree_go2

unitree_go2_stairs = autoconnect(
    unitree_go2,
    StairNavigatorModule.blueprint(),
).global_config(stair_navigation=True, n_workers=10)

__all__ = ["unitree_go2_stairs"]
