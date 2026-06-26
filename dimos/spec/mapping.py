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

from typing import Protocol

from dimos.core.stream import Out
from dimos.msgs.nav_msgs.OccupancyGrid import OccupancyGrid
from dimos.msgs.sensor_msgs.PointCloud2 import PointCloud2
from dimos.spec.utils import Spec


class GlobalPointcloud(Protocol):
    global_map: Out[PointCloud2]


class GlobalCostmap(Protocol):
    global_costmap: Out[OccupancyGrid]


class MetricRelocalizationSpec(Spec, Protocol):
    def relocalize_current_map(
        self,
        search_radius_m: float = 2.0,
        stride_m: float | None = None,
        yaw_candidates_deg: list[float] | None = None,
        min_compared_cells: int = 20,
        min_occupied_cells: int = 1,
        min_confidence: float = 0.55,
    ) -> dict[str, float | int | bool]: ...

    def publish_persistent_map(
        self, shift_x: float = 0.0, shift_y: float = 0.0, rotate_deg: float = 0.0
    ) -> bool: ...
