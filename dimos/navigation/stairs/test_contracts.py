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

from dimos.navigation.stairs.contracts import (
    StairCandidate,
    StairCorridor,
    StairDetectionConfig,
    StairPhase,
)


def test_stair_detection_config_defaults() -> None:
    cfg = StairDetectionConfig()
    assert cfg.min_riser == 0.15
    assert cfg.max_riser == 0.20
    assert cfg.min_tread == 0.25
    assert cfg.min_steps == 3
    assert cfg.resolution == 0.05


def test_stair_candidate_fields() -> None:
    c = StairCandidate(
        axis_yaw=0.0,
        ascending_direction=(1.0, 0.0),
        centerline=[(0.0, 0.0), (1.0, 0.0)],
        polygon=[(0.0, -0.5), (1.0, -0.5), (1.0, 0.5), (0.0, 0.5)],
        mean_riser=0.17,
        mean_tread=0.28,
        confidence=0.9,
    )
    assert c.mean_riser == 0.17
    assert len(c.centerline) == 2


def test_stair_corridor_defaults() -> None:
    corridor = StairCorridor(
        polygon=[(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)],
        centerline=[(0.0, 0.5), (1.0, 0.5)],
        safe_half_width=0.25,
        ascending=True,
    )
    assert corridor.ascending is True
    assert corridor.riser_lines == []


def test_stair_phase_values() -> None:
    assert StairPhase.ON_STAIR.value == "on_stair"
    assert StairPhase.IDLE in StairPhase
