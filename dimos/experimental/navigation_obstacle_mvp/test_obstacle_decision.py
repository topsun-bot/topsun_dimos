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

import pytest

from dimos.experimental.navigation_obstacle_mvp.obstacle_decision import (
    ThresholdCrossingPlanner,
    ThresholdObstacle,
)


def test_ignores_obstacle_below_ten_cm_trigger() -> None:
    planner = ThresholdCrossingPlanner()

    decision = planner.evaluate(
        ThresholdObstacle(
            height_m=0.08,
            width_m=0.12,
            distance_m=0.30,
            lateral_clearance_m=0.30,
        )
    )

    assert decision.kind == "ignore"
    assert not decision.actions


def test_crosses_twelve_cm_threshold_with_physical_sequence() -> None:
    planner = ThresholdCrossingPlanner()

    decision = planner.evaluate(
        ThresholdObstacle(
            height_m=0.12,
            width_m=0.16,
            distance_m=0.35,
            lateral_clearance_m=0.30,
        )
    )

    assert decision.kind == "cross"
    assert decision.can_cross
    assert [action.kind for action in decision.actions] == [
        "sport",
        "sport",
        "sport",
        "wait",
        "velocity",
        "stop",
        "velocity",
        "stop",
        "sport",
        "sport",
    ]
    assert decision.actions[1].label == "raise feet to 0.16m"
    assert decision.actions[4].linear_x_mps > 0.0
    assert decision.actions[6].linear_x_mps > decision.actions[4].linear_x_mps


def test_rejects_obstacle_above_crossing_limit() -> None:
    planner = ThresholdCrossingPlanner()

    decision = planner.evaluate(
        ThresholdObstacle(
            height_m=0.25,
            width_m=0.10,
            distance_m=0.30,
            lateral_clearance_m=0.30,
        )
    )

    assert decision.kind == "avoid"
    assert "height" in decision.reason


def test_rejects_wide_obstacle_as_non_threshold() -> None:
    planner = ThresholdCrossingPlanner()

    decision = planner.evaluate(
        ThresholdObstacle(
            height_m=0.12,
            width_m=0.80,
            distance_m=0.30,
            lateral_clearance_m=0.30,
        )
    )

    assert decision.kind == "avoid"
    assert "width" in decision.reason


def test_requires_alignment_before_crossing() -> None:
    planner = ThresholdCrossingPlanner()

    decision = planner.evaluate(
        ThresholdObstacle(
            height_m=0.12,
            width_m=0.10,
            distance_m=0.30,
            lateral_clearance_m=0.30,
            heading_error_rad=0.60,
        )
    )

    assert decision.kind == "align"
    assert not decision.actions


def test_rejects_negative_obstacle_dimensions() -> None:
    with pytest.raises(ValueError, match="height_m"):
        ThresholdObstacle(
            height_m=-0.01,
            width_m=0.10,
            distance_m=0.30,
            lateral_clearance_m=0.30,
        )
