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

from dimos.core.global_config import GlobalConfig
from dimos.simulation.unitree_mujoco.paths import unitree_mujoco_root
from dimos.simulation.unitree_mujoco.scenes import resolve_robot_scene


def test_global_config_mujoco_backends() -> None:
    assert GlobalConfig().mujoco_backend == "dimos"
    assert GlobalConfig(simulation=True).unitree_connection_type == "mujoco"
    cfg = GlobalConfig(simulation=True, mujoco_backend="unitree")
    assert cfg.unitree_connection_type == "unitree_mujoco"


def test_resolve_stairs_scene_when_vendored() -> None:
    if not unitree_mujoco_root().is_dir():
        return
    cfg = GlobalConfig(simulation=True, mujoco_room="stairs")
    scene = resolve_robot_scene(cfg)
    assert scene.is_file()
    assert "stair" in scene.name.lower() or "terrain" in scene.name.lower()
    from dimos.simulation.mujoco.build_scene_stairs import SCENE_MARKER

    assert SCENE_MARKER in scene.read_text(encoding="utf-8")


def test_unitree_go2_policy_inherits_sport_locomotion() -> None:
    from dimos.simulation.mujoco.policy import Go1OnnxController
    from dimos.simulation.unitree_mujoco.go2_policy import UnitreeGo2OnnxController

    assert UnitreeGo2OnnxController.get_obs is Go1OnnxController.get_obs
    assert "_policy_linvel" in UnitreeGo2OnnxController.__dict__


def test_unitree_stairs_scene_loads_when_vendored() -> None:
    if not unitree_mujoco_root().is_dir():
        return
    import mujoco

    from dimos.simulation.mujoco.build_scene_stairs import build_unitree_scene_dimos_stairs_xml
    from dimos.simulation.unitree_mujoco.scene_loader import mujoco_scene_path

    build_unitree_scene_dimos_stairs_xml(force=True)
    cfg = GlobalConfig(simulation=True, mujoco_room="stairs")
    scene = mujoco_scene_path(cfg)
    model = mujoco.MjModel.from_xml_path(str(scene))
    assert model.nbody > 0


def test_walk_stair_sport_payload_maps_to_gains() -> None:
    from dimos.robot.unitree.go2.stair_locomotion.sport_actions import sport_payload, walk_stair_call
    from dimos.simulation.mujoco.sport_state import (
        SportExecMode,
        apply_sport_api_payload,
        default_sport_buffer,
        sport_array_to_gains,
    )

    buf = default_sport_buffer()
    apply_sport_api_payload(buf, sport_payload(walk_stair_call(True)))
    gains = sport_array_to_gains(buf)
    assert gains.active
    assert gains.exec_mode == SportExecMode.WALK_STAIR
    assert gains.foot_raise_m > 0.05
