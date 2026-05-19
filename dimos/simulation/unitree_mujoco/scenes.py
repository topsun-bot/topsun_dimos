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

"""Resolve MuJoCo scene XML paths for the Unitree official simulator."""

from __future__ import annotations

from pathlib import Path

from dimos.core.global_config import GlobalConfig
from dimos.simulation.unitree_mujoco.paths import unitree_mujoco_root

_DIMOS_STAIRS_SCENE = Path(__file__).parent / "scenes" / "scene_dimos_stairs.xml"


def resolve_robot_scene(cfg: GlobalConfig) -> Path:
    """Pick a scene file under ``third_party/unitree_mujoco`` or DimOS stairs overlay."""
    root = unitree_mujoco_root()
    go2_dir = root / "unitree_robots" / "go2"

    room = (cfg.mujoco_room or "").strip().lower()
    if room in ("stairs", "scene_stairs", "stair"):
        if _DIMOS_STAIRS_SCENE.is_file():
            return _DIMOS_STAIRS_SCENE
        terrain = go2_dir / "scene_terrain.xml"
        if terrain.is_file():
            return terrain

    default = go2_dir / "scene.xml"
    if default.is_file():
        return default

    raise FileNotFoundError(
        f"Unitree MuJoCo scenes not found under {root}. "
        "Clone https://github.com/unitreerobotics/unitree_mujoco into third_party/unitree_mujoco."
    )
