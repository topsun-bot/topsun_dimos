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

这个blueprint组装了完整的自主探索系统,包括:
- 地图模块:体素地图和代价地图
- 导航模块:路径规划和前沿探索
- 地图保存:自动保存和手动保存

纯自主模式:无需 LLM;通过本地 MCP HTTP 调用 ``begin_exploration`` 启动探索.
也可在运行后用 ``dimos mcp call begin_exploration`` / ``end_exploration`` 手动控制.
"""

import argparse
import os
from pathlib import Path
import sys

from dimos.agents.mcp.mcp_adapter import McpAdapter, McpError
from dimos.agents.mcp.mcp_server import McpServer
from dimos.core.coordination.blueprints import autoconnect
from dimos.core.coordination.module_coordinator import ModuleCoordinator
from dimos.core.global_config import global_config
from dimos.mapping.costmapper import CostMapper
from dimos.mapping.voxels import VoxelGridMapper
from dimos.navigation.frontier_exploration.wavefront_frontier_goal_selector import (
    WavefrontFrontierExplorer,
)
from dimos.navigation.patrolling.module import PatrollingModule
from dimos.navigation.replanning_a_star.module import ReplanningAStarPlanner
from dimos.robot.unitree.go2.blueprints.basic.unitree_go2_basic import unitree_go2_basic

# 导入地图保存模块
sys.path.insert(0, str(Path(__file__).parent))
from map_saver_module import MapSaverModule


def _start_exploration_via_mcp() -> str:
    """在 MCP 就绪后调用 ``begin_exploration``(不经过 LLM)."""
    adapter = McpAdapter()
    if not adapter.wait_for_ready(timeout=60.0):
        raise RuntimeError(f"MCP 在 60s 内未就绪({adapter.url}).请确认 McpServer 已随蓝图启动.")
    try:
        return adapter.call_tool_text("begin_exploration")
    except McpError as e:
        raise RuntimeError(f"MCP 调用 begin_exploration 失败: {e}") from e


# 组装完整的自主探索blueprint
def create_exploration_blueprint(robot_ip: str | None = None):
    """创建纯自主探索blueprint(无需LLM).

    Args:
        robot_ip: 机器人IP地址,或 "mujoco"/"replay" 用于仿真/回放模式
    """
    return autoconnect(
        # 1. Go2基础连接(传感器和控制)
        unitree_go2_basic,
        # 2. 地图构建模块
        VoxelGridMapper.blueprint(),  # 体素地图(点云->3D地图)
        CostMapper.blueprint(),  # 代价地图(用于路径规划)
        # 3. 导航模块
        ReplanningAStarPlanner.blueprint(),  # A*路径规划器
        WavefrontFrontierExplorer.blueprint(),  # 前沿探索(自动选择探索目标)
        PatrollingModule.blueprint(),  # 巡逻模块(管理探索目标)
        # 4. 地图保存模块
        MapSaverModule.blueprint(
            save_dir="maps",  # 保存目录
            auto_save_interval=60.0,  # 每60秒自动保存
            enable_auto_save=True,  # 启用自动保存
        ),
        # 暴露各模块 @skill(含 WavefrontFrontierExplorer.begin_exploration)
        McpServer.blueprint(),
    ).global_config(
        n_workers=6,  # 使用6个worker进程
        robot_model="unitree_go2",
        robot_ip=robot_ip,  # 设置机器人IP
    )


def main() -> None:
    """主函数 - 构建并运行blueprint."""
    # 解析命令行参数
    parser = argparse.ArgumentParser(description="Unitree Go2 自主探索与地图保存系统")
    parser.add_argument("--simulation", action="store_true", help="使用MuJoCo仿真模式")
    parser.add_argument("--replay", action="store_true", help="使用回放模式")
    parser.add_argument(
        "--viewer", type=str, choices=["rerun", "rerun-web", "foxglove"], help="启用可视化"
    )
    parser.add_argument(
        "--no-mcp-auto-explore",
        action="store_true",
        help="不自动 MCP 调用 begin_exploration(可自行 dimos mcp call 或 Web 控制台启动)",
    )
    args = parser.parse_args()

    # 根据参数确定robot_ip
    if args.simulation:
        robot_ip = "mujoco"
        print("🎮 使用MuJoCo仿真模式\n")
    elif args.replay:
        robot_ip = "replay"
        print("📼 使用回放模式\n")
    elif "ROBOT_IP" in os.environ:
        robot_ip = os.environ["ROBOT_IP"]
        print(f"🤖 连接到真实硬件: {robot_ip}\n")
    else:
        print("⚠️  警告: 未设置ROBOT_IP环境变量")
        print("   请使用以下方式之一:")
        print("   1. export ROBOT_IP=192.168.123.161  # 真实硬件")
        print("   2. python ... --simulation          # 仿真模式")
        print("   3. python ... --replay              # 回放模式")
        return

    print("=" * 70)
    print("🤖 Unitree Go2 纯自主探索与地图保存系统")
    print("=" * 70)
    print()
    print("系统架构:")
    print()
    print("  [地图层]")
    print("  - VoxelGridMapper: 点云->3D体素地图")
    print("  - CostMapper: 生成导航代价地图")
    print()
    print("  [导航层]")
    print("  - ReplanningAStarPlanner: A*路径规划")
    print("  - WavefrontFrontierExplorer: 前沿探索算法(自动选择目标)")
    print("  - PatrollingModule: 探索目标管理")
    print()
    print("  [地图保存]")
    print("  - MapSaverModule: 自动保存(每60秒)+ 停止时保存")
    print()
    print("  [MCP]")
    print(f"  - McpServer: http://127.0.0.1:{global_config.mcp_port}/mcp")
    print("  - 启动后自动 tools/call begin_exploration(可用 --no-mcp-auto-explore 关闭)")
    print()
    print("🎯 功能:")
    print("  • 完全自主探索未知区域(无需人工干预)")
    print("  • 实时构建3D体素地图和2D代价地图")
    print("  • 自动保存地图到 maps/ 目录")
    print("  • 格式: PGM + YAML (ROS标准格式)")
    print()
    print("📊 可视化:")
    print("  • Rerun: 实时3D可视化(地图,路径,前沿点)")
    print("  • 启动: 添加 --viewer rerun 参数")
    print()
    print("🛑 紧急停止:")
    print("  • Ctrl+C: 快速停止并保存地图")
    print("  • 遥控器: L2+R2 (硬件级停止)")
    print()
    print("=" * 70)
    print()
    print("正在启动系统...")
    print()

    # 创建并运行blueprint
    blueprint = create_exploration_blueprint(robot_ip=robot_ip)
    coordinator = ModuleCoordinator.build(blueprint)

    if not args.no_mcp_auto_explore:
        try:
            msg = _start_exploration_via_mcp()
            print(f"MCP begin_exploration: {msg}")
        except RuntimeError as e:
            print(f"错误: {e}")
            coordinator.stop()
            raise SystemExit(1) from e

    coordinator.loop()


if __name__ == "__main__":
    main()
