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

这个blueprint组装了完整的多Agent系统，包括：
- Perception Agent: 环境感知
- Mapping Agent: 地图构建
- Planning & Control Agent: 路径规划与控制
- Review Agent: 系统监控
- 三个技能容器: 为LLM提供技能接口
- MCP Server & Client: Agent通信协议
"""

from dimos.agents.mcp.mcp_client import McpClient
from dimos.agents.mcp.mcp_server import McpServer
from dimos.core.coordination.blueprints import autoconnect
from dimos.core.transport import SHMTransport, LCMTransport
from dimos.msgs.sensor_msgs import Image, PointCloud2
from dimos.robot.unitree.go2.connection import go2_connection

# 导入我们实现的模块
from examples.mapping_go2.modules.perception_agent_module import PerceptionAgentModule
from examples.mapping_go2.modules.mapping_agent_module import MappingAgentModule
from examples.mapping_go2.modules.planning_control_agent_module import (
    PlanningControlAgentModule,
)
from examples.mapping_go2.modules.review_agent_module import ReviewAgentModule

# 导入技能容器
from examples.mapping_go2.skills.perception_skill_container import (
    PerceptionSkillContainer,
)
from examples.mapping_go2.skills.mapping_skill_container import MappingSkillContainer
from examples.mapping_go2.skills.exploration_skill_container import (
    ExplorationSkillContainer,
)

# 导入系统提示词
from examples.mapping_go2.system_prompts.exploration_system_prompt import (
    EXPLORATION_SYSTEM_PROMPT,
)


# 组装完整的blueprint
go2_autonomous_exploration = (
    autoconnect(
        # 1. Go2硬件连接（提供传感器数据和控制接口）
        go2_connection(),
        # 2. Agent模块
        PerceptionAgentModule.blueprint(),
        MappingAgentModule.blueprint(),
        PlanningControlAgentModule.blueprint(),
        ReviewAgentModule.blueprint(),
        # 3. 技能容器（为LLM提供可调用的技能）
        PerceptionSkillContainer.blueprint(),
        MappingSkillContainer.blueprint(),
        ExplorationSkillContainer.blueprint(),
        # 4. MCP Server（暴露技能为MCP工具）
        McpServer.blueprint(),
        # 5. MCP Client（LLM Agent，使用自定义系统提示词）
        McpClient.blueprint(system_prompt=EXPLORATION_SYSTEM_PROMPT),
    )
    .transports(
        {
            # 使用共享内存传输图像数据（高效）
            ("color_image", Image): SHMTransport("/go2/color_image", Image),
            ("depth_image", Image): SHMTransport("/go2/depth_image", Image),
            # 使用共享内存传输点云数据（高效）
            ("point_cloud", PointCloud2): SHMTransport("/go2/point_cloud", PointCloud2),
            # 控制指令使用LCM（低延迟）
            # cmd_vel 默认使用LCM
        }
    )
    .global_config(
        n_workers=8,  # 使用8个worker进程
    )
)


def main() -> None:
    """主函数 - 构建并运行blueprint."""
    print("=" * 60)
    print("Unitree Go2 自主探索与地图绘制系统")
    print("=" * 60)
    print()
    print("系统架构：")
    print("  - Perception Agent: 环境感知与障碍物检测")
    print("  - Mapping Agent: SLAM与栅格地图构建")
    print("  - Planning & Control Agent: 路径规划与运动控制")
    print("  - Review Agent: 系统监控与性能评估")
    print()
    print("可用技能：")
    print("  感知: detect_obstacles, identify_traversable_area, ...")
    print("  地图: get_current_map, save_map, get_coverage_rate, ...")
    print("  探索: start_exploration, navigate_to_goal, emergency_stop, ...")
    print("  审查: get_exploration_progress, get_system_health, ...")
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
    print("  - 调用技能: dimos mcp call start_exploration")
    print("  - 发送命令: dimos agent-send 'start exploration'")
    print()
    print("=" * 60)
    print()
    print("正在启动系统...")
    print()

    # 构建并运行blueprint
    # build() 部署所有模块到worker进程并连接数据流
    # loop() 阻塞主线程直到收到停止信号（Ctrl-C或SIGTERM）
    go2_autonomous_exploration.build().loop()


if __name__ == "__main__":
    main()
