# Copyright 2025-2026 Dimensional Inc.
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

import re
import sys
from typing import Any, Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

from dimos.constants import DEFAULT_BUILD_NATIVE
from dimos.models.vl.types import VlModelName
from dimos.visualization.rerun.constants import (
    RERUN_ENABLE_WEB,
    RERUN_OPEN_DEFAULT,
    RerunOpenOption,
    ViewerBackend,
)


def _get_all_numbers(s: str) -> list[float]:
    return [float(x) for x in re.findall(r"-?\d+\.?\d*", s)]


class GlobalConfig(BaseSettings):
    robot_ip: str | None = None
    robot_ips: str | None = None
    xarm7_ip: str | None = None
    xarm6_ip: str | None = None
    can_port: str | None = None
    simulation: str = ""
    replay: bool = False
    replay_db: str = "go2_short"
    new_memory: bool = False
    # Default ``rerun``; ``dimos --simulation mujoco run …`` without ``--viewer`` also uses ``rerun`` (native).
    viewer: ViewerBackend = "rerun"
    rerun_open: RerunOpenOption = RERUN_OPEN_DEFAULT
    rerun_web: bool = RERUN_ENABLE_WEB
    rerun_host: str | None = None
    rerun_websocket_server_port: int = 3030
    n_workers: int = 2
    memory_limit: str = "auto"
    mujoco_camera_position: str | None = None
    mujoco_room: str | None = None
    mujoco_room_from_occupancy: str | None = None
    mujoco_global_costmap_from_occupancy: str | None = None
    mujoco_global_map_from_pointcloud: str | None = None
    mujoco_start_pos: str = "-1.0, 1.0"
    mujoco_steps_per_frame: int = 7
    # Open MuJoCo passive viewer window (GLFW). Use --no-mujoco-viewer to disable.
    mujoco_viewer: bool = True
    # Alias for --no-mujoco-viewer; kept for MUJOCO_HEADLESS env / --mujoco-headless.
    mujoco_headless: bool = False
    # Offscreen mujoco.Renderer GL backend. "auto" tries egl → osmesa → glfw; "off" skips cameras.
    mujoco_offscreen_gl: Literal["auto", "glfw", "egl", "osmesa", "off"] = "auto"
    robot_model: str | None = None
    robot_width: float = 0.3
    robot_rotation_diameter: float = 0.6
    nerf_speed: float = 1.0
    planner_robot_speed: float | None = None
    obstacle_crossing_mvp: bool = False
    obstacle_crossing_mvp_height_cm: float | None = None
    # Inject static MVP test obstacles into the default MuJoCo office scene.
    mujoco_navigation_test_obstacles: bool = False
    mcp_port: int = 9990
    build_native: bool = DEFAULT_BUILD_NATIVE
    dtop: bool = False
    obstacle_avoidance: bool = True
    detection_model: VlModelName = "moondream"
    listen_host: str = "127.0.0.1"
    # WebInput browser microphone → Whisper STT. False or init failure → text-only web UI.
    web_input_stt_enabled: bool = True
    dimsim_scene: str = "apt"
    dimsim_port: int = 8090

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    def update(self, **kwargs: object) -> None:
        """Update config fields in place."""
        for key, value in kwargs.items():
            if not hasattr(self, key):
                raise AttributeError(f"GlobalConfig has no field '{key}'")
            setattr(self, key, value)

    @property
    def unitree_connection_type(self) -> str:
        if self.replay:
            return "replay"
        if self.simulation:
            return self.simulation
        return "webrtc"

    @property
    def mujoco_start_pos_float(self) -> tuple[float, float]:
        x, y = _get_all_numbers(self.mujoco_start_pos)
        return (x, y)

    @property
    def mujoco_camera_position_float(self) -> tuple[float, ...]:
        if self.mujoco_camera_position is None:
            return (-0.906, 0.008, 1.101, 4.931, 89.749, -46.378)
        return tuple(_get_all_numbers(self.mujoco_camera_position))


# ``--obstacle-crossing-mvp-height-cm8`` (missing space/``=``) → value ``8``.
_OBSTACLE_MVP_HEIGHT_CM_FLAG = "obstacle-crossing-mvp-height-cm"
_OBSTACLE_MVP_HEIGHT_CM_STUCK_RE = re.compile(
    rf"^--{_OBSTACLE_MVP_HEIGHT_CM_FLAG}(?P<value>\d+(?:\.\d+)?)$"
)


def normalize_global_config_argv(argv: list[str]) -> list[str]:
    """Fix common GlobalConfig CLI typos before Typer parses ``sys.argv``."""
    out: list[str] = []
    for arg in argv:
        match = _OBSTACLE_MVP_HEIGHT_CM_STUCK_RE.match(arg)
        if match is not None:
            out.append(f"--{_OBSTACLE_MVP_HEIGHT_CM_FLAG}")
            out.append(match.group("value"))
            continue
        out.append(arg)
    return out


def cli_flag_explicitly_set(flag: str, argv: list[str] | None = None) -> bool:
    """Return True if ``flag`` was passed on the CLI (``--flag`` or ``--flag=value``)."""
    argv = argv or sys.argv
    prefix = f"--{flag.replace('_', '-')}"
    return any(arg == prefix or arg.startswith(f"{prefix}=") for arg in argv)


def apply_simulation_viewer_defaults(
    cli_overrides: dict[str, Any],
    *,
    current: GlobalConfig | None = None,
    argv: list[str] | None = None,
) -> dict[str, Any]:
    """Default DimOS visualization for simulator runs when ``--viewer`` was not passed.

    MuJoCo / DimSim stacks benefit from Rerun + the command center (localhost:7779) for
    costmaps, paths, and point clouds. Explicit ``--viewer none`` (or any other viewer)
    is always respected.
    """
    if cli_flag_explicitly_set("viewer", argv):
        return cli_overrides

    cfg = current or global_config
    simulation = cli_overrides.get("simulation", cfg.simulation)
    if not simulation:
        return cli_overrides

    merged = dict(cli_overrides)
    merged["viewer"] = "rerun"
    merged.setdefault("rerun_web", False)
    merged.setdefault("rerun_open", "native")
    return merged


global_config = GlobalConfig()
