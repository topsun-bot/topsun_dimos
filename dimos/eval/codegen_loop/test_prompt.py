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

import ast
import math

import numpy as np
import pytest

from dimos.eval.codegen_loop.prompt import (
    ProposeInput,
    _body_frame_move,
    build_refine_prompt,
    hardcoded_baseline,
)


@pytest.mark.parametrize(
    ("yaw_deg", "target", "expected_forward", "expected_left"),
    [
        (0.0, (1.0, 0.0), 1.0, 0.0),
        (0.0, (0.0, 1.0), 0.0, 1.0),
        (90.0, (0.0, 1.0), 1.0, 0.0),
        (90.0, (1.0, 0.0), 0.0, -1.0),
    ],
)
def test_world_target_is_projected_into_current_body_frame(
    yaw_deg: float,
    target: tuple[float, float],
    expected_forward: float,
    expected_left: float,
) -> None:
    forward, left, _ = _body_frame_move((0.0, 0.0), math.radians(yaw_deg), target)

    assert forward == pytest.approx(expected_forward, abs=1e-9)
    assert left == pytest.approx(expected_left, abs=1e-9)


def test_hardcoded_baseline_emits_valid_function_for_empty_route() -> None:
    source = hardcoded_baseline(
        ProposeInput(
            task_text="stay",
            start_xy=(0.0, 0.0),
            start_yaw_deg=0.0,
            waypoints=np.empty((0, 2), dtype=np.float64),
        )
    )

    ast.parse(source)
    assert "    pass" in source


def test_northbound_target_uses_lateral_body_motion_before_turn() -> None:
    source = hardcoded_baseline(
        ProposeInput(
            task_text="north",
            start_xy=(0.0, 0.0),
            start_yaw_deg=0.0,
            waypoints=np.array([[0.0, 1.0]], dtype=np.float64),
        )
    )

    assert "forward=0.00, left=1.00, degrees=+90.0" in source


def test_refine_prompt_treats_infinite_ate_as_missing_sim_trajectory() -> None:
    prompt = build_refine_prompt(
        prev_source="async def run(robot):\n    pass",
        ate_m=float("inf"),
        hit=0.0,
    )

    assert "无可对齐轨迹" in prompt
    assert "不要先归因于 waypoint" in prompt
    assert "轨迹整体匹配" not in prompt
