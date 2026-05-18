# Copyright 2026 Dimensional Inc.
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

from __future__ import annotations

from dimos.core.core import rpc
from dimos.core.module import Module, ModuleConfig
from dimos.core.stream import In
from dimos.mapping.terrain.stair_detection import (
    candidates_to_corridors,
    detect_stairs_from_pointcloud,
)
from dimos.msgs.sensor_msgs.PointCloud2 import PointCloud2
from dimos.navigation.stairs.contracts import StairCorridor, StairDetectionConfig
from dimos.navigation.stairs.navigation_stair_spec import NavigationStairSpec
from dimos.utils.logging_config import setup_logger

logger = setup_logger()


class StairNavigatorConfig(ModuleConfig):
    detection: StairDetectionConfig = StairDetectionConfig()
    safe_half_width: float = 0.25


class StairNavigatorModule(Module):
    """Detect straight stairs from the global map and arm corridor-aware planning."""

    config: StairNavigatorConfig
    global_map: In[PointCloud2]

    _navigator: NavigationStairSpec
    _active_corridor: StairCorridor | None = None

    @rpc
    def start(self) -> None:
        super().start()
        if not self.config.g.stair_navigation:
            logger.info("stair_navigation disabled — StairNavigatorModule idle")
            return
        self.register_disposable(self.global_map.subscribe(self._on_global_map))

    @rpc
    def stop(self) -> None:
        self._clear_corridor()
        super().stop()

    def _clear_corridor(self) -> None:
        if self._active_corridor is not None:
            self._navigator.set_stair_corridor(None)
            self._active_corridor = None

    def _on_global_map(self, cloud: PointCloud2) -> None:
        if not self.config.g.stair_navigation:
            return

        candidates = detect_stairs_from_pointcloud(cloud, self.config.detection)
        if not candidates:
            if self._active_corridor is not None:
                logger.info("Stair lost — clearing corridor")
                self._clear_corridor()
            return

        corridors = candidates_to_corridors(
            candidates,
            safe_half_width=self.config.safe_half_width,
            ascending=True,
        )
        corridor = corridors[0]
        if self._active_corridor is None or self._corridor_changed(corridor):
            self._active_corridor = corridor
            self._navigator.set_stair_corridor(corridor)
            logger.info(
                "Stair corridor armed",
                mean_riser=round(corridor.mean_riser, 3),
                steps=len(corridor.centerline),
            )

    def _corridor_changed(self, corridor: StairCorridor) -> bool:
        if self._active_corridor is None:
            return True
        return abs(corridor.mean_riser - self._active_corridor.mean_riser) > 0.02


stair_navigator_module = StairNavigatorModule.blueprint
