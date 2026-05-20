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

"""Shared interfaces for image-conditioned trajectory prediction engines."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

import numpy as np
from PIL import Image as PILImage


@dataclass
class TrajectoryInferenceResult:
    """Raw and processed outputs from one trajectory-model forward pass."""

    trajectories: np.ndarray  # (num_samples, len_traj_pred, 2) in body frame (m)
    chosen_waypoint: np.ndarray  # (2,) body frame (m)


class TrajectoryNavigationRuntimeError(RuntimeError):
    pass


class TrajectoryNavigationEngine(Protocol):
    """Interface implemented by NoMaD, NavDP, or future trajectory backends."""

    @property
    def ready(self) -> bool: ...

    def initialize(self) -> None: ...

    def push_observation(self, pil_image: PILImage.Image) -> None: ...

    def context_ready(self) -> bool: ...

    def infer_exploration(self) -> TrajectoryInferenceResult: ...


def dimos_image_to_pil(image: Any) -> PILImage.Image:
    """Convert DimOS sensor_msgs.Image to PIL RGB."""
    rgb = image.to_rgb()
    arr = rgb.as_numpy()
    if arr.dtype != np.uint8:
        arr = np.clip(arr, 0, 255).astype(np.uint8)
    return PILImage.fromarray(arr)
