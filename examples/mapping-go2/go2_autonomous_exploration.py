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

"""Unitree Go2 自主探索与地图绘制 - 主Blueprint.

这个blueprint组装了完整的自主探索系统，包括：
- 感知模块：空间记忆和感知技能
- 地图模块：体素地图和代价地图
- 导航模块：路径规划和前沿探索
- 地图保存：自动保存和手动保存
- MCP集成：LLM控制接口
"""

from pathlib import Path

from dimos.agents.mcp.mcp_client import McpClient
from dimos.agents.mcp.mcp_server import McpServer
from dimos.core.coordination.blueprints import autoconnect
from dimos.core.coordination.module_coordinator import ModuleCoordinator
from dimos.mapping.costmapper import CostMapper
from dimos.mapping.voxels import VoxelGridMapper
from dimos.navigation.frontier_exploration.wavefront_frontier_goal_selector import (
    WavefrontFrontierExplorer,
)
from dimos.navigation.patrolling.module import PatrollingModule
from dimos.navigation.replanning_a_star.module import ReplanningAStarPlanner
from dimos.perception.perceive_loop_skill import PerceiveLoopSkill
from dimos.perception.spatial_perception import SpatialMemory
from dimos.robot.unitree.go2.blueprints.basic.unitree_go2_basic import unitree_go2_basic
from dimos.robot.unitree.go2.connection import GO2Connection

# 导入技能容器
from dimos.agents.skills.navigation import NavigationSkillContainer

# 导入系统提示词
from dimos.agents.system_prompt import SYSTEM_PROMPT

# 导入地图保存模块
import sys
sys.path.insert(0, str(Path(__file__).parent))
from map_saver_module import MapSaverModule


# 组装完整的自主探索blueprint
go2_autonomous_exploration = (
    autoconnect(
        # 1. Go2基础连接（传感器和控制）
        unitree_go2_basic,
        # 2. 感知模块
        SpatialMemory.blueprint(),  # 空间记忆（物体跟踪和定位）
        PerceiveLoopSkill.blueprint(),  # 感知循环技能
        # 3. 地图构建模块
        VoxelGridMapper.blueprint(),  # 体素地图（点云->3D地图）
        CostMapper.blueprint(),  # 代价地图（用于路径规划）
        # 4. 导航模块
        ReplanningAStarPlanner.blueprint(),  # A*路径规划器
        WavefrontFrontierExplorer.blueprint(),  # 前沿探索
        PatrollingModule.blueprint(),  # 巡逻模块（管理探索目标）
        # 5. 地图保存模块
        MapSaverModule.blueprint(
            save_dir="maps",  # 保存目录
            auto_save_interval=60.0,  # 每60秒自动保存
            enable_auto_save=True,  # 启用自动保存
        ),
        # 6. 技能容器（为LLM提供导航技能）
        NavigationSkillContainer.blueprint(),
        # 7. MCP Server（暴露技能为MCP工具）
        McpServer.blueprint(),
        # 8. MCP Client（LLM Agent）
        McpClient.blueprint(system_prompt=SYSTEM_PROMPT),
    )
    .global_config(
        n_workers=11,  # 使用11个worker进程（增加了地图保存模块）
        robot_model="unitree_go2",
    )
)


def main() -> None:
    """主函数 - 构建并运行blueprint."""
    print("=" * 70)
    print("Unitree Go2 自主探索与地图绘制系统")
    print("=" * 70)
    print()
    print("系统架构：")
    print("  【感知层】")
    print("  - SpatialMemory: 空间记忆（物体跟踪和定位）")
    print("  - PerceiveLoopSkill: 感知循环技能")
    print()
    print("  【地图层】")
    print("  - VoxelGridMapper: 点云->3D体素地图")
    print("  - CostMapper: 生成导航代价地图")
    print()
    print("  【导航层】")
    print("  - ReplanningAStarPlanner: A*路径规划")
    print("  - WavefrontFrontierExplorer: 前沿探索算法")
    print("  - PatrollingModule: 探索目标管理")
    print()
    print("  【技能层】")
    print("  - NavigationSkillContainer: 导航技能接口")
    print("  - MapSaverModule: 地图保存（自动+手动）")
    print("  - MCP Server/Client: LLM集成")
    print()
    print("可用技能：")
    print("  导航: set_goal, cancel_goal, get_navigation_state")
    print("  感知: perceive_objects, query_spatial_memory")
    print("  地图: save_map_now, get_save_status, set_auto_save")
    print()
    print("地图保存：")
    print("  - 自动保存: 每60秒自动保存到 maps/ 目录")
    print("  - 手动保存: dimos mcp call save_map_now --arg name='my_map'")
    print("  - 停止时保存: 系统停止时自动保存最终地图")
    print("  - 格式: PGM + YAML (ROS标准格式)")
    print()
    print("启动方式：")
    print("  1. 回放模式: dimos --replay run go2-autonomous-exploration")
    print("  2. 仿真模式: dimos --simulation run go2-autonomous-exploration")
    print("  3. 真实硬件: export ROBOT_IP=192.168.123.161")
    print("              python examples/mapping-go2/go2_autonomous_exploration.py")
    print()
    print("MCP接口：")
    print("  - 服务地址: http://localhost:9990/mcp")
    print("  - 查看技能: dimos mcp list-tools")
    print("  - 调用技能: dimos mcp call set_goal --arg x=2.0 --arg y=1.0")
    print("  - 发送命令: dimos agent-send 'navigate to x=2, y=1'")
    print()
    print("可视化：")
    print("  - Rerun: 实时查看地图、路径、前沿点、检测物体")
    print("  - 启动: 添加 --viewer rerun 参数")
    print()
    print("=" * 70)
    print()
    print("正在启动系统...")
    print()

    # 构建并运行blueprint
    ModuleCoordinator.build(go2_autonomous_exploration).loop()


if __name__ == "__main__":
    main()
