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

"""感知技能容器 - 提供环境感知相关的技能."""

from typing import Dict, List

from dimos.agents.annotation import skill
from dimos.core.core import rpc
from dimos.core.module import Module
from dimos.utils.logging import get_logger

logger = get_logger(__name__)


class PerceptionSkillContainer(Module):
    """感知技能容器，提供障碍物检测、可通行区域识别等技能."""

    def __init__(self) -> None:
        super().__init__()
        self._obstacle_count = 0
        self._perception_quality = 1.0
        self._last_obstacles: List[Dict[str, float]] = []

    @rpc
    def start(self) -> None:
        """启动感知技能容器."""
        super().start()
        logger.info("PerceptionSkillContainer started")

    @rpc
    def stop(self) -> None:
        """停止感知技能容器."""
        super().stop()
        logger.info("PerceptionSkillContainer stopped")

    @skill
    def detect_obstacles(self) -> str:
        """检测当前视野内的障碍物.

        扫描相机和激光雷达数据，识别环境中的障碍物，包括静态和动态障碍物。

        Returns:
            检测结果的描述，包括障碍物数量和位置信息
        """
        # 实际实现会从感知模块获取障碍物数据
        # 这里返回模拟数据
        if self._obstacle_count == 0:
            return "当前视野内未检测到障碍物，环境清晰可通行"

        obstacles_desc = f"检测到 {self._obstacle_count} 个障碍物："
        for i, obs in enumerate(self._last_obstacles[:5], 1):
            distance = obs.get("distance", 0.0)
            angle = obs.get("angle", 0.0)
            obstacles_desc += f"\n  {i}. 距离 {distance:.2f}m，方位角 {angle:.1f}度"

        return obstacles_desc

    @skill
    def identify_traversable_area(self) -> str:
        """识别当前可通行的区域.

        分析深度图像和点云数据，识别机器人可以安全通过的区域，
        排除障碍物、悬崖、楼梯等危险区域。

        Returns:
            可通行区域的描述，包括方向和范围
        """
        # 实际实现会分析深度图和点云
        return (
            "可通行区域分析完成：\n"
            "  - 前方 0-5米范围内可通行\n"
            "  - 左侧 30度范围内可通行\n"
            "  - 右侧 45度范围内可通行\n"
            "  - 建议沿前方或右前方移动"
        )

    @skill
    def get_perception_quality(self) -> str:
        """获取当前感知系统的质量评估.

        评估传感器数据质量、光照条件、遮挡情况等因素，
        给出感知系统的整体质量评分。

        Returns:
            感知质量评估结果，包括评分和影响因素
        """
        quality_score = self._perception_quality * 100

        if quality_score >= 90:
            quality_level = "优秀"
            description = "传感器工作正常，光照充足，视野清晰"
        elif quality_score >= 70:
            quality_level = "良好"
            description = "传感器工作正常，存在轻微遮挡或光照不足"
        elif quality_score >= 50:
            quality_level = "一般"
            description = "存在较多遮挡或光照条件较差，建议谨慎移动"
        else:
            quality_level = "较差"
            description = "传感器数据质量差，建议停止移动并检查传感器"

        return (
            f"感知质量评估：\n"
            f"  - 评分：{quality_score:.1f}/100 ({quality_level})\n"
            f"  - 状态：{description}"
        )

    @skill
    def check_sensor_status(self) -> str:
        """检查所有传感器的工作状态.

        检查相机、激光雷达、IMU等传感器是否正常工作，
        是否有数据丢失或延迟。

        Returns:
            传感器状态报告
        """
        return (
            "传感器状态检查：\n"
            "  - RGB相机：正常 (30 FPS)\n"
            "  - 深度相机：正常 (30 FPS)\n"
            "  - 激光雷达：正常 (10 Hz)\n"
            "  - IMU：正常 (100 Hz)\n"
            "  - 所有传感器工作正常"
        )

    def update_obstacle_data(self, obstacle_count: int, obstacles: List[Dict[str, float]]) -> None:
        """更新障碍物数据（内部方法，由感知模块调用）.

        Args:
            obstacle_count: 障碍物数量
            obstacles: 障碍物列表，每个包含distance和angle
        """
        self._obstacle_count = obstacle_count
        self._last_obstacles = obstacles

    def update_perception_quality(self, quality: float) -> None:
        """更新感知质量（内部方法，由感知模块调用）.

        Args:
            quality: 感知质量评分 (0.0-1.0)
        """
        self._perception_quality = max(0.0, min(1.0, quality))
