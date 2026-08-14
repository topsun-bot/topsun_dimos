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

from dimos_lcm.std_msgs import Bool  # type: ignore[import-untyped]
import numpy as np

from dimos.mapping.costmapper import CostMapper
from dimos.msgs.nav_msgs.OccupancyGrid import OccupancyGrid


def test_source_fault_replaces_costmap_and_blocks_automatic_recovery() -> None:
    mapper = CostMapper(require_navigation_source_health=True)
    published: list[OccupancyGrid] = []
    unsubscribe = mapper.global_costmap.subscribe(published.append)
    try:
        assert mapper._map_updates_allowed() is False
        mapper._on_navigation_source_health(Bool(data=True))
        assert mapper._map_updates_allowed() is True

        mapper._on_navigation_source_health(Bool(data=False))
        assert mapper._map_updates_allowed() is False
        assert len(published) == 1
        assert published[0].grid.shape == (1, 1)
        np.testing.assert_array_equal(published[0].grid, np.array([[-1]], dtype=np.int8))

        mapper._on_navigation_source_health(Bool(data=True))
        mapper._on_navigation_source_health(Bool(data=False))
        assert mapper._map_updates_allowed() is False
        assert len(published) == 1
    finally:
        unsubscribe()
        mapper._navigation_trace.close()
        mapper._close_module()
