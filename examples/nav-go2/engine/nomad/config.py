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

"""Configuration for NoMaD / visualnav-transformer integration."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Self

import yaml

from trajectory_planner_config import TrajectoryLocalPlannerConfig

NAV_GO2_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_NAV_CONFIG = NAV_GO2_ROOT / "config" / "nomad_nav.yaml"


def _path_from_env(name: str) -> Path | None:
    value = os.environ.get(name, "").strip()
    if not value:
        return None
    return Path(value).expanduser().resolve()


class NoMaDConfig(TrajectoryLocalPlannerConfig):
    """Paths and inference parameters for NoMaD traversability."""

    # Root of https://github.com/robodhruv/visualnav-transformer (vint_release/)
    visualnav_root: str | None = None
    checkpoint_path: str | None = None
    model_config_path: str | None = None

    num_samples: int = 8
    waypoint_index: int = 2
    num_diffusion_iters: int | None = None  # default: read from model yaml

    # Exploration (goal-masked) mode — same as deployment/src/explore.py
    max_v: float = 0.45
    frame_rate: float = 4.0

    def resolved_visualnav_root(self) -> Path | None:
        if self.visualnav_root:
            return Path(self.visualnav_root).expanduser().resolve()
        return _path_from_env("VISUALNAV_ROOT")

    def resolved_checkpoint(self) -> Path | None:
        if self.checkpoint_path:
            return Path(self.checkpoint_path).expanduser().resolve()
        env = _path_from_env("NOMAD_CHECKPOINT")
        if env is not None:
            return env
        root = self.resolved_visualnav_root()
        if root is not None:
            candidate = root / "deployment" / "model_weights" / "nomad.pth"
            if candidate.is_file():
                return candidate
        return None

    def resolved_model_config(self) -> Path | None:
        if self.model_config_path:
            return Path(self.model_config_path).expanduser().resolve()
        env = _path_from_env("NOMAD_MODEL_CONFIG")
        if env is not None:
            return env
        root = self.resolved_visualnav_root()
        if root is not None:
            candidate = root / "train" / "config" / "nomad.yaml"
            if candidate.is_file():
                return candidate
        return None

    @classmethod
    def from_yaml(cls, path: Path | str) -> Self:
        """Load NoMaD settings from a YAML file (e.g. ``nomad_nav.yaml``)."""
        config_path = Path(path).expanduser().resolve()
        raw: dict[str, Any] = yaml.safe_load(config_path.read_text()) or {}

        inference_hz = raw.pop("inference_hz", None)
        if inference_hz is not None:
            raw["min_inference_interval_s"] = 1.0 / max(float(inference_hz), 0.1)

        for key in ("checkpoint_path", "model_config_path"):
            value = raw.get(key)
            if not value:
                continue
            resolved = Path(str(value)).expanduser()
            if not resolved.is_absolute():
                resolved = (config_path.parent / resolved).resolve()
            raw[key] = str(resolved)

        visualnav = raw.get("visualnav_root")
        if visualnav:
            p = Path(str(visualnav)).expanduser()
            if not p.is_absolute():
                p = (config_path.parent / p).resolve()
            raw["visualnav_root"] = str(p)

        # nomad_nav.yaml also holds WaypointFollower keys; ignore unknown fields.
        allowed = set(cls.model_fields.keys())
        filtered = {k: v for k, v in raw.items() if k in allowed}
        return cls.model_validate(filtered)

    @classmethod
    def load_default(cls) -> Self:
        """Load settings from ``config/nomad_nav.yaml`` under the nav-go2 example."""
        return cls.from_yaml(DEFAULT_NAV_CONFIG)
