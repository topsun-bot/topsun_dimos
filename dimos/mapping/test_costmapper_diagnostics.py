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

import numpy as np

from dimos.core.global_config import GlobalConfig
from dimos.mapping.costmapper import Config, _costmap_trace_metadata
from dimos.mapping.pointclouds.occupancy import HeightCostConfig
from dimos.msgs.nav_msgs.OccupancyGrid import OccupancyGrid


def test_costmapper_trace_records_reproducible_input_and_algorithm() -> None:
    config = Config(
        g=GlobalConfig(),
        algo="height_cost",
        config=HeightCostConfig(
            resolution=0.2,
            can_pass_under=0.7,
            can_climb=0.12,
            ignore_noise=0.03,
            smoothing=0.5,
        ),
        initial_safe_radius_meters=0.4,
    )
    grid = OccupancyGrid(
        grid=np.array([[0, 100]], dtype=np.int8),
        resolution=0.2,
        frame_id="world",
        ts=12.5,
    )

    fields = _costmap_trace_metadata(
        config,
        grid,
        costmap_id="costmap-000001",
        costmap_sequence=1,
        source="merged_map",
        source_ts=12.0,
        source_point_count=4321,
        source_rx_monotonic_sec=10.0,
        calculation_time_ms=4.25,
    )

    assert fields["source"] == "merged_map"
    assert fields["source_point_count"] == 4321
    assert fields["occupancy_algorithm"] == "height_cost"
    assert fields["occupancy_config"] == {
        "resolution": 0.2,
        "frame_id": None,
        "can_pass_under": 0.7,
        "can_climb": 0.12,
        "ignore_noise": 0.03,
        "smoothing": 0.5,
        "overhead_cutoff": None,
    }
    assert fields["initial_safe_radius_meters"] == 0.4
