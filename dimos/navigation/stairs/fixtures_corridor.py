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

from dimos.mapping.terrain.fixtures.synthetic import straight_stair_5_steps_17cm
from dimos.mapping.terrain.stair_detection import candidates_to_corridors, detect_stairs_from_height_map
from dimos.navigation.stairs.contracts import StairCorridor, StairDetectionConfig


def synthetic_stair_corridor_ascending() -> StairCorridor:
    synth = straight_stair_5_steps_17cm()
    candidates = detect_stairs_from_height_map(
        synth.height_map,
        synth.origin_x,
        synth.origin_y,
        StairDetectionConfig(resolution=synth.resolution),
    )
    return candidates_to_corridors(candidates, ascending=True)[0]


def synthetic_stair_corridor_descending() -> StairCorridor:
    corridor = synthetic_stair_corridor_ascending()
    return StairCorridor(
        polygon=corridor.polygon,
        centerline=list(corridor.centerline),
        safe_half_width=corridor.safe_half_width,
        ascending=False,
        axis_yaw=corridor.axis_yaw,
        mean_riser=corridor.mean_riser,
        mean_tread=corridor.mean_tread,
        riser_lines=list(corridor.riser_lines),
    )
