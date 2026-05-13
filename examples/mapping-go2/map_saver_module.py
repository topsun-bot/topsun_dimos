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

"""地图保存模块 - 定期保存和手动保存地图."""

import os
import time
from pathlib import Path
from typing import Optional

import numpy as np

from dimos.agents.annotation import skill
from dimos.core.core import rpc
from dimos.core.module import Module, ModuleConfig
from dimos.core.stream import In
from dimos.msgs.nav_msgs.OccupancyGrid import OccupancyGrid
from dimos.utils.logging_config import setup_logger

logger = setup_logger()


class MapSaverConfig(ModuleConfig):
    """地图保存模块配置."""

    save_dir: str = "maps"
    auto_save_interval: float = 60.0
    enable_auto_save: bool = True


class MapSaverModule(Module):
    """地图保存模块.

    功能：
    - 定期自动保存地图
    - 提供手动保存技能
    - 支持多种格式（PGM、PNG、NPY）
    - 保存地图元数据
    """

    config: MapSaverConfig
    # 输入流
    global_costmap: In[OccupancyGrid]

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)

        self._save_dir = Path(self.config.save_dir)
        self._auto_save_interval = self.config.auto_save_interval
        self._enable_auto_save = self.config.enable_auto_save

        self._last_map: Optional[OccupancyGrid] = None
        self._last_save_time = 0.0
        self._save_count = 0

        # 创建保存目录
        self._save_dir.mkdir(parents=True, exist_ok=True)

    @rpc
    def start(self) -> None:
        """启动地图保存模块."""
        super().start()

        # 订阅地图数据
        self.global_costmap.subscribe(self._on_map_update)

        logger.info(
            f"MapSaverModule started: save_dir={self._save_dir}, "
            f"auto_save={self._enable_auto_save}, interval={self._auto_save_interval}s"
        )

    @rpc
    def stop(self) -> None:
        """停止地图保存模块."""
        # 停止时保存最后一次地图
        if self._last_map is not None:
            self._save_map_internal(self._last_map, "final")
            logger.info("Saved final map on shutdown")

        super().stop()

    def _on_map_update(self, map_data: OccupancyGrid) -> None:
        """处理地图更新.

        Args:
            map_data: 占据栅格地图
        """
        self._last_map = map_data

        # 检查是否需要自动保存
        if self._enable_auto_save:
            current_time = time.time()
            if current_time - self._last_save_time >= self._auto_save_interval:
                self._save_map_internal(map_data, "auto")
                self._last_save_time = current_time

    def _save_map_internal(
        self, map_data: OccupancyGrid, prefix: str = "map"
    ) -> str:
        """内部保存地图方法.

        Args:
            map_data: 地图数据
            prefix: 文件名前缀

        Returns:
            保存的文件路径
        """
        timestamp = time.strftime("%Y%m%d_%H%M%S")
        base_name = f"{prefix}_{timestamp}"

        # 保存为PGM格式（ROS标准格式）
        pgm_path = self._save_dir / f"{base_name}.pgm"
        yaml_path = self._save_dir / f"{base_name}.yaml"

        try:
            # 转换地图数据为图像格式
            width = map_data.info.width
            height = map_data.info.height
            data = np.array(map_data.data).reshape((height, width))

            # 转换为0-255范围（PGM格式）
            # -1 (未知) -> 205
            # 0 (自由) -> 254
            # 100 (障碍) -> 0
            image_data = np.zeros_like(data, dtype=np.uint8)
            image_data[data == -1] = 205  # 未知区域
            image_data[data == 0] = 254  # 自由空间
            image_data[data == 100] = 0  # 障碍物

            # 保存PGM文件
            with open(pgm_path, "wb") as f:
                f.write(f"P5\n{width} {height}\n255\n".encode())
                f.write(image_data.tobytes())

            # 保存YAML元数据
            with open(yaml_path, "w") as f:
                f.write(f"image: {base_name}.pgm\n")
                f.write(f"resolution: {map_data.info.resolution}\n")
                f.write(
                    f"origin: [{map_data.info.origin.position.x}, "
                    f"{map_data.info.origin.position.y}, 0.0]\n"
                )
                f.write("negate: 0\n")
                f.write("occupied_thresh: 0.65\n")
                f.write("free_thresh: 0.196\n")

            self._save_count += 1
            logger.info(f"Map saved: {pgm_path} (#{self._save_count})")

            return str(pgm_path)

        except Exception as e:
            logger.error(f"Failed to save map: {e}")
            return ""

    @skill
    def save_map_now(self, name: str = "manual") -> str:
        """立即保存当前地图.

        手动触发地图保存，可以指定文件名前缀。

        Args:
            name: 文件名前缀，默认为"manual"

        Returns:
            保存结果的描述，包括文件路径
        """
        if self._last_map is None:
            return "错误：当前没有可用的地图数据"

        file_path = self._save_map_internal(self._last_map, name)

        if file_path:
            return (
                f"地图保存成功：\n"
                f"  - 文件路径：{file_path}\n"
                f"  - 地图尺寸：{self._last_map.info.width} x {self._last_map.info.height}\n"
                f"  - 分辨率：{self._last_map.info.resolution} 米/像素\n"
                f"  - 总保存次数：{self._save_count}"
            )
        else:
            return "错误：地图保存失败，请查看日志"

    @skill
    def get_save_status(self) -> str:
        """获取地图保存状态.

        返回自动保存配置和保存历史信息。

        Returns:
            保存状态的描述
        """
        if self._last_map is None:
            map_status = "无地图数据"
        else:
            map_status = (
                f"{self._last_map.info.width}x{self._last_map.info.height}, "
                f"{self._last_map.info.resolution}m/px"
            )

        time_since_save = time.time() - self._last_save_time
        next_save_in = max(0, self._auto_save_interval - time_since_save)

        return (
            f"地图保存状态：\n"
            f"  - 保存目录：{self._save_dir}\n"
            f"  - 自动保存：{'启用' if self._enable_auto_save else '禁用'}\n"
            f"  - 保存间隔：{self._auto_save_interval} 秒\n"
            f"  - 下次自动保存：{next_save_in:.1f} 秒后\n"
            f"  - 已保存次数：{self._save_count}\n"
            f"  - 当前地图：{map_status}"
        )

    @skill
    def set_auto_save(self, enabled: bool) -> str:
        """启用或禁用自动保存.

        Args:
            enabled: True启用，False禁用

        Returns:
            设置结果的描述
        """
        self._enable_auto_save = enabled
        status = "启用" if enabled else "禁用"

        logger.info(f"Auto-save {status}")

        return (
            f"自动保存已{status}：\n"
            f"  - 保存间隔：{self._auto_save_interval} 秒\n"
            f"  - 保存目录：{self._save_dir}"
        )
