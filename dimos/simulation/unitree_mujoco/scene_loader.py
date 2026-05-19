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

"""Load Unitree MuJoCo scenes; copy to a space-free path when required (macOS)."""

from __future__ import annotations

import shutil
from pathlib import Path

from dimos.core.global_config import GlobalConfig
from dimos.simulation.unitree_mujoco.paths import unitree_mujoco_root
from dimos.simulation.unitree_mujoco.scenes import resolve_robot_scene

_SPACE_FREE_GO2 = Path.home() / ".local" / "dimos-mujoco-work" / "unitree_robots" / "go2"
_STAIRS_SCENE_SRC = Path(__file__).parent / "scenes" / "scene_dimos_stairs.xml"
_STAIRS_SCENE_LOCAL = Path(__file__).parent / "scenes" / "scene_dimos_stairs_local.xml"


def _path_has_spaces(path: Path) -> bool:
    return " " in str(path.resolve())


def _sync_space_free_go2_tree() -> Path:
    """Copy ``unitree_robots/go2`` to ``~/.local/dimos-mujoco-work`` (no spaces in path)."""
    src_go2 = unitree_mujoco_root() / "unitree_robots" / "go2"
    if not src_go2.is_dir():
        raise FileNotFoundError(f"Missing Unitree Go2 assets at {src_go2}")

    if _SPACE_FREE_GO2.is_dir():
        shutil.rmtree(_SPACE_FREE_GO2)
    shutil.copytree(src_go2, _SPACE_FREE_GO2)
    return _SPACE_FREE_GO2


def _install_stairs_scene(go2_dir: Path) -> Path:
    """Place stairs MJCF beside ``go2.xml`` with a same-directory include."""
    dest = go2_dir / "scene_dimos_stairs.xml"
    src = _STAIRS_SCENE_LOCAL if _STAIRS_SCENE_LOCAL.is_file() else _STAIRS_SCENE_SRC
    shutil.copy2(src, dest)
    return dest


def mujoco_scene_path(cfg: GlobalConfig) -> Path:
    """Return an MJCF path safe for ``mujoco.MjModel.from_xml_path`` (no spaces)."""
    scene = resolve_robot_scene(cfg).resolve()
    if not _path_has_spaces(scene) and not _path_has_spaces(unitree_mujoco_root()):
        return scene

    go2_dir = _sync_space_free_go2_tree()
    room = (cfg.mujoco_room or "").strip().lower()
    if room in ("stairs", "scene_stairs", "stair"):
        return _install_stairs_scene(go2_dir)
    return go2_dir / "scene.xml"
