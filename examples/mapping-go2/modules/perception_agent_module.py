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

"""感知Agent模块 - 处理传感器数据并提供环境感知能力."""

import numpy as np
from typing import List, Dict, Optional

from dimos.core.core import rpc
from dimos.core.module import Module
from dimos.core.stream import In, Out
from dimos.msgs.sensor_msgs import Image, PointCloud2, Imu
from dimos.msgs.geometry_msgs import PoseStamped
from dimos.utils.logging import get_logger

logger = get_logger(__name__)


class ObstacleInfo:
    """障碍物信息."""

    def __init__(self, distance: float, angle: float, size: float = 0.0):
        self.distance = distance  # 距离（米）
        self.angle = angle  # 方位角（度）
        self.size = size  # 尺寸（米）


class PerceptionAgentModule(Module):
    """感知Agent模块.

    职责：
    - 处理相机、激光雷达、IMU数据
    - 实时障碍物检测与跟踪
    - 环境特征提取
    - 可通行区域识别
    - 感知质量评估
    """

    # 输入流
    color_image: In[Image]
    depth_image: In[Image]
    point_cloud: In[PointCloud2]
    imu: In[Imu]
    current_pose: In[PoseStamped]

    # 输出流（简化版，实际应使用自定义消息类型）
    # obstacles: Out[ObstacleArray]
    # traversable_area: Out[OccupancyGrid]
    # perception_status: Out[PerceptionStatus]

    def __init__(self) -> None:
        super().__init__()
        self._obstacles: List[ObstacleInfo] = []
        self._perception_quality = 1.0
        self._image_count = 0
        self._point_cloud_count = 0
        self._last_image_time = 0.0
        self._last_pc_time = 0.0

        # 感知参数
        self._min_obstacle_distance = 0.3  # 最小障碍物距离（米）
        self._max_detection_range = 5.0  # 最大检测范围（米）
        self._obstacle_threshold = 0.5  # 障碍物高度阈值（米）

    @rpc
    def start(self) -> None:
        """启动感知Agent."""
        super().start()

        # 订阅输入流
        self.color_image.subscribe(self._on_color_image)
        self.depth_image.subscribe(self._on_depth_image)
        self.point_cloud.subscribe(self._on_point_cloud)
        self.imu.subscribe(self._on_imu)
        self.current_pose.subscribe(self._on_pose)

        logger.info("PerceptionAgentModule started")

    @rpc
    def stop(self) -> None:
        """停止感知Agent."""
        super().stop()
        logger.info("PerceptionAgentModule stopped")

    def _on_color_image(self, image: Image) -> None:
        """处理RGB图像.

        Args:
            image: RGB图像数据
        """
        self._image_count += 1
        self._last_image_time = image.header.stamp

        # 实际实现会进行图像处理：
        # - 目标检测
        # - 特征提取
        # - 语义分割等

        if self._image_count % 30 == 0:  # 每秒记录一次（假设30fps）
            logger.debug(f"Processed {self._image_count} color images")

    def _on_depth_image(self, image: Image) -> None:
        """处理深度图像.

        Args:
            image: 深度图像数据
        """
        # 实际实现会进行深度处理：
        # - 障碍物检测
        # - 可通行区域识别
        # - 地面平面估计

        # 模拟障碍物检测
        self._detect_obstacles_from_depth(image)

    def _on_point_cloud(self, pc: PointCloud2) -> None:
        """处理点云数据.

        Args:
            pc: 点云数据
        """
        self._point_cloud_count += 1
        self._last_pc_time = pc.header.stamp

        # 实际实现会进行点云处理：
        # - 地面分割
        # - 障碍物聚类
        # - 3D特征提取

        # 模拟障碍物检测
        self._detect_obstacles_from_pointcloud(pc)

        if self._point_cloud_count % 10 == 0:  # 每秒记录一次（假设10Hz）
            logger.debug(f"Processed {self._point_cloud_count} point clouds")

    def _on_imu(self, imu: Imu) -> None:
        """处理IMU数据.

        Args:
            imu: IMU数据
        """
        # 实际实现会使用IMU数据：
        # - 姿态估计
        # - 运动补偿
        # - 稳定性检测
        pass

    def _on_pose(self, pose: PoseStamped) -> None:
        """处理位姿数据.

        Args:
            pose: 当前位姿
        """
        # 实际实现会使用位姿数据：
        # - 坐标变换
        # - 传感器融合
        pass

    def _detect_obstacles_from_depth(self, depth_image: Image) -> None:
        """从深度图像检测障碍物.

        Args:
            depth_image: 深度图像
        """
        # 实际实现会分析深度图像
        # 这里生成模拟数据
        obstacles = []

        # 模拟检测到的障碍物
        if np.random.random() > 0.7:  # 30%概率检测到障碍物
            num_obstacles = np.random.randint(1, 4)
            for _ in range(num_obstacles):
                distance = np.random.uniform(0.5, 3.0)
                angle = np.random.uniform(-45, 45)
                size = np.random.uniform(0.1, 0.5)
                obstacles.append(ObstacleInfo(distance, angle, size))

        self._obstacles = obstacles
        self._update_perception_quality()

    def _detect_obstacles_from_pointcloud(self, pc: PointCloud2) -> None:
        """从点云检测障碍物.

        Args:
            pc: 点云数据
        """
        # 实际实现会进行点云聚类和障碍物识别
        # 这里使用简化的模拟逻辑
        pass

    def _update_perception_quality(self) -> None:
        """更新感知质量评估."""
        # 实际实现会基于多个因素评估：
        # - 传感器数据质量
        # - 光照条件
        # - 遮挡情况
        # - 数据更新频率

        # 简化的质量评估
        quality = 1.0

        # 检查数据更新频率
        # 实际会检查时间戳

        # 检查障碍物检测置信度
        if len(self._obstacles) > 5:
            quality *= 0.9  # 障碍物过多可能影响质量

        self._perception_quality = max(0.0, min(1.0, quality))

    @rpc
    def get_obstacles(self) -> List[Dict[str, float]]:
        """获取当前检测到的障碍物列表.

        Returns:
            障碍物列表，每个包含distance、angle、size
        """
        return [
            {
                "distance": obs.distance,
                "angle": obs.angle,
                "size": obs.size,
            }
            for obs in self._obstacles
        ]

    @rpc
    def get_perception_quality(self) -> float:
        """获取当前感知质量.

        Returns:
            感知质量评分 (0.0-1.0)
        """
        return self._perception_quality

    @rpc
    def get_sensor_stats(self) -> Dict[str, int]:
        """获取传感器统计信息.

        Returns:
            传感器数据统计
        """
        return {
            "image_count": self._image_count,
            "point_cloud_count": self._point_cloud_count,
        }
