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

import json

import pytest

from dimos.mapping.map_profile import (
    build_map_profile,
    load_map_profile,
    map_profile_path,
    write_map_profile,
)


def _profile() -> dict[str, object]:
    return build_map_profile(
        map_id="office_mid360_v1",
        sensor_profile="mid360_pointlio_v1",
        voxel_size=0.05,
        extrinsic_version="go2_orin_navigation_20260813_v1",
        preprocessing={"voxel_size": 0.05, "frame": "world"},
        source_dataset="office.db",
    )


def test_map_profile_round_trip(tmp_path) -> None:
    map_path = tmp_path / "office.pc2.lcm"

    written = write_map_profile(map_path, _profile())
    loaded = load_map_profile(map_path)

    assert written == map_profile_path(map_path)
    assert loaded is not None
    assert loaded["map_id"] == "office_mid360_v1"
    assert loaded["sensor_profile"] == "mid360_pointlio_v1"


def test_map_profile_rejects_tampered_preprocessing(tmp_path) -> None:
    map_path = tmp_path / "office.pc2.lcm"
    profile_path = write_map_profile(map_path, _profile())
    payload = json.loads(profile_path.read_text(encoding="utf-8"))
    payload["preprocessing"]["voxel_size"] = 0.2
    profile_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="preprocess_config_hash"):
        load_map_profile(map_path)
