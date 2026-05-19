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

"""Go2 connection backed by Unitree's official MuJoCo simulator (``unitree_mujoco``)."""

from __future__ import annotations

from dimos.core.global_config import GlobalConfig
from dimos.robot.unitree.mujoco_connection import MujocoConnection
from dimos.simulation.unitree_mujoco.constants import LAUNCHER_PATH
from dimos.simulation.unitree_mujoco.paths import unitree_mujoco_root
from dimos.utils.logging_config import setup_logger

logger = setup_logger()


class UnitreeMujocoConnection(MujocoConnection):
    """DimOS velocity + Sport SHM bridge with Unitree DDS state and official Go2 MJCF."""

    def __init__(self, global_config: GlobalConfig) -> None:
        root = unitree_mujoco_root()
        if not root.is_dir():
            raise FileNotFoundError(
                f"Unitree MuJoCo not found at {root}. "
                "Clone: git clone https://github.com/unitreerobotics/unitree_mujoco "
                "third_party/unitree_mujoco"
            )
        try:
            import unitree_sdk2py  # noqa: F401
        except ImportError as exc:
            raise ImportError(
                "unitree-sdk2py is required for Unitree MuJoCo sim. "
                "Install with: uv sync --extra go2-sim  "
                "(or: uv sync --extra sim --extra unitree-dds --extra cpu --extra unitree)"
            ) from exc

        super().__init__(global_config, launcher_path=LAUNCHER_PATH)
        logger.info(
            "Using Unitree official MuJoCo simulator",
            root=str(root),
            scene_hint=global_config.mujoco_room,
        )
