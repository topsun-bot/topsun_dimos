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
    APPROACH_STEPS,
    NUM_STEPS,
    RISER_M,
    SCENE_MARKER,
    SUB_TREADS_PER_STEP,
    TREAD_M,
    _main_stair_start_x,
    _stair_geom_lines,
    build_unitree_scene_dimos_stairs_xml,
)


def test_stair_geom_uniform_15cm_treads() -> None:
    lines = _stair_geom_lines()
    macro_geoms = [ln for ln in lines if 'name="stair_step_' in ln]
    assert len(macro_geoms) == NUM_STEPS * SUB_TREADS_PER_STEP
    if APPROACH_STEPS > 0:
        assert sum(1 for ln in lines if "stair_approach_" in ln) == APPROACH_STEPS
    assert SCENE_MARKER in lines[0]
    assert f"{RISER_M / SUB_TREADS_PER_STEP / 2.0:.4f}" in macro_geoms[0]
    assert f"{TREAD_M / 2.0:.4f}" in lines[0] or f"{TREAD_M:.2f}" in lines[0]
    assert not any("stair_climb_ramp" in ln for ln in lines)
    assert _main_stair_start_x() > 2.0


def test_build_unitree_stairs_scene_sub_treads() -> None:
    path = build_unitree_scene_dimos_stairs_xml(force=True)
    text = path.read_text(encoding="utf-8")
    assert SCENE_MARKER in text
    assert 'name="stair_step_0_0"' in text
    assert "dimos_sensor_rig" in text
    assert 'include file="go2.xml"' in text
    assert text.rstrip().endswith("</mujoco>")
    assert "<freejoint/>" not in text.split("dimos_sensor_rig")[1].split("</body>")[0]
