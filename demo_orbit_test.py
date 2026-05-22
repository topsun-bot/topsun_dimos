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

"""Test script: agent-mode orbit_object test.

Usage:
  Real robot:  OPENAI_API_KEY=sk-xxx HF_HUB_OFFLINE=1 uv run python demo_orbit_test.py <robot_ip>
  Simulation:  OPENAI_API_KEY=sk-xxx uv run python demo_orbit_test.py

启动后使用 dimos agent-send 发送命令:
  uv run dimos agent-send "orbit the nearest obstacle"
"""

import sys
import threading

from dimos.core.global_config import global_config

robot_ip = sys.argv[1] if len(sys.argv) > 1 else None

if robot_ip:
    global_config.update(robot_ip=robot_ip, viewer="none")
else:
    global_config.update(simulation="mujoco", viewer="rerun")

from dimos.agents.mcp.mcp_client import McpClient
from dimos.agents.mcp.mcp_server import McpServer
from dimos.agents.skills.navigation import NavigationSkillContainer
from dimos.agents.skills.orbit_object import OrbitObjectSkillContainer
from dimos.agents.web_human_input import WebInput
from dimos.core.coordination.blueprints import autoconnect
from dimos.perception.spatial_perception import SpatialMemory
from dimos.robot.unitree.go2.blueprints.smart.unitree_go2 import unitree_go2
from dimos.robot.unitree.unitree_skill_container import UnitreeSkillContainer

orbit_test = autoconnect(
    unitree_go2,
    SpatialMemory.blueprint(),
    NavigationSkillContainer.blueprint(),
    UnitreeSkillContainer.blueprint(),
    OrbitObjectSkillContainer.blueprint(),
    WebInput.blueprint(),
    McpServer.blueprint(),
    McpClient.blueprint(),
)

if __name__ == "__main__":
    from dimos.core.coordination.module_coordinator import ModuleCoordinator
    from dimos.utils.logging_config import setup_logger

    logger = setup_logger()

    _original_send = ModuleCoordinator._send_on_system_modules

    def _safe_send(self) -> None:
        """Use rpcs dict instead of hasattr (avoid pipe blocking), timeout-safe."""
        modules = list(self._deployed_modules.values())
        for module in modules:
            if "on_system_modules" in getattr(module, "rpcs", set()):
                try:
                    module.on_system_modules(modules)
                except Exception as exc:
                    logger.warning("on_system_modules failed", module=str(module), error=str(exc))
                    # 超时也不阻塞，后台重试
                    threading.Thread(
                        target=_retry_on_system_modules,
                        args=(module, modules),
                        daemon=True,
                    ).start()

    def _retry_on_system_modules(module, modules) -> None:
        import time

        for attempt in range(3):
            time.sleep(10)
            try:
                module.on_system_modules(modules)
                logger.info("on_system_modules retry succeeded", attempt=attempt + 1)
                return
            except Exception:
                logger.warning("on_system_modules retry failed", attempt=attempt + 1)

    ModuleCoordinator._send_on_system_modules = _safe_send

    coordinator = ModuleCoordinator.build(orbit_test)
    print("\n" + "=" * 50)
    print("  Agent 模式已启动")
    print("  在另一个终端执行:")
    print('  uv run dimos agent-send "orbit the nearest obstacle"')
    print("=" * 50 + "\n")
    coordinator.loop()
