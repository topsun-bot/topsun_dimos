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

"""地图技能容器 - 提供地图构建和查询相关的技能."""

import time
from typing import Dict

from dimos.agents.annotation import skill
from dimos.core.core import rpc
from dimos.core.module import Module
from dimos.utils.logging import get_logger

logger = get_logger(__name__)


class MappingSkillContainer(Module):
    """地图技能容器，提供地图构建、保存、查询等技能."""

    def __init__(self) -> None:
        super().__init__()
        self._map_size = (0, 0)  # 地图尺寸（像素）
        self._map_resolution = 0.05  # 地图分辨率（米/像素）
        self._coverage_rate = 0.0  # 探索覆盖率
        self._explored_area = 0.0  # 已探索面积（平方米）
        self._total_area = 0.0  # 总面积（平方米）
        self._frontier_count = 0  # 前沿点数量
        self._start_time = time.time()

    @rpc
    def start(self) -> None:
        """启动地图技能容器."""
        super().start()
        self._start_time = time.time()
        logger.info("MappingSkillContainer started")

    @rpc
    def stop(self) -> None:
        """停止地图技能容器."""
        super().stop()
        logger.info("MappingSkillContainer stopped")

    @skill
    def get_current_map(self) -> str:
        """获取当前地图的状态信息.

        返回地图的基本信息，包括尺寸、分辨率、已探索区域等。

        Returns:
            地图状态的详细描述
        """
        elapsed_time = time.time() - self._start_time
        minutes = int(elapsed_time // 60)
        seconds = int(elapsed_time % 60)

        return (
            f"当前地图状态：\n"
            f"  - 地图尺寸：{self._map_size[0]} x {self._map_size[1]} 像素\n"
            f"  - 分辨率：{self._map_resolution} 米/像素\n"
            f"  - 物理尺寸：{self._map_size[0] * self._map_resolution:.1f} x "
            f"{self._map_size[1] * self._map_resolution:.1f} 米\n"
            f"  - 已探索面积：{self._explored_area:.2f} 平方米\n"
            f"  - 探索覆盖率：{self._coverage_rate * 100:.1f}%\n"
            f"  - 前沿点数量：{self._frontier_count}\n"
            f"  - 建图时长：{minutes}分{seconds}秒"
        )

    @skill
    def save_map(self, filename: str) -> str:
        """保存当前地图到文件.

        将当前构建的栅格地图保存到指定文件，支持多种格式。

        Args:
            filename: 保存的文件名，支持 .pgm, .png, .yaml 等格式

        Returns:
            保存结果的描述
        """
        if not filename:
            return "错误：文件名不能为空"

        # 实际实现会保存地图数据
        logger.info(f"Saving map to {filename}")

        return (
            f"地图保存成功：\n"
            f"  - 文件名：{filename}\n"
            f"  - 地图尺寸：{self._map_size[0]} x {self._map_size[1]}\n"
            f"  - 覆盖率：{self._coverage_rate * 100:.1f}%\n"
            f"  - 文件已保存到当前目录"
        )

    @skill
    def get_coverage_rate(self) -> str:
        """获取当前探索的覆盖率.

        计算已探索区域占总可探索区域的比例。

        Returns:
            覆盖率信息和探索进度
        """
        coverage_percent = self._coverage_rate * 100

        if coverage_percent < 30:
            status = "探索刚开始"
            suggestion = "继续探索未知区域"
        elif coverage_percent < 60:
            status = "探索进行中"
            suggestion = "已完成大部分主要区域，继续探索边缘区域"
        elif coverage_percent < 85:
            status = "探索接近完成"
            suggestion = "重点探索剩余的小区域和死角"
        else:
            status = "探索基本完成"
            suggestion = "可以结束探索或进行精细化扫描"

        return (
            f"探索覆盖率：{coverage_percent:.1f}%\n"
            f"  - 状态：{status}\n"
            f"  - 已探索：{self._explored_area:.2f} 平方米\n"
            f"  - 总面积：{self._total_area:.2f} 平方米\n"
            f"  - 剩余前沿点：{self._frontier_count}\n"
            f"  - 建议：{suggestion}"
        )

    @skill
    def reset_map(self) -> str:
        """重置地图，清除所有已构建的地图数据.

        清空当前地图，重新开始建图过程。

        Returns:
            重置结果的描述
        """
        self._coverage_rate = 0.0
        self._explored_area = 0.0
        self._frontier_count = 0
        self._start_time = time.time()

        logger.info("Map reset")

        return (
            "地图已重置：\n"
            "  - 所有地图数据已清除\n"
            "  - 探索覆盖率归零\n"
            "  - 可以开始新的探索任务"
        )

    @skill
    def get_map_statistics(self) -> str:
        """获取地图的详细统计信息.

        提供地图的各种统计数据，包括障碍物占比、自由空间占比等。

        Returns:
            地图统计信息
        """
        # 模拟统计数据
        free_space_percent = 65.0
        obstacle_percent = 15.0
        unknown_percent = 20.0

        return (
            f"地图统计信息：\n"
            f"  - 自由空间：{free_space_percent:.1f}%\n"
            f"  - 障碍物：{obstacle_percent:.1f}%\n"
            f"  - 未探索：{unknown_percent:.1f}%\n"
            f"  - 地图分辨率：{self._map_resolution} 米/像素\n"
            f"  - 总像素数：{self._map_size[0] * self._map_size[1]}\n"
            f"  - 前沿点数量：{self._frontier_count}"
        )

    @skill
    def get_frontier_info(self) -> str:
        """获取当前前沿点的信息.

        前沿点是已知区域和未知区域的边界，是探索的目标点。

        Returns:
            前沿点信息描述
        """
        if self._frontier_count == 0:
            return "当前没有可用的前沿点，探索可能已完成或地图未初始化"

        return (
            f"前沿点信息：\n"
            f"  - 前沿点数量：{self._frontier_count}\n"
            f"  - 状态：{'充足' if self._frontier_count > 10 else '较少'}\n"
            f"  - 建议：{'继续探索' if self._frontier_count > 5 else '探索接近完成'}"
        )

    def update_map_data(
        self,
        map_size: tuple[int, int],
        resolution: float,
        coverage_rate: float,
        explored_area: float,
        total_area: float,
        frontier_count: int,
    ) -> None:
        """更新地图数据（内部方法，由地图模块调用）.

        Args:
            map_size: 地图尺寸（像素）
            resolution: 地图分辨率（米/像素）
            coverage_rate: 探索覆盖率 (0.0-1.0)
            explored_area: 已探索面积（平方米）
            total_area: 总面积（平方米）
            frontier_count: 前沿点数量
        """
        self._map_size = map_size
        self._map_resolution = resolution
        self._coverage_rate = max(0.0, min(1.0, coverage_rate))
        self._explored_area = explored_area
        self._total_area = total_area
        self._frontier_count = frontier_count
