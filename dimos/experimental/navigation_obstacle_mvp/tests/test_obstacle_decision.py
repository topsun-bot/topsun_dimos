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

import pytest

from dimos.experimental.navigation_obstacle_mvp.obstacle_decision import (
    ObstacleAction,
    decide_obstacle_action,
)


@pytest.mark.parametrize(
    ("height_cm", "expected"),
    [
        (None, "detour"),
        (0.0, "cross"),
        (5.0, "cross"),
        (10.0, "cross"),
        (10.01, "detour"),
        (25.0, "detour"),
        (50.0, "detour"),
        (50.01, "stop"),
        (100.0, "stop"),
    ],
)
def test_decide_obstacle_action_default_thresholds(
    height_cm: float | None, expected: ObstacleAction
) -> None:
    assert decide_obstacle_action(obstacle_height_cm=height_cm) == expected


@pytest.mark.parametrize(
    ("cross_max", "detour_max", "height", "expected"),
    [
        (15.0, 40.0, 15.0, "cross"),
        (15.0, 40.0, 15.01, "detour"),
        (15.0, 40.0, 40.0, "detour"),
        (15.0, 40.0, 40.01, "stop"),
    ],
)
def test_decide_obstacle_action_custom_thresholds(
    cross_max: float,
    detour_max: float,
    height: float,
    expected: ObstacleAction,
) -> None:
    assert (
        decide_obstacle_action(
            obstacle_height_cm=height,
            cross_max_cm=cross_max,
            detour_max_cm=detour_max,
        )
        == expected
    )
