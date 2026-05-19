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

from dimos.simulation.mujoco.build_scene_stairs import (
    NUM_STEPS,
    RISER_M,
    STAIR_START_X,
    TREAD_M,
    _stair_geom_lines,
)


def test_stair_geom_snippet_has_ten_discrete_treads() -> None:
    lines = _stair_geom_lines()
    step_lines = [ln for ln in lines if "stair_step_" in ln]
    assert len(step_lines) == NUM_STEPS == 10
    assert f'pos="{STAIR_START_X + 0.5 * TREAD_M:.4f}' in step_lines[0]
    assert f'pos="{STAIR_START_X + (9 + 0.5) * TREAD_M:.4f}' in step_lines[-1]
    # Each tread is one box (tread x width x riser), not a growing wedge.
    assert f'size="{TREAD_M / 2.0:.4f}' in step_lines[0]
    assert f"{RISER_M / 2.0:.4f}" in step_lines[0]
    assert f'size="{10 * TREAD_M / 2.0:.4f}' not in step_lines[-1]
