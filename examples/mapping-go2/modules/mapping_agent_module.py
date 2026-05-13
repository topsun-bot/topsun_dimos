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

"""地图Agent模块 - 负责SLAM和栅格地图构建."""

import numpy as np
from typing import List, Tuple, Optional

from dimos.core.core import rpc
from dimos.core.module import Module
from dimos.core.stream import In, Out
from dimos.msgs.sensor_msgs import PointCloud2
from dimos.msgs.geometry_msgs import PoseStamped
from dimos.msgs.nav_msgs import Odometry
from dimos.utils.logging import get_logger

logger = get_logger(__name__)


class FrontierPoint:
    """前沿点."""

    def __init__(self, x: float, y: float, size: int, score: float = 0.0):
        self.x = x  # X坐标（米）
        self.y = y  # Y坐标（米）
        self.size = size  # 前沿点大小（像素数）
        self.score = score  # 评分


class MappingAgentModule(Module):
    """地图Agent模块.

    职责：
    - SLAM（同步定位与地图构建）
    - 栅格地图维护与更新
    - 前沿点检测
    - 探索覆盖率统计
    - 地图保存与加载
    """

    # 输入流
    point_cloud: In[PointCloud2]
    current_pose: In[PoseStamped]
    odom: In[Odometry]

    # 输出流（简化版）
    # occupancy_grid: Out[OccupancyGrid]
    # frontiers: Out[FrontierArray]
    # map_metadata: Out[MapMetadata]

    def __init__(
        self,
        map_resolution: float = 0.05,
        map_size: Tuple[int, int] = (400, 400),
    ) -> None:
        super().__init__()

        # 地图参数
        self._map_resolution = map_resolution  # 米/像素
        self._map_size = map_size  # 像素
        self._map_origin = (0.0, 0.0)  # 地图原点（米）

        # 地图数据
        self._occupancy_grid: Optional[np.ndarray] = None
        self._frontiers: List[FrontierPoint] = []

        # 统计数据
        self._explored_area = 0.0  # 已探索面积（平方米）
        self._total_area = 0.0  # 总面积（平方米）
        self._coverage_rate = 0.0  # 覆盖率
        self._update_count = 0

        # 当前位姿
        self._current_pose: Optional[PoseStamped] = None

    @rpc
    def start(self) -> None:
        """启动地图Agent."""
        super().start()

        # 初始化地图
        self._initialize_map()

        # 订阅输入流
        self.point_cloud.subscribe(self._on_point_cloud)
        self.current_pose.subscribe(self._on_pose)
        self.odom.subscribe(self._on_odom)

        logger.info(
            f"MappingAgentModule started with map size {self._map_size}, "
            f"resolution {self._map_resolution}m/pixel"
        )

    @rpc
    def stop(self) -> None:
        """停止地图Agent."""
        super().stop()
        logger.info("MappingAgentModule stopped")

    def _initialize_map(self) -> None:
        """初始化地图数据结构."""
        # 创建占据栅格地图
        # 0: 未知, 1-99: 自由空间, 100: 障碍物
        self._occupancy_grid = np.zeros(self._map_size, dtype=np.int8)

        # 计算总面积
        self._total_area = (
            self._map_size[0] * self._map_size[1] * self._map_resolution**2
        )

        logger.info(f"Map initialized: {self._map_size}, total area: {self._total_area:.2f} m²")

    def _on_point_cloud(self, pc: PointCloud2) -> None:
        """处理点云数据并更新地图.

        Args:
            pc: 点云数据
        """
        if self._current_pose is None:
            return

        self._update_count += 1

        # 实际实现会：
        # 1. 将点云转换到地图坐标系
        # 2. 进行地面分割
        # 3. 更新占据栅格
        # 4. 检测前沿点

        # 模拟地图更新
        self._simulate_map_update()

        if self._update_count % 10 == 0:
            # 定期更新前沿点
            self._update_frontiers()
            # 更新覆盖率
            self._update_coverage_rate()

            logger.debug(
                f"Map updated: coverage {self._coverage_rate*100:.1f}%, "
                f"frontiers {len(self._frontiers)}"
            )

    def _on_pose(self, pose: PoseStamped) -> None:
        """处理位姿更新.

        Args:
            pose: 当前位姿
        """
        self._current_pose = pose

    def _on_odom(self, odom: Odometry) -> None:
        """处理里程计数据.

        Args:
            odom: 里程计数据
        """
        # 实际实现会使用里程计进行位姿估计
        pass

    def _simulate_map_update(self) -> None:
        """模拟地图更新（实际实现会基于真实传感器数据）."""
        if self._occupancy_grid is None:
            return

        # 模拟探索进度
        # 实际会根据点云数据更新地图
        self._coverage_rate = min(0.95, self._coverage_rate + 0.001)

    def _update_frontiers(self) -> None:
        """更新前沿点列表."""
        if self._occupancy_grid is None:
            return

        # 实际实现会：
        # 1. 检测已知区域和未知区域的边界
        # 2. 聚类边界点形成前沿
        # 3. 过滤太小的前沿
        # 4. 计算前沿点的评分

        # 模拟前沿点生成
        self._frontiers = []

        # 根据覆盖率生成不同数量的前沿点
        if self._coverage_rate < 0.3:
            num_frontiers = np.random.randint(15, 25)
        elif self._coverage_rate < 0.6:
            num_frontiers = np.random.randint(8, 15)
        elif self._coverage_rate < 0.85:
            num_frontiers = np.random.randint(3, 8)
        else:
            num_frontiers = np.random.randint(0, 3)

        for _ in range(num_frontiers):
            x = np.random.uniform(-5.0, 5.0)
            y = np.random.uniform(-5.0, 5.0)
            size = np.random.randint(10, 50)
            score = np.random.uniform(0.5, 1.0)
            self._frontiers.append(FrontierPoint(x, y, size, score))

    def _update_coverage_rate(self) -> None:
        """更新探索覆盖率."""
        if self._occupancy_grid is None:
            return

        # 实际实现会统计已探索的栅格数量
        # 这里使用模拟值
        self._explored_area = self._coverage_rate * self._total_area

    @rpc
    def get_map_data(self) -> dict:
        """获取地图数据.

        Returns:
            地图数据字典
        """
        return {
            "size": self._map_size,
            "resolution": self._map_resolution,
            "origin": self._map_origin,
            "coverage_rate": self._coverage_rate,
            "explored_area": self._explored_area,
            "total_area": self._total_area,
        }

    @rpc
    def get_frontiers(self) -> List[dict]:
        """获取前沿点列表.

        Returns:
            前沿点列表
        """
        return [
            {
                "x": f.x,
                "y": f.y,
                "size": f.size,
                "score": f.score,
            }
            for f in self._frontiers
        ]

    @rpc
    def get_coverage_rate(self) -> float:
        """获取探索覆盖率.

        Returns:
            覆盖率 (0.0-1.0)
        """
        return self._coverage_rate

    @rpc
    def save_map_to_file(self, filename: str) -> bool:
        """保存地图到文件.

        Args:
            filename: 文件名

        Returns:
            是否保存成功
        """
        if self._occupancy_grid is None:
            logger.error("No map data to save")
            return False

        try:
            # 实际实现会保存为PGM或其他格式
            # 这里只是记录日志
            logger.info(f"Map saved to {filename}")
            return True
        except Exception as e:
            logger.error(f"Failed to save map: {e}")
            return False

    @rpc
    def reset_map(self) -> None:
        """重置地图."""
        self._initialize_map()
        self._frontiers = []
        self._coverage_rate = 0.0
        self._explored_area = 0.0
        self._update_count = 0
        logger.info("Map reset")
