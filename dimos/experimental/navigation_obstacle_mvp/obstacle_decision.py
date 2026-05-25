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

from __future__ import annotations

from typing import Literal, TypeAlias

ObstacleAction: TypeAlias = Literal["cross", "detour", "stop"]


def decide_obstacle_action(
    *,
    obstacle_height_cm: float | None,
    cross_max_cm: float = 10.0,
    detour_max_cm: float = 50.0,
) -> ObstacleAction:
    """Map an optional forward obstacle height to cross / detour / stop.

    Args:
        obstacle_height_cm: Estimated max height in centimeters ahead of the robot.
            ``None`` means unknown — returns ``detour`` (conservative: trigger replan).
        cross_max_cm: Heights at or below this are treated as traversable (~10cm MVP).
        detour_max_cm: Heights above ``cross_max_cm`` and at or below this request
            replan / detour. Above this value, navigation should hard-stop.

    Returns:
        ``cross``, ``detour``, or ``stop``.
    """
    if obstacle_height_cm is None:
        return "detour"

    if obstacle_height_cm <= cross_max_cm:
        return "cross"

    if obstacle_height_cm <= detour_max_cm:
        return "detour"

    return "stop"
