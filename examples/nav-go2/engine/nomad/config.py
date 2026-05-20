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

from trajectory_planner_config import TrajectoryLocalPlannerConfig


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
