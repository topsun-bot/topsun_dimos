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

"""规划控制Agent模块 - 负责路径规划、前沿探索和运动控制."""

import math
import time
from typing import Optional, List, Tuple

from dimos.core.core import rpc
from dimos.core.module import Module
from dimos.core.stream import In, Out
from dimos.msgs.geometry_msgs import Twist, PoseStamped
from dimos.utils.logging import get_logger

logger = get_logger(__name__)


class PlanningControlAgentModule(Module):
    """规划控制Agent模块.

    职责：
    - 前沿探索策略
    - 路径规划（A*等算法）
    - 运动控制与避障
    - 探索进度跟踪
    """

    # 输入流
    current_pose: In[PoseStamped]
    # occupancy_grid: In[OccupancyGrid]
    # obstacles: In[ObstacleArray]
    # frontiers: In[FrontierArray]

    # 输出流
    cmd_vel: Out[Twist]
    goal_pose: Out[PoseStamped]
    # path: Out[Path]
    # exploration_status: Out[ExplorationStatus]

    def __init__(self) -> None:
        super().__init__()

        # 探索状态
        self._is_exploring = False
        self._is_paused = False
        self._exploration_strategy = "wavefront"

        # 当前目标
        self._current_goal: Optional[Tuple[float, float]] = None
        self._goal_reached = False

        # 统计数据
        self._distance_traveled = 0.0
        self._goals_reached = 0
        self._start_time: Optional[float] = None

        # 控制参数
        self._max_linear_vel = 0.5  # 最大线速度（m/s）
        self._max_angular_vel = 1.0  # 最大角速度（rad/s）
        self._goal_tolerance = 0.3  # 目标容差（米）

        # 当前位姿
        self._current_pose: Optional[PoseStamped] = None
        self._last_pose: Optional[PoseStamped] = None

    @rpc
    def start(self) -> None:
        """启动规划控制Agent."""
        super().start()

        # 订阅输入流
        self.current_pose.subscribe(self._on_pose)

        logger.info("PlanningControlAgentModule started")

    @rpc
    def stop(self) -> None:
        """停止规划控制Agent."""
        super().stop()
        self._stop_robot()
        logger.info("PlanningControlAgentModule stopped")

    def _on_pose(self, pose: PoseStamped) -> None:
        """处理位姿更新.

        Args:
            pose: 当前位姿
        """
        self._last_pose = self._current_pose
        self._current_pose = pose

        # 更新移动距离
        if self._last_pose is not None and self._is_exploring:
            dx = pose.pose.position.x - self._last_pose.pose.position.x
            dy = pose.pose.position.y - self._last_pose.pose.position.y
            distance = math.sqrt(dx**2 + dy**2)
            self._distance_traveled += distance

        # 检查是否到达目标
        if self._current_goal is not None:
            self._check_goal_reached()

        # 如果正在探索且未暂停，执行控制循环
        if self._is_exploring and not self._is_paused:
            self._exploration_control_loop()

    def _exploration_control_loop(self) -> None:
        """探索控制循环."""
        if self._current_goal is None:
            # 没有目标，选择新的前沿点
            self._select_next_frontier()
        elif self._goal_reached:
            # 到达目标，选择下一个
            logger.info(f"Goal reached: {self._current_goal}")
            self._goals_reached += 1
            self._current_goal = None
            self._goal_reached = False
        else:
            # 向目标移动
            self._move_towards_goal()

    def _select_next_frontier(self) -> None:
        """选择下一个前沿点作为目标."""
        # 实际实现会从地图模块获取前沿点列表
        # 根据策略选择最佳前沿点

        # 模拟选择前沿点
        import random
        x = random.uniform(-3.0, 3.0)
        y = random.uniform(-3.0, 3.0)
        self._current_goal = (x, y)
        self._goal_reached = False

        logger.info(f"Selected new frontier: ({x:.2f}, {y:.2f})")

        # 发布目标位姿
        goal = PoseStamped()
        goal.header.stamp = time.time()
        goal.header.frame_id = "map"
        goal.pose.position.x = x
        goal.pose.position.y = y
        goal.pose.position.z = 0.0
        self.goal_pose.publish(goal)

    def _move_towards_goal(self) -> None:
        """向目标移动."""
        if self._current_pose is None or self._current_goal is None:
            return

        # 计算到目标的距离和角度
        dx = self._current_goal[0] - self._current_pose.pose.position.x
        dy = self._current_goal[1] - self._current_pose.pose.position.y
        distance = math.sqrt(dx**2 + dy**2)
        angle_to_goal = math.atan2(dy, dx)

        # 获取当前朝向（简化，实际需要从四元数转换）
        current_yaw = 0.0  # 实际应从pose.orientation计算

        # 计算角度差
        angle_diff = angle_to_goal - current_yaw
        # 归一化到[-pi, pi]
        while angle_diff > math.pi:
            angle_diff -= 2 * math.pi
        while angle_diff < -math.pi:
            angle_diff += 2 * math.pi

        # 生成控制指令
        cmd = Twist()

        # 简单的比例控制
        if abs(angle_diff) > 0.2:  # 需要转向
            cmd.linear.x = 0.1  # 慢速前进
            cmd.angular.z = max(
                -self._max_angular_vel,
                min(self._max_angular_vel, angle_diff * 2.0)
            )
        else:  # 朝向正确，前进
            cmd.linear.x = min(self._max_linear_vel, distance * 0.5)
            cmd.angular.z = angle_diff * 0.5

        # 发布控制指令
        self.cmd_vel.publish(cmd)

    def _check_goal_reached(self) -> None:
        """检查是否到达目标."""
        if self._current_pose is None or self._current_goal is None:
            return

        dx = self._current_goal[0] - self._current_pose.pose.position.x
        dy = self._current_goal[1] - self._current_pose.pose.position.y
        distance = math.sqrt(dx**2 + dy**2)

        if distance < self._goal_tolerance:
            self._goal_reached = True
            self._stop_robot()

    def _stop_robot(self) -> None:
        """停止机器人运动."""
        cmd = Twist()
        cmd.linear.x = 0.0
        cmd.angular.z = 0.0
        self.cmd_vel.publish(cmd)

    @rpc
    def start_exploration(self) -> bool:
        """开始自主探索.

        Returns:
            是否成功启动
        """
        if self._is_exploring and not self._is_paused:
            logger.warning("Exploration already running")
            return False

        self._is_exploring = True
        self._is_paused = False
        self._start_time = time.time()

        logger.info("Exploration started")
        return True

    @rpc
    def pause_exploration(self) -> bool:
        """暂停探索.

        Returns:
            是否成功暂停
        """
        if not self._is_exploring:
            logger.warning("No exploration to pause")
            return False

        if self._is_paused:
            logger.warning("Exploration already paused")
            return False

        self._is_paused = True
        self._stop_robot()

        logger.info("Exploration paused")
        return True

    @rpc
    def stop_exploration(self) -> bool:
        """停止探索.

        Returns:
            是否成功停止
        """
        if not self._is_exploring:
            logger.warning("No exploration to stop")
            return False

        self._is_exploring = False
        self._is_paused = False
        self._current_goal = None
        self._stop_robot()

        logger.info("Exploration stopped")
        return True

    @rpc
    def emergency_stop(self) -> None:
        """紧急停止."""
        self._is_exploring = False
        self._is_paused = False
        self._current_goal = None
        self._stop_robot()

        logger.warning("Emergency stop triggered")

    @rpc
    def navigate_to_goal(self, x: float, y: float) -> bool:
        """导航到指定目标.

        Args:
            x: 目标X坐标（米）
            y: 目标Y坐标（米）

        Returns:
            是否成功设置目标
        """
        self._current_goal = (x, y)
        self._goal_reached = False

        logger.info(f"Navigating to goal: ({x:.2f}, {y:.2f})")
        return True

    @rpc
    def set_exploration_strategy(self, strategy: str) -> bool:
        """设置探索策略.

        Args:
            strategy: 策略名称 (wavefront/nearest/random)

        Returns:
            是否成功设置
        """
        valid_strategies = ["wavefront", "nearest", "random"]
        if strategy not in valid_strategies:
            logger.error(f"Invalid strategy: {strategy}")
            return False

        self._exploration_strategy = strategy
        logger.info(f"Exploration strategy set to: {strategy}")
        return True

    @rpc
    def get_exploration_stats(self) -> dict:
        """获取探索统计数据.

        Returns:
            统计数据字典
        """
        elapsed_time = 0.0
        if self._start_time is not None:
            elapsed_time = time.time() - self._start_time

        return {
            "is_exploring": self._is_exploring,
            "is_paused": self._is_paused,
            "strategy": self._exploration_strategy,
            "current_goal": self._current_goal,
            "distance_traveled": self._distance_traveled,
            "goals_reached": self._goals_reached,
            "elapsed_time": elapsed_time,
        }
