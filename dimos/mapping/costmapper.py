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

from dataclasses import asdict
import time

from pydantic import Field
from reactivex import operators as ops

from dimos.core.core import rpc
from dimos.core.module import Module, ModuleConfig
from dimos.core.stream import In, Out
from dimos.experimental.navigation_obstacle_mvp.mvp_costmap import apply_mvp_sill_lethal_zones
from dimos.mapping.pointclouds.occupancy import (
    OCCUPANCY_ALGOS,
    HeightCostConfig,
    OccupancyConfig,
)
from dimos.msgs.nav_msgs.OccupancyGrid import OccupancyGrid
from dimos.msgs.sensor_msgs.PointCloud2 import PointCloud2
from dimos.utils.logging_config import setup_logger

logger = setup_logger()


class Config(ModuleConfig):
    algo: str = "height_cost"
    config: OccupancyConfig = Field(default_factory=HeightCostConfig)


class CostMapper(Module):
    config: Config
    global_map: In[PointCloud2]
    global_costmap: Out[OccupancyGrid]

    @rpc
    def start(self) -> None:
        super().start()

        def _publish_costmap(grid: OccupancyGrid, calc_time_ms: float, rx_monotonic: float) -> None:
            self.global_costmap.publish(grid)

        def _calculate_and_time(
            msg: PointCloud2,
        ) -> tuple[OccupancyGrid, float, float]:
            rx_monotonic = time.monotonic()  # Capture receipt time
            start = time.perf_counter()
            grid = self._calculate_costmap(msg)
            elapsed_ms = (time.perf_counter() - start) * 1000
            return grid, elapsed_ms, rx_monotonic

        stream = self.global_map.observable()  # type: ignore[no-untyped-call]
        if self.config.g.simulation:
            # MuJoCo sim: dense global_map updates are the main CPU sink for navigation.
            stream = stream.pipe(ops.throttle_first(0.15))

        self.register_disposable(
            stream.pipe(ops.map(_calculate_and_time)).subscribe(
                lambda result: _publish_costmap(result[0], result[1], result[2])
            )
        )

    @rpc
    def stop(self) -> None:
        super().stop()

    # @timed()  # TODO: fix thread leak in timed decorator
    def _calculate_costmap(self, msg: PointCloud2) -> OccupancyGrid:
        fn = OCCUPANCY_ALGOS[self.config.algo]
        kwargs = asdict(self.config.config)
        if self.config.g.simulation:
            kwargs["resolution"] = max(kwargs.get("resolution", 0.05), 0.08)
        grid = fn(msg, **kwargs)
        return apply_mvp_sill_lethal_zones(
            grid,
            obstacle_crossing_mvp=self.config.g.obstacle_crossing_mvp,
            simulation=self.config.g.simulation,
        )
