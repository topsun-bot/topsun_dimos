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

"""Go2 navigation stack with local 4G WebRTC automatic ArUco recharge."""

from dimos.core.coordination.blueprints import autoconnect
from dimos.robot.unitree.go2.blueprints.smart.unitree_go2 import unitree_go2
from dimos.robot.unitree.go2.recharge.auto_module import AutoRechargeModule

unitree_go2_auto_recharge = autoconnect(
    unitree_go2,
    # Commissioning default: complete navigation and visual docking, but require
    # an explicit code/config change before the first physical lie-down trial.
    AutoRechargeModule.blueprint(allow_liedown=False),
).global_config(n_workers=11, robot_model="unitree_go2")
