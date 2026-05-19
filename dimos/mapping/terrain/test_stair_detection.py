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

import math

import pytest

from dimos.mapping.terrain.fixtures.synthetic import (
    mujoco_scene_stairs_height_map,
    negative_flat_height_map,
    negative_ramp_height_map,
    straight_stair_5_steps_15cm,
    straight_stair_5_steps_17cm,
    straight_stair_8_steps_20cm,
)
from dimos.mapping.terrain.stair_detection import (
    candidates_to_corridors,
    detect_stairs_from_height_map,
)
from dimos.navigation.stairs.contracts import StairDetectionConfig


@pytest.mark.parametrize(
    "fixture_fn,expected_riser",
    [
        (straight_stair_5_steps_15cm, 0.15),
        (straight_stair_5_steps_17cm, 0.17),
        (straight_stair_8_steps_20cm, 0.20),
    ],
)
def test_detect_straight_stairs_riser_height(fixture_fn, expected_riser) -> None:
    synth = fixture_fn()
    candidates = detect_stairs_from_height_map(
        synth.height_map,
        synth.origin_x,
        synth.origin_y,
        StairDetectionConfig(resolution=synth.resolution),
    )
    assert len(candidates) == 1
    c = candidates[0]
    assert abs(c.mean_riser - expected_riser) < 0.03
    assert abs(c.axis_yaw - synth.expected_axis_yaw) < math.radians(15)


def test_negative_ramp_no_stairs() -> None:
    synth = negative_ramp_height_map()
    candidates = detect_stairs_from_height_map(
        synth.height_map,
        synth.origin_x,
        synth.origin_y,
        StairDetectionConfig(resolution=synth.resolution),
    )
    assert len(candidates) == 0


def test_detect_mujoco_like_sparse_floor_stairs_15cm() -> None:
    """Regression: stairs on a large flat floor (scene_stairs) must still be detected."""
    synth = mujoco_scene_stairs_height_map()
    candidates = detect_stairs_from_height_map(
        synth.height_map,
        synth.origin_x,
        synth.origin_y,
        StairDetectionConfig(resolution=synth.resolution),
    )
    assert len(candidates) == 1
    assert candidates[0].mean_riser == pytest.approx(0.15, abs=0.03)


def test_negative_flat_no_stairs() -> None:
    synth = negative_flat_height_map()
    candidates = detect_stairs_from_height_map(
        synth.height_map,
        synth.origin_x,
        synth.origin_y,
        StairDetectionConfig(resolution=synth.resolution),
    )
    assert len(candidates) == 0


def test_candidates_to_corridors() -> None:
    synth = straight_stair_5_steps_17cm()
    candidates = detect_stairs_from_height_map(
        synth.height_map,
        synth.origin_x,
        synth.origin_y,
        StairDetectionConfig(resolution=synth.resolution),
    )
    corridors = candidates_to_corridors(candidates)
    assert len(corridors) == 1
    assert len(corridors[0].centerline) >= 3
    assert corridors[0].mean_riser == pytest.approx(0.17, abs=0.03)


def test_stair_detection_config_table_driven() -> None:
    cfg = StairDetectionConfig(min_riser=0.15, max_riser=0.20)
    assert cfg.min_steps == 3
