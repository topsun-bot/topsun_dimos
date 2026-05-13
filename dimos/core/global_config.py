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

"""Global CLI/runtime settings (`GlobalConfig`).

Configuration precedence (lowest → highest, last wins):

1. Field defaults on this model
2. ``.env`` in the current working directory (via pydantic-settings)
3. ``dimos.local.toml`` — table ``[feishu]`` maps to Feishu webhook fields only
   (see ``dimos.local.example.toml``); path from ``DIMOS_LOCAL_CONFIG`` if set,
   else ``<cwd>/dimos.local.toml``, else ``<repo root>/dimos.local.toml`` when
   that file exists
4. Process environment variables (including names under ``AliasChoices``)
5. Explicit ``GlobalConfig(...)`` constructor arguments
6. ``global_config.update(...)`` and blueprint / CLI overrides at runtime
   (see ``ModuleCoordinator.build`` and the DimOS CLI)
"""

from __future__ import annotations

import os
from pathlib import Path
import re
import sys
from typing import Literal, TypeAlias

from pydantic import AliasChoices, Field
from pydantic_settings import (
    BaseSettings,
    InitSettingsSource,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
)

from dimos.constants import DIMOS_PROJECT_ROOT
from dimos.models.vl.types import VlModelName

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib  # type: ignore[no-redef]

ViewerBackend: TypeAlias = Literal["rerun", "rerun-web", "rerun-connect", "foxglove", "none"]

# Repo-root gitignored file; committed template is ``dimos.local.example.toml``.
DIMOS_LOCAL_CONFIG_FILENAME = "dimos.local.toml"

# 飞书自定义机器人 webhook：在 ``dimos.local.toml`` 中使用 ``[feishu]`` 节。
FEISHU_TOML_TABLE = "feishu"


def _resolve_dimos_local_toml_path() -> Path | None:
    """Return the path to the optional local TOML file, or None if absent."""
    env_path = os.environ.get("DIMOS_LOCAL_CONFIG")
    if env_path:
        p = Path(env_path).expanduser()
        return p if p.is_file() else None
    cwd_file = Path.cwd() / DIMOS_LOCAL_CONFIG_FILENAME
    if cwd_file.is_file():
        return cwd_file
    root_file = DIMOS_PROJECT_ROOT / DIMOS_LOCAL_CONFIG_FILENAME
    if root_file.is_file():
        return root_file
    return None


def _feishu_init_kwargs_from_dimos_local_toml() -> dict[str, object]:
    """Load ``[feishu]`` from ``dimos.local.toml`` into flat ``GlobalConfig`` keys."""
    path = _resolve_dimos_local_toml_path()
    if path is None:
        return {}
    try:
        with path.open("rb") as f:
            data = tomllib.load(f)
    except OSError:
        return {}
    except tomllib.TOMLDecodeError:
        return {}

    section = data.get(FEISHU_TOML_TABLE)
    if not isinstance(section, dict):
        return {}

    out: dict[str, object] = {}
    if "webhook_url" in section and section["webhook_url"] is not None:
        url = section["webhook_url"]
        out["feishu_webhook_url"] = None if url == "" else url
    if "webhook_secret" in section and section["webhook_secret"] is not None:
        sec = section["webhook_secret"]
        out["feishu_webhook_secret"] = None if sec == "" else sec
    if "min_interval_s" in section and section["min_interval_s"] is not None:
        out["feishu_min_interval_s"] = section["min_interval_s"]
    return out


def _get_all_numbers(s: str) -> list[float]:
    return [float(x) for x in re.findall(r"-?\d+\.?\d*", s)]


class GlobalConfig(BaseSettings):
    """Process-wide settings; precedence is described in the module docstring."""

    robot_ip: str | None = None
    robot_ips: str | None = None
    xarm7_ip: str | None = None
    xarm6_ip: str | None = None
    can_port: str | None = None
    simulation: bool = False
    replay: bool = False
    replay_db: str = "go2_short"
    new_memory: bool = False
    viewer: ViewerBackend = "rerun"
    n_workers: int = 2
    memory_limit: str = "auto"
    mujoco_camera_position: str | None = None
    mujoco_room: str | None = None
    mujoco_room_from_occupancy: str | None = None
    mujoco_global_costmap_from_occupancy: str | None = None
    mujoco_global_map_from_pointcloud: str | None = None
    mujoco_start_pos: str = "-1.0, 1.0"
    mujoco_steps_per_frame: int = 7
    robot_model: str | None = None
    robot_width: float = 0.3
    robot_rotation_diameter: float = 0.6
    nerf_speed: float = 1.0
    planner_robot_speed: float | None = None
    mcp_port: int = 9990
    dtop: bool = False
    obstacle_avoidance: bool = True
    detection_model: VlModelName = "moondream"
    listen_host: str = "127.0.0.1"
    # Feishu (Lark) bot: env vars or ``[feishu]`` in dimos.local.toml (see FEISHU_TOML_TABLE).
    feishu_webhook_url: str | None = Field(
        default=None,
        validation_alias=AliasChoices("DIMOS_FEISHU_WEBHOOK_URL", "FEISHU_WEBHOOK_URL"),
    )
    feishu_webhook_secret: str | None = Field(
        default=None,
        validation_alias=AliasChoices("DIMOS_FEISHU_WEBHOOK_SECRET", "FEISHU_WEBHOOK_SECRET"),
    )
    feishu_min_interval_s: float = Field(
        default=60.0,
        validation_alias=AliasChoices("DIMOS_FEISHU_MIN_INTERVAL_S", "FEISHU_MIN_INTERVAL_S"),
    )

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
    )

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        """Order sources so env overrides local TOML and TOML overrides ``.env``."""
        feishu_local = InitSettingsSource(
            settings_cls,
            init_kwargs=_feishu_init_kwargs_from_dimos_local_toml(),
        )
        return (
            init_settings,
            env_settings,
            feishu_local,
            dotenv_settings,
            file_secret_settings,
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
            return "mujoco"
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


global_config = GlobalConfig()
