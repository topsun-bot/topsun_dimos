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

import math
from dataclasses import dataclass


@dataclass(frozen=True)
class StairLocomotionConfig:
    min_linear_x: float = 0.15
    max_linear_x: float = 0.35
    riser_slowdown_factor: float = 0.5
    max_pitch_rad: float = math.radians(25.0)
    align_yaw_tolerance_rad: float = math.radians(12.0)
    approach_distance_m: float = 0.6
    foot_raise_height_m: float = 0.12
    body_height_delta_m: float = -0.02
