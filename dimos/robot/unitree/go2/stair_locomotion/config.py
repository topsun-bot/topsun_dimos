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
    """Stair climb tuning for 15 cm risers (Go2 Sport API + cmd_vel)."""

    min_linear_x: float = 0.15
    max_linear_x: float = 0.30
    riser_slowdown_factor: float = 0.55
    max_pitch_rad: float = math.radians(28.0)
    align_yaw_tolerance_rad: float = math.radians(12.0)
    approach_distance_m: float = 0.85
    # Sport API (FootRaiseHeight 1014) — ~12 cm for 15 cm riser stairs
    foot_raise_height_m: float = 0.12
    body_height_delta_m: float = -0.02
    gait_id: int = 0
    speed_level: int = 0
    use_economic_gait: bool = False
    # SDK2 ``CrossStep`` / WebRTC ``CrossStep`` 1302 — higher foot clearance in sim.
    use_cross_step: bool = False
    sport_settle_s: float = 0.25
    disable_obstacle_avoidance_on_stair: bool = True
    yaw_gain_approach: float = 0.5
    yaw_gain_align: float = 0.8
    yaw_gain_on_stair: float = 0.35
