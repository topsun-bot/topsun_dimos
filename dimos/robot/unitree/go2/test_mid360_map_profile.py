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

import pytest

from dimos.mapping.map_profile import preprocess_config_hash
from dimos.robot.unitree.go2.mid360_map_profile import (
    build_mid360_preprocessing_manifest,
)


def test_mid360_manifest_covers_lio_adapter_and_map_geometry() -> None:
    manifest = build_mid360_preprocessing_manifest()

    assert manifest["pointlio"]["device_model"] == "mid360s"
    assert manifest["pointlio"]["odom_freq"] == 10.0
    assert manifest["pointlio"]["extrinsic_t"] == [-0.011, -0.02329, 0.04412]
    assert manifest["navigation_adapter"]["max_odom_bracket_sec"] == 0.1
    assert manifest["navigation_adapter"]["self_max_xyz"] == [0.45, 0.28, 0.35]
    assert manifest["live_mapping"]["algorithm"] == "voxel_grid_mapper"
    assert manifest["live_mapping"]["voxel_size"] == 0.05
    assert manifest["live_mapping"]["carve_columns"] is True
    assert manifest["live_mapping"]["emit_every"] == 10
    assert manifest["costmap_projection"] == {
        "algorithm": "height_cost",
        "resolution": 0.1,
        "can_pass_under": 0.6,
        "can_climb": 0.15,
        "ignore_noise": 0.05,
        "smoothing": 1.0,
        "frame_id": None,
    }
    assert manifest["map_export"]["voxel_size"] == 0.05


def test_mid360_manifest_hash_changes_with_map_voxel_size() -> None:
    fine = build_mid360_preprocessing_manifest(map_voxel_size_m=0.05)
    coarse = build_mid360_preprocessing_manifest(map_voxel_size_m=0.10)

    assert preprocess_config_hash(fine) != preprocess_config_hash(coarse)


def test_mid360_manifest_rejects_invalid_map_voxel_size() -> None:
    with pytest.raises(ValueError, match="positive"):
        build_mid360_preprocessing_manifest(map_voxel_size_m=0.0)
