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


def test_global_config_unitree_backend() -> None:
    cfg = GlobalConfig(simulation=True, mujoco_backend="unitree")
    assert cfg.unitree_connection_type == "unitree_mujoco"
    cfg_dimos = GlobalConfig(simulation=True, mujoco_backend="dimos")
    assert cfg_dimos.unitree_connection_type == "mujoco"


def test_resolve_stairs_scene_when_vendored() -> None:
    if not unitree_mujoco_root().is_dir():
        return
    cfg = GlobalConfig(simulation=True, mujoco_room="stairs")
    scene = resolve_robot_scene(cfg)
    assert scene.is_file()
    assert "stair" in scene.name.lower() or "terrain" in scene.name.lower()
