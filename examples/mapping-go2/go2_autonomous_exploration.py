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

这个blueprint组装了完整的自主探索系统，使用DimOS现有的导航和地图模块。
"""

from dimos.agents.mcp.mcp_client import McpClient
from dimos.agents.mcp.mcp_server import McpServer
from dimos.core.coordination.blueprints import autoconnect
from dimos.mapping.costmapper import CostMapper
from dimos.mapping.voxels import VoxelGridMapper
from dimos.navigation.frontier_exploration.wavefront_frontier_goal_selector import (
    WavefrontFrontierExplorer,
)
from dimos.navigation.patrolling.module import PatrollingModule
from dimos.navigation.replanning_a_star.module import ReplanningAStarPlanner
from dimos.robot.unitree.go2.blueprints.basic.unitree_go2_basic import unitree_go2_basic

# 导入技能容器
from dimos.agents.skills.navigation import NavigationSkillContainer

# 导入系统提示词
from dimos.agents.system_prompt import SYSTEM_PROMPT


# 组装完整的自主探索blueprint
go2_autonomous_exploration = (
    autoconnect(
        # 1. Go2基础连接（传感器和控制）
        unitree_go2_basic,
        # 2. 地图构建模块
        VoxelGridMapper.blueprint(),  # 体素地图（点云->3D地图）
        CostMapper.blueprint(),  # 代价地图（用于路径规划）
        # 3. 导航模块
        ReplanningAStarPlanner.blueprint(),  # A*路径规划器
        WavefrontFrontierExplorer.blueprint(),  # 前沿探索
        PatrollingModule.blueprint(),  # 巡逻模块（管理探索目标）
        # 4. 技能容器（为LLM提供导航技能）
        NavigationSkillContainer.blueprint(),
        # 5. MCP Server（暴露技能为MCP工具）
        McpServer.blueprint(),
        # 6. MCP Client（LLM Agent）
        McpClient.blueprint(system_prompt=SYSTEM_PROMPT),
    )
    .global_config(
        n_workers=9,  # 使用9个worker进程
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
    print("  - VoxelGridMapper: 点云->3D体素地图")
    print("  - CostMapper: 生成导航代价地图")
    print("  - ReplanningAStarPlanner: A*路径规划")
    print("  - WavefrontFrontierExplorer: 前沿探索算法")
    print("  - PatrollingModule: 探索目标管理")
    print("  - NavigationSkillContainer: 导航技能接口")
    print()
    print("可用技能：")
    print("  - set_goal(x, y, theta): 设置导航目标")
    print("  - cancel_goal(): 取消当前目标")
    print("  - get_navigation_state(): 获取导航状态")
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
    print("  - Rerun: 实时查看地图、路径、前沿点")
    print("  - 启动: 添加 --viewer rerun 参数")
    print()
    print("=" * 70)
    print()
    print("正在启动系统...")
    print()

    # 构建并运行blueprint
    go2_autonomous_exploration.build().loop()


if __name__ == "__main__":
    main()
