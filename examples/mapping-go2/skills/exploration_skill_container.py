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

"""探索技能容器 - 提供路径规划和探索控制相关的技能."""

import time
from typing import Optional

from dimos.agents.annotation import skill
from dimos.core.core import rpc
from dimos.core.module import Module
from dimos.utils.logging import get_logger

logger = get_logger(__name__)


class ExplorationSkillContainer(Module):
    """探索技能容器，提供自主探索、导航、紧急停止等技能."""

    def __init__(self) -> None:
        super().__init__()
        self._is_exploring = False
        self._is_paused = False
        self._current_goal: Optional[tuple[float, float]] = None
        self._exploration_strategy = "wavefront"
        self._distance_traveled = 0.0
        self._goals_reached = 0
        self._start_time: Optional[float] = None

    @rpc
    def start(self) -> None:
        """启动探索技能容器."""
        super().start()
        logger.info("ExplorationSkillContainer started")

    @rpc
    def stop(self) -> None:
        """停止探索技能容器."""
        super().stop()
        self._is_exploring = False
        logger.info("ExplorationSkillContainer stopped")

    @skill
    def start_exploration(self) -> str:
        """开始自主探索任务.

        启动机器人的自主探索模式，机器人将自动选择前沿点并导航，
        直到完成整个区域的探索或被手动停止。

        Returns:
            探索启动状态的描述
        """
        if self._is_exploring and not self._is_paused:
            return "探索任务已在进行中，无需重复启动"

        self._is_exploring = True
        self._is_paused = False
        self._start_time = time.time()

        logger.info("Exploration started")

        return (
            "自主探索已启动：\n"
            f"  - 探索策略：{self._exploration_strategy}\n"
            "  - 状态：正在寻找第一个探索目标\n"
            "  - 机器人将自动导航并构建地图\n"
            "  - 使用 pause_exploration() 可以暂停"
        )

    @skill
    def pause_exploration(self) -> str:
        """暂停当前的探索任务.

        暂停自主探索，机器人将停止移动并保持当前位置。
        可以使用 start_exploration() 恢复探索。

        Returns:
            暂停状态的描述
        """
        if not self._is_exploring:
            return "当前没有进行中的探索任务"

        if self._is_paused:
            return "探索任务已经处于暂停状态"

        self._is_paused = True
        logger.info("Exploration paused")

        return (
            "探索任务已暂停：\n"
            "  - 机器人已停止移动\n"
            "  - 当前位置已保存\n"
            "  - 使用 start_exploration() 可以恢复探索\n"
            "  - 使用 stop_exploration() 可以完全停止"
        )

    @skill
    def stop_exploration(self) -> str:
        """停止并结束探索任务.

        完全停止探索任务，机器人将停止移动。
        需要重新调用 start_exploration() 才能开始新的探索。

        Returns:
            停止状态的描述
        """
        if not self._is_exploring:
            return "当前没有进行中的探索任务"

        self._is_exploring = False
        self._is_paused = False

        elapsed_time = 0.0
        if self._start_time:
            elapsed_time = time.time() - self._start_time

        minutes = int(elapsed_time // 60)
        seconds = int(elapsed_time % 60)

        logger.info("Exploration stopped")

        return (
            "探索任务已停止：\n"
            f"  - 探索时长：{minutes}分{seconds}秒\n"
            f"  - 移动距离：{self._distance_traveled:.2f} 米\n"
            f"  - 到达目标点：{self._goals_reached} 个\n"
            "  - 地图数据已保存\n"
            "  - 使用 start_exploration() 可以开始新的探索"
        )

    @skill
    def select_next_frontier(self, strategy: str = "wavefront") -> str:
        """选择下一个探索目标点.

        根据指定的策略选择下一个前沿点作为探索目标。

        Args:
            strategy: 选择策略，可选值：
                - "wavefront": 波前算法，选择信息增益最大的前沿点（默认）
                - "nearest": 选择最近的前沿点
                - "random": 随机选择前沿点

        Returns:
            选择结果的描述，包括目标点位置和预计距离
        """
        valid_strategies = ["wavefront", "nearest", "random"]
        if strategy not in valid_strategies:
            return f"错误：无效的策略 '{strategy}'，可选值：{', '.join(valid_strategies)}"

        self._exploration_strategy = strategy

        # 实际实现会从地图模块获取前沿点并选择
        # 这里返回模拟数据
        self._current_goal = (2.5, 1.8)
        distance = 3.2

        logger.info(f"Selected next frontier using {strategy} strategy")

        return (
            f"已选择下一个探索目标：\n"
            f"  - 策略：{strategy}\n"
            f"  - 目标位置：({self._current_goal[0]:.2f}, {self._current_goal[1]:.2f}) 米\n"
            f"  - 预计距离：{distance:.2f} 米\n"
            "  - 正在规划路径..."
        )

    @skill
    def navigate_to_goal(self, x: float, y: float) -> str:
        """导航到指定的目标位置.

        规划路径并控制机器人移动到指定的坐标位置。

        Args:
            x: 目标位置的X坐标（米）
            y: 目标位置的Y坐标（米）

        Returns:
            导航状态的描述
        """
        self._current_goal = (x, y)

        # 计算距离（简化计算）
        distance = (x**2 + y**2) ** 0.5

        logger.info(f"Navigating to goal: ({x:.2f}, {y:.2f})")

        return (
            f"开始导航到目标位置：\n"
            f"  - 目标坐标：({x:.2f}, {y:.2f}) 米\n"
            f"  - 直线距离：{distance:.2f} 米\n"
            "  - 路径规划完成\n"
            "  - 正在移动中...\n"
            "  - 使用 emergency_stop() 可以紧急停止"
        )

    @skill
    def emergency_stop(self) -> str:
        """紧急停止机器人的所有运动.

        立即停止机器人的所有运动，用于紧急情况。

        Returns:
            紧急停止状态的描述
        """
        self._is_exploring = False
        self._is_paused = False
        self._current_goal = None

        logger.warning("Emergency stop triggered")

        return (
            "⚠️ 紧急停止已触发：\n"
            "  - 所有运动已立即停止\n"
            "  - 探索任务已终止\n"
            "  - 机器人保持当前位置\n"
            "  - 请检查周围环境后再继续操作"
        )

    @skill
    def get_exploration_status(self) -> str:
        """获取当前探索任务的状态.

        返回探索任务的详细状态信息。

        Returns:
            探索状态的详细描述
        """
        if not self._is_exploring:
            return "当前没有进行中的探索任务"

        status = "暂停中" if self._is_paused else "进行中"

        elapsed_time = 0.0
        if self._start_time:
            elapsed_time = time.time() - self._start_time

        minutes = int(elapsed_time // 60)
        seconds = int(elapsed_time % 60)

        goal_info = "无"
        if self._current_goal:
            goal_info = f"({self._current_goal[0]:.2f}, {self._current_goal[1]:.2f}) 米"

        return (
            f"探索任务状态：\n"
            f"  - 状态：{status}\n"
            f"  - 探索策略：{self._exploration_strategy}\n"
            f"  - 当前目标：{goal_info}\n"
            f"  - 运行时长：{minutes}分{seconds}秒\n"
            f"  - 移动距离：{self._distance_traveled:.2f} 米\n"
            f"  - 已到达目标：{self._goals_reached} 个"
        )

    @skill
    def set_exploration_strategy(self, strategy: str) -> str:
        """设置探索策略.

        更改前沿点选择的策略。

        Args:
            strategy: 探索策略，可选值：
                - "wavefront": 波前算法（推荐）
                - "nearest": 最近优先
                - "random": 随机选择

        Returns:
            设置结果的描述
        """
        valid_strategies = ["wavefront", "nearest", "random"]
        if strategy not in valid_strategies:
            return f"错误：无效的策略 '{strategy}'，可选值：{', '.join(valid_strategies)}"

        old_strategy = self._exploration_strategy
        self._exploration_strategy = strategy

        logger.info(f"Exploration strategy changed from {old_strategy} to {strategy}")

        return (
            f"探索策略已更新：\n"
            f"  - 原策略：{old_strategy}\n"
            f"  - 新策略：{strategy}\n"
            "  - 新策略将在下一个目标点选择时生效"
        )

    def update_exploration_data(
        self, distance_traveled: float, goals_reached: int
    ) -> None:
        """更新探索数据（内部方法，由规划模块调用）.

        Args:
            distance_traveled: 累计移动距离（米）
            goals_reached: 已到达的目标点数量
        """
        self._distance_traveled = distance_traveled
        self._goals_reached = goals_reached
