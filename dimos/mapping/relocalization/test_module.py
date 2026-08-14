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

from dimos_lcm.std_msgs import Bool
import numpy as np
import pytest

from dimos.mapping.map_profile import build_map_profile, write_map_profile
from dimos.mapping.relocalization.module import RelocalizationModule
from dimos.protocol.rpc.pubsubrpc import LCMRPC
from dimos.spec.utils import spec_annotation_compliance
from dimos.types.relocalization_spec import RelocalizationStateSpec


def _T_map_world(x: float = 1.0, yaw_deg: float = 0.0) -> np.ndarray:
    matrix = np.eye(4)
    yaw = np.radians(yaw_deg)
    matrix[0, 0] = np.cos(yaw)
    matrix[0, 1] = -np.sin(yaw)
    matrix[1, 0] = np.sin(yaw)
    matrix[1, 1] = np.cos(yaw)
    matrix[0, 3] = x
    return matrix


@pytest.fixture()
def relocalization_module_factory(mocker, tmp_path):
    """Construct real modules while replacing only event-loop and RPC boundaries."""
    mocker.patch("dimos.core.module.get_loop", return_value=(None, None))
    mocker.patch.object(LCMRPC, "__init__", return_value=None)
    mocker.patch.object(LCMRPC, "serve_module_rpc")
    mocker.patch.object(LCMRPC, "start")
    mocker.patch.object(LCMRPC, "stop")
    modules = []

    def create(**kwargs):
        # Persistence-focused tests opt in explicitly because jtlinux keeps it
        # disabled in production by default.
        kwargs.setdefault("save_first_transform_json", True)
        # This fixture exercises the cached/fast-ICP state machine. Production
        # keeps that optional path disabled unless a blueprint opts into it.
        kwargs.setdefault("fast_icp_enabled", True)
        kwargs.setdefault("fitness_threshold", 0.75)
        kwargs.setdefault("fast_icp_min_fitness", 0.75)
        kwargs.setdefault("subsequent_relocalization_mode", "fast_icp")
        map_file = kwargs.pop("map_file", "recording_go2")
        module = RelocalizationModule(
            map_file=map_file,
            cached_transform_dir=str(tmp_path),
            **kwargs,
        )
        modules.append(module)
        return module

    yield create

    for module in modules:
        module.dispose()


def test_first_published_transform_is_saved_once(relocalization_module_factory, tmp_path) -> None:
    module = relocalization_module_factory()
    first_matrix = _T_map_world(1.25)
    first_tf = module._tf_from_T_map_world(first_matrix)

    module._record_relocalization_success(first_matrix, first_tf, 0.81, 52_000, "global")
    module._publish_tf(first_tf)

    cache_dir = tmp_path / "recording_go2"
    latest = cache_dir / "latest.json"
    first_payload = json.loads(latest.read_text(encoding="utf-8"))
    assert first_payload["source"] == "first_published_tf"
    assert first_payload["match_mode"] == "global"
    assert first_payload["T_map_world"] == first_matrix.tolist()
    assert len(list(cache_dir.glob("*-first-tf.json"))) == 1

    second_matrix = _T_map_world(9.0)
    second_tf = module._tf_from_T_map_world(second_matrix)
    module._record_relocalization_success(
        second_matrix,
        second_tf,
        0.92,
        60_000,
        "subsequent_fast_icp",
    )
    module._publish_tf(second_tf)

    unchanged_payload = json.loads(latest.read_text(encoding="utf-8"))
    assert unchanged_payload["T_map_world"] == first_matrix.tolist()
    assert len(list(cache_dir.glob("*-first-tf.json"))) == 1


def test_relocalization_state_rpc_requires_a_transform_published_this_run(
    relocalization_module_factory,
) -> None:
    """Spatial memory must never bind records to an unvalidated cached transform."""
    module = relocalization_module_factory(save_first_transform_json=False)

    assert spec_annotation_compliance(RelocalizationModule, RelocalizationStateSpec)
    assert module.config.fast_icp_enabled is True
    assert module.config.fitness_threshold == pytest.approx(0.75)
    assert module.get_current_map_key() == "recording_go2"
    assert module.get_current_map_file() == "recording_go2"
    assert module.get_current_map_profile() is None
    assert module.get_world_to_map() is None
    assert module.is_relocalized() is False

    matrix = _T_map_world(1.5)
    transform = module._tf_from_T_map_world(matrix)
    module._record_relocalization_success(matrix, transform, 0.82, 52_000, "global")

    assert module.get_world_to_map() is transform
    assert module.is_relocalized() is False

    module._publish_tf(transform)

    assert module.get_world_to_map() is transform
    assert module.is_relocalized() is True


def test_mid360_profile_is_required_and_must_match(
    relocalization_module_factory,
    tmp_path,
) -> None:
    map_path = tmp_path / "office.pc2.lcm"
    map_path.write_bytes(b"not decoded in this test")
    module = relocalization_module_factory(
        map_file=str(map_path),
        require_map_profile=True,
        expected_sensor_profile="mid360_pointlio_v1",
        expected_extrinsic_version="mount_v1",
    )

    with pytest.raises(ValueError, match="required but missing"):
        module._load_and_validate_map_profile(map_path)

    profile = build_map_profile(
        map_id="office",
        sensor_profile="go2_voxel",
        voxel_size=0.05,
        extrinsic_version="mount_v1",
        preprocessing={"voxel_size": 0.05},
        source_dataset="office.db",
    )
    write_map_profile(map_path, profile)

    with pytest.raises(ValueError, match="sensor_profile"):
        module._load_and_validate_map_profile(map_path)

    profile["sensor_profile"] = "mid360_pointlio_v1"
    write_map_profile(map_path, profile)
    loaded = module._load_and_validate_map_profile(map_path)

    assert loaded is not None
    assert loaded["map_id"] == "office"


def test_navigation_source_fault_invalidates_relocalization_until_restart(
    relocalization_module_factory,
) -> None:
    module = relocalization_module_factory(require_navigation_source_health=True)
    merged_maps = []
    unsubscribe = module.merged_map.subscribe(merged_maps.append)
    transform = module._tf_from_T_map_world(_T_map_world(1.0))
    try:
        module._on_navigation_source_health(Bool(data=True))
        module._record_relocalization_success(_T_map_world(1.0), transform, 0.9, 50_000, "global")
        module._publish_tf(transform)
        assert module.is_relocalized() is True

        module._on_navigation_source_health(Bool(data=False))

        assert module.is_relocalized() is False
        assert module.get_world_to_map() is None
        assert module._last_T_map_world is None
        assert len(merged_maps) == 1
        assert len(merged_maps[0]) == 0
        assert merged_maps[0].frame_id == "world"

        module._on_navigation_source_health(Bool(data=True))
        module._record_relocalization_success(_T_map_world(2.0), transform, 0.9, 50_000, "global")
        module._publish_tf(transform)
        assert module.is_relocalized() is False
    finally:
        unsubscribe()


def test_new_module_loads_json_and_uses_10k_start_threshold(
    relocalization_module_factory,
) -> None:
    first_run = relocalization_module_factory()
    matrix = _T_map_world(2.5)
    tf = first_run._tf_from_T_map_world(matrix)
    first_run._record_relocalization_success(matrix, tf, 0.76, 50_000, "global")
    first_run._publish_tf(tf)

    second_run = relocalization_module_factory()
    second_run._load_cached_transform_on_start()

    assert second_run._loaded_T_map_world_from_json is True
    assert np.array_equal(second_run._last_T_map_world, matrix)
    assert second_run._required_local_points() == 10_000
    assert second_run._current_relocalization_mode() == "cached_start_fast_icp"


def test_global_subsequent_mode_bypasses_fast_icp(relocalization_module_factory, mocker) -> None:
    module = relocalization_module_factory(
        subsequent_relocalization_mode="global",
        save_first_transform_json=False,
    )
    module._premap = mocker.MagicMock()
    module._last_T_map_world = _T_map_world()
    module._has_published_tf_this_run = True
    expected_tf = module._tf_from_T_map_world(_T_map_world())
    fast = mocker.patch.object(module, "_try_fast_icp_relocalize")
    global_relocalize = mocker.patch.object(
        module,
        "_try_global_relocalize",
        return_value=expected_tf,
    )
    msg = mocker.MagicMock()
    msg.__len__.return_value = 50_000

    result = module._try_relocalize(msg)

    assert result is expected_tf
    fast.assert_not_called()
    global_relocalize.assert_called_once_with(msg)
    assert module._required_local_points() == 50_000


def test_fast_icp_uses_cached_matrix_and_configured_estimator(
    relocalization_module_factory,
    mocker,
) -> None:
    module = relocalization_module_factory(save_first_transform_json=False)
    premap = mocker.sentinel.premap
    local_map = mocker.sentinel.local_map
    module._premap = mocker.MagicMock(pointcloud=premap)
    initial = _T_map_world(3.0)
    refined = _T_map_world(3.05)
    module._last_T_map_world = initial
    module._json_init_T_map_world = initial.copy()
    module._loaded_T_map_world_from_json = True
    fast_icp = mocker.patch(
        "dimos.mapping.relocalization.module._relocalize_with_initial",
        return_value=(refined, 0.86, mocker.MagicMock()),
    )
    msg = mocker.MagicMock(pointcloud=local_map)
    msg.__len__.return_value = 10_000

    result = module._try_fast_icp_relocalize(msg)

    assert result is not None
    fast_icp.assert_called_once_with(
        premap,
        local_map,
        initial,
        max_correspondence_distance=0.10,
        max_iteration=80,
        crop_radius=None,
        icp_estimation="point_to_point",
    )
    assert np.array_equal(module._last_T_map_world, refined)


def test_fast_icp_failure_falls_back_to_global_at_50k_points(
    relocalization_module_factory,
    mocker,
) -> None:
    module = relocalization_module_factory(save_first_transform_json=False)
    module._premap = mocker.MagicMock()
    module._last_T_map_world = _T_map_world()
    module._loaded_T_map_world_from_json = True
    expected_tf = module._tf_from_T_map_world(_T_map_world(1.1))
    fast = mocker.patch.object(module, "_try_fast_icp_relocalize", return_value=None)
    global_relocalize = mocker.patch.object(
        module,
        "_try_global_relocalize",
        return_value=expected_tf,
    )
    msg = mocker.MagicMock()
    msg.__len__.return_value = 50_000

    result = module._try_relocalize(msg)

    assert result is expected_tf
    fast.assert_called_once_with(msg)
    global_relocalize.assert_called_once_with(msg)


def test_fast_icp_failure_defers_global_fallback_below_50k_points(
    relocalization_module_factory,
    mocker,
) -> None:
    module = relocalization_module_factory(save_first_transform_json=False)
    module._premap = mocker.MagicMock()
    module._last_T_map_world = _T_map_world()
    module._loaded_T_map_world_from_json = True
    fast = mocker.patch.object(module, "_try_fast_icp_relocalize", return_value=None)
    global_relocalize = mocker.patch.object(module, "_try_global_relocalize")
    msg = mocker.MagicMock()
    msg.__len__.return_value = 10_000

    result = module._try_relocalize(msg)

    assert result is None
    fast.assert_called_once_with(msg)
    global_relocalize.assert_not_called()


def test_cached_start_fast_icp_does_not_write_json(
    relocalization_module_factory,
    tmp_path,
) -> None:
    module = relocalization_module_factory()
    global_matrix = _T_map_world(1.25)
    global_tf = module._tf_from_T_map_world(global_matrix)
    module._record_relocalization_success(global_matrix, global_tf, 0.89, 52_000, "global")
    module._publish_tf(global_tf)

    cache_dir = tmp_path / "recording_go2"
    latest = cache_dir / "latest.json"
    global_payload = json.loads(latest.read_text(encoding="utf-8"))
    assert global_payload["match_mode"] == "global"

    cached_matrix = _T_map_world(2.0)
    cached_tf = module._tf_from_T_map_world(cached_matrix)
    module._record_relocalization_success(
        cached_matrix,
        cached_tf,
        0.91,
        12_000,
        "cached_start_fast_icp",
    )
    module._publish_tf(cached_tf)

    unchanged_payload = json.loads(latest.read_text(encoding="utf-8"))
    assert unchanged_payload["T_map_world"] == global_matrix.tolist()
    assert len(list(cache_dir.glob("*-first-tf.json"))) == 1


def test_cached_start_rejected_on_large_pose_delta(
    relocalization_module_factory,
    mocker,
) -> None:
    module = relocalization_module_factory(save_first_transform_json=False)
    premap = mocker.sentinel.premap
    local_map = mocker.sentinel.local_map
    module._premap = mocker.MagicMock(pointcloud=premap)
    initial = _T_map_world(3.0)
    bad_result = _T_map_world(4.0, yaw_deg=20.0)
    module._last_T_map_world = initial
    module._json_init_T_map_world = initial.copy()
    module._loaded_T_map_world_from_json = True
    mocker.patch(
        "dimos.mapping.relocalization.module._relocalize_with_initial",
        return_value=(bad_result, 0.90, mocker.MagicMock()),
    )
    msg = mocker.MagicMock(pointcloud=local_map)
    msg.__len__.return_value = 12_000

    result = module._try_fast_icp_relocalize(msg)

    assert result is None
    assert np.array_equal(module._last_T_map_world, initial)


def test_cached_start_uses_stricter_fitness(
    relocalization_module_factory,
    mocker,
) -> None:
    module = relocalization_module_factory(
        save_first_transform_json=False,
        cached_start_min_fitness=0.85,
        fast_icp_min_fitness=0.75,
        subsequent_relocalization_mode="fast_icp",
    )
    premap = mocker.sentinel.premap
    local_map = mocker.sentinel.local_map
    module._premap = mocker.MagicMock(pointcloud=premap)
    initial = _T_map_world(3.0)
    refined = _T_map_world(3.05)
    module._last_T_map_world = initial
    module._json_init_T_map_world = initial.copy()
    module._loaded_T_map_world_from_json = True
    mocker.patch(
        "dimos.mapping.relocalization.module._relocalize_with_initial",
        return_value=(refined, 0.80, mocker.MagicMock()),
    )
    cached_msg = mocker.MagicMock(pointcloud=local_map)
    cached_msg.__len__.return_value = 12_000

    cached_result = module._try_fast_icp_relocalize(cached_msg)
    assert cached_result is None

    module._has_published_tf_this_run = True
    subsequent_msg = mocker.MagicMock(pointcloud=local_map)
    subsequent_msg.__len__.return_value = 12_000
    subsequent_result = module._try_fast_icp_relocalize(subsequent_msg)
    assert subsequent_result is not None
