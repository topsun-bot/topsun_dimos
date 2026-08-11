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

"""Minimal 4G Go2 visual-recharge stack, deliberately free of GPU-only perception."""

from dimos.agents.mcp.mcp_server import McpServer
from dimos.core.coordination.blueprints import autoconnect
from dimos.navigation.movement_manager.movement_manager import MovementManager
from dimos.robot.unitree.go2.connection import GO2Connection
from dimos.robot.unitree.go2.recharge.module import ArucoRechargeModule

unitree_go2_aruco_recharge_minimal = autoconnect(
    GO2Connection.blueprint(lidar=False, camera=True),
    MovementManager.blueprint(),
    ArucoRechargeModule.blueprint(),
    McpServer.blueprint(),
).global_config(n_workers=4, robot_model="unitree_go2")
