#!/usr/bin/env python3
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

"""Build ``scene_stairs.xml``: uniform 15 cm straight stairs along +x."""

from __future__ import annotations

import os
from pathlib import Path

from dimos.simulation.mujoco.model import _get_data_dir

_UNITREE_SCENES_DIR = (
    Path(__file__).resolve().parent.parent / "unitree_mujoco" / "scenes"
)
_UNITREE_STAIRS_LOCAL = _UNITREE_SCENES_DIR / "scene_dimos_stairs_local.xml"
_UNITREE_STAIRS_REPO = _UNITREE_SCENES_DIR / "scene_dimos_stairs.xml"
_UNITREE_GO2_INCLUDE_REPO = (
    "../../../../third_party/unitree_mujoco/unitree_robots/go2/go2.xml"
)

_UNITREE_SENSOR_RIG = """\
    <!-- DimOS perception rig (welded to Go2 base_link; no freejoint — extra DOFs break ONNX qpos[7:]) -->
    <body name="dimos_sensor_rig" pos="0 0 0">
      <geom name="dimos_sensor_anchor" type="sphere" size="0.005" mass="0.02"
        rgba="0 0 0 0" contype="0" conaffinity="0"/>
      <camera name="head_camera" pos="0.28 0 0.12" fovy="45"/>
      <camera name="lidar_front_camera" pos="0.30 0 0.22" fovy="160"/>
      <camera name="lidar_left_camera" pos="0.12 0.18 0.22" fovy="160"/>
      <camera name="lidar_right_camera" pos="0.12 -0.18 0.22" fovy="160"/>
    </body>
  </worldbody>
  <equality>
    <weld body1="dimos_sensor_rig" body2="base_link" solref="0.002 1"/>
  </equality>
"""

# Uniform straight stairs: 15 cm riser (fixed); tread depth sets slope angle in MuJoCo.
NUM_STEPS = 10
RISER_M = 0.15
# Tread depth sets slope: 28 cm ≈ 28°, 32 cm ≈ 25°, 36 cm ≈ 23° (15 cm riser fixed).
# Default 32 cm for `dimos run` (gentler). Override: DIMOS_STAIR_TREAD_M=0.28 for CI headless test.
TREAD_M = float(os.environ.get("DIMOS_STAIR_TREAD_M", "0.32"))
STAIR_WIDTH_M = 1.2
STAIR_START_Y = 0.0

# Main run starts after flat floor (approach mini-steps tended to trip Go1 ONNX).
APPROACH_STEPS = 0
APPROACH_RISER_M = 0.0
APPROACH_TREAD_M = 0.0
APPROACH_START_X = 2.55
STAIR_START_X = 2.55

# MuJoCo: 3 sub-treads per 15 cm riser (5 cm each) — macro landing still 15 cm apart.
SUB_TREADS_PER_STEP = 3
SUB_RISER_M = RISER_M / SUB_TREADS_PER_STEP
SUB_TREAD_M = TREAD_M / SUB_TREADS_PER_STEP

SCENE_MARKER = "v8_uniform_15cm_gentle_32cm_tread"

STAIR_ASSET_SNIPPET = """\
    <material name="mat_stair" rgba="0.55 0.45 0.35 1.0"/>
"""

STAIR_WORLD_SNIPPET = f"""\
    <!-- {SCENE_MARKER}: {NUM_STEPS} x {RISER_M:.2f} m riser, {TREAD_M:.2f} m tread (+x) -->
"""


def _approach_geom_lines() -> list[str]:
    if APPROACH_STEPS <= 0:
        return []
    half_w = STAIR_WIDTH_M / 2.0
    half_t = APPROACH_TREAD_M / 2.0
    half_r = APPROACH_RISER_M / 2.0
    lines = [
        f"    <!-- approach: {APPROACH_STEPS} x {APPROACH_RISER_M:.3f} m before 15 cm run -->",
    ]
    for i in range(APPROACH_STEPS):
        cx = APPROACH_START_X + (i + 0.5) * APPROACH_TREAD_M
        cz = (i + 0.5) * APPROACH_RISER_M
        lines.append(
            f'    <geom name="stair_approach_{i}" type="box" '
            f'pos="{cx:.4f} {STAIR_START_Y:.4f} {cz:.4f}" '
            f'size="{half_t:.4f} {half_w:.4f} {half_r:.4f}" '
            f'material="mat_stair" contype="1" conaffinity="1" friction="1.15"/>'
        )
    return lines


def approach_top_z() -> float:
    return APPROACH_STEPS * APPROACH_RISER_M


def _approach_top_z() -> float:
    return approach_top_z()


def _main_stair_start_x() -> float:
    return STAIR_START_X


def _macro_step_origin_x(step_index: int) -> float:
    return STAIR_START_X + step_index * TREAD_M


def _step_center_x(step_index: int) -> float:
    """Center of macro tread (for planning / tests)."""
    return _macro_step_origin_x(step_index) + TREAD_M / 2.0


def stair_top_goal_xy() -> tuple[float, float]:
    """World-frame (x, y) at the top tread center of the default DimOS stair run."""
    return (_step_center_x(NUM_STEPS - 1), STAIR_START_Y)


def _stair_geom_lines() -> list[str]:
    half_w = STAIR_WIDTH_M / 2.0
    half_sub_t = SUB_TREAD_M / 2.0
    half_sub_r = SUB_RISER_M / 2.0
    lines = [STAIR_WORLD_SNIPPET, *_approach_geom_lines()]
    for i in range(NUM_STEPS):
        z_base = _approach_top_z() + i * RISER_M
        x_base = _macro_step_origin_x(i)
        for j in range(SUB_TREADS_PER_STEP):
            cx = x_base + (j + 0.5) * SUB_TREAD_M
            cz = z_base + (j + 0.5) * SUB_RISER_M
            name = f"stair_step_{i}_{j}"
            lines.append(
                f'    <geom name="{name}" type="box" '
                f'pos="{cx:.4f} {STAIR_START_Y:.4f} {cz:.4f}" '
                f'size="{half_sub_t:.4f} {half_w:.4f} {half_sub_r:.4f}" '
                f'material="mat_stair" contype="1" conaffinity="1" friction="1.2"/>'
            )
    return lines


def build_scene_stairs_xml(*, force: bool = False) -> Path:
    """Write ``scene_stairs.xml`` from ``scene_empty.xml`` plus uniform stair treads."""
    data_dir = Path(str(_get_data_dir()))
    source = data_dir / "scene_empty.xml"
    target = data_dir / "scene_stairs.xml"

    if not source.is_file():
        raise FileNotFoundError(
            f"Missing {source}. Run simulation once or: get_data('mujoco_sim') / git lfs pull."
        )

    if target.is_file() and not force:
        existing = target.read_text(encoding="utf-8")
        if SCENE_MARKER in existing and "scene_stairs" in existing:
            return target

    text = source.read_text(encoding="utf-8")
    text = text.replace('<mujoco model="scene_empty">', '<mujoco model="scene_stairs">', 1)

    if "mat_stair" not in text:
        text = text.replace("  </asset>", f"{STAIR_ASSET_SNIPPET}  </asset>", 1)

    floor_marker = '    <geom name="floor" size="0 0 0.01" type="plane" material="groundplane"/>'
    large_floor = (
        '    <geom name="floor" type="plane" size="20 20 0.1" pos="0 0 0" '
        'contype="1" conaffinity="1" priority="1" friction="1.0" material="groundplane"/>'
    )
    if floor_marker not in text:
        raise RuntimeError("scene_empty.xml layout changed; update build_scene_stairs.py")

    stair_block = "\n".join(_stair_geom_lines()) + "\n"
    text = text.replace(floor_marker, large_floor + "\n" + stair_block, 1)

    target.write_text(text, encoding="utf-8")
    return target


def _unitree_stairs_mjcf(*, include_go2: str) -> str:
    mid_x = STAIR_START_X + (NUM_STEPS * TREAD_M) / 2.0
    mid_z = approach_top_z() + (NUM_STEPS * RISER_M) / 2.0
    stair_block = "\n".join(_stair_geom_lines())
    return f"""<mujoco model="go2 dimos stairs">
  <include file="{include_go2}"/>

  <statistic center="{mid_x:.2f} 0 {mid_z:.2f}" extent="4.0"/>

  <visual>
    <headlight diffuse="0.6 0.6 0.6" ambient="0.3 0.3 0.3" specular="0 0 0"/>
    <rgba haze="0.15 0.25 0.35 1"/>
    <global azimuth="-130" elevation="-20"/>
  </visual>

  <asset>
    <texture type="skybox" builtin="gradient" rgb1="0.3 0.5 0.7" rgb2="0 0 0" width="512" height="3072"/>
    <texture type="2d" name="groundplane" builtin="checker" mark="edge" rgb1="0.2 0.3 0.4" rgb2="0.1 0.2 0.3"
      markrgb="0.8 0.8 0.8" width="300" height="300"/>
    <material name="groundplane" texture="groundplane" texuniform="true" texrepeat="5 5" reflectance="0.2"/>
    <material name="mat_stair" rgba="0.55 0.45 0.35 1.0"/>
  </asset>

  <worldbody>
    <light pos="0 0 3" dir="0 0 -1" directional="true"/>
    <geom name="floor" type="plane" size="0 0 0.05" material="groundplane"/>
{stair_block}
{_UNITREE_SENSOR_RIG}
</mujoco>
"""


def build_unitree_scene_dimos_stairs_xml(*, force: bool = False) -> Path:
    """Write Unitree Go2 stairs MJCF (sub-tread geometry aligned with ``scene_stairs.xml``)."""
    _UNITREE_SCENES_DIR.mkdir(parents=True, exist_ok=True)

    if _UNITREE_STAIRS_LOCAL.is_file() and not force:
        existing = _UNITREE_STAIRS_LOCAL.read_text(encoding="utf-8")
        if SCENE_MARKER in existing and "stair_step_0_0" in existing:
            return _UNITREE_STAIRS_LOCAL

    local_text = _unitree_stairs_mjcf(include_go2="go2.xml")
    _UNITREE_STAIRS_LOCAL.write_text(local_text, encoding="utf-8")

    repo_text = _unitree_stairs_mjcf(include_go2=_UNITREE_GO2_INCLUDE_REPO)
    _UNITREE_STAIRS_REPO.write_text(repo_text, encoding="utf-8")
    if not local_text.rstrip().endswith("</mujoco>"):
        raise RuntimeError("Generated Unitree stairs MJCF is missing </mujoco>")
    return _UNITREE_STAIRS_LOCAL


if __name__ == "__main__":
    path = build_scene_stairs_xml(force=True)
    unitree_path = build_unitree_scene_dimos_stairs_xml(force=True)
    print(f"Wrote {path}")
    print(f"Wrote {unitree_path}")
