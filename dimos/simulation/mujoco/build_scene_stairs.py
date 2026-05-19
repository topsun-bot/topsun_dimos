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

"""Build a minimal ``scene_stairs.xml`` (open floor + discrete tread steps)."""

from __future__ import annotations

from pathlib import Path

from dimos.simulation.mujoco.model import _get_data_dir

# 10 treads, 15 cm riser (within 10–15 cm; matches StairDetectionConfig min_riser 0.15)
NUM_STEPS = 10
RISER_M = 0.15
TREAD_M = 0.28
STAIR_WIDTH_M = 1.2
STAIR_START_X = 3.0
STAIR_START_Y = 0.0

# Approach ramp: 5 mini treads (3 cm riser) before main 15 cm stairs.
TRANSITION_STEPS = 5
TRANSITION_RISER_M = 0.03
TRANSITION_TREAD_M = 0.16
TRANSITION_START_X = 2.2

# Bump to invalidate cached scene_stairs.xml when geometry changes.
SCENE_MARKER = "discrete_treads_15cm_v4_ramp"

STAIR_ASSET_SNIPPET = """\
    <material name="mat_stair" rgba="0.55 0.45 0.35 1.0"/>
"""

STAIR_WORLD_SNIPPET = f"""\
    <!-- {SCENE_MARKER}: {NUM_STEPS} treads, riser {RISER_M:.2f} m, tread {TREAD_M:.2f} m (+x) -->
"""


def _transition_geom_lines() -> list[str]:
    """Shallow ramp treads so the Go1 ONNX policy can enter the main stair run."""
    half_w = STAIR_WIDTH_M / 2.0
    half_t = TRANSITION_TREAD_M / 2.0
    half_r = TRANSITION_RISER_M / 2.0
    lines = [
        f'    <!-- approach ramp: {TRANSITION_STEPS} x {TRANSITION_RISER_M:.2f} m riser -->',
    ]
    for i in range(TRANSITION_STEPS):
        cx = TRANSITION_START_X + (i + 0.5) * TRANSITION_TREAD_M
        cz = (i + 0.5) * TRANSITION_RISER_M
        lines.append(
            f'    <geom name="stair_ramp_{i}" type="box" '
            f'pos="{cx:.4f} {STAIR_START_Y:.4f} {cz:.4f}" '
            f'size="{half_t:.4f} {half_w:.4f} {half_r:.4f}" '
            f'material="mat_stair" contype="1" conaffinity="1" friction="1.1"/>'
        )
    return lines


def _stair_geom_lines() -> list[str]:
    """One box per tread; tread i sits on top of tread i-1 (no monolithic wedge)."""
    half_w = STAIR_WIDTH_M / 2.0
    half_t = TREAD_M / 2.0
    half_r = RISER_M / 2.0
    lines = [STAIR_WORLD_SNIPPET, *_transition_geom_lines()]
    z_offset = TRANSITION_STEPS * TRANSITION_RISER_M
    for i in range(NUM_STEPS):
        cx = STAIR_START_X + (i + 0.5) * TREAD_M
        cz = z_offset + (i + 0.5) * RISER_M
        lines.append(
            f'    <geom name="stair_step_{i}" type="box" '
            f'pos="{cx:.4f} {STAIR_START_Y:.4f} {cz:.4f}" '
            f'size="{half_t:.4f} {half_w:.4f} {half_r:.4f}" '
            f'material="mat_stair" contype="1" conaffinity="1" friction="1.0"/>'
        )
    return lines


def build_scene_stairs_xml(*, force: bool = False) -> Path:
    """Write ``scene_stairs.xml`` from ``scene_empty.xml`` plus discrete stair treads."""
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


if __name__ == "__main__":
    path = build_scene_stairs_xml(force=True)
    print(f"Wrote {path}")
