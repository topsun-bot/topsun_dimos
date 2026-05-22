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

"""MuJoCo staircase XML fragments for office scenes."""

from __future__ import annotations

# 20 treads: 15 cm run (depth), 2 m width, 15 cm rise per step.
STAIR_COUNT = 20
STAIR_RUN_M = 0.15
STAIR_WIDTH_M = 2.0
STAIR_RISE_M = 0.15
# Bottom-front corner of the first tread (world frame, meters).
STAIR_ORIGIN_XYZ = (3.0, 0.0, 0.0)

STAIR_MATERIAL_XML = (
    '    <material name="mat_stair" rgba="0.55 0.52 0.48 1" '
    'texture="tex_woodFloor_diffuse" shininess="0.1953125" />\n'
)


def staircase_asset_xml() -> str:
    return STAIR_MATERIAL_XML


def staircase_worldbody_xml() -> str:
    """Box treads climbing +X; width along Y centered on origin."""
    ox, oy, oz = STAIR_ORIGIN_XYZ
    half_w = STAIR_WIDTH_M / 2.0
    half_run = STAIR_RUN_M / 2.0
    half_rise = STAIR_RISE_M / 2.0

    lines = [
        f'    <body name="staircase" pos="{ox} {oy} {oz}">',
        "      <!-- 20 steps: 0.15 m run x 2.0 m width x 0.15 m rise -->",
    ]
    for i in range(STAIR_COUNT):
        cx = (i + 0.5) * STAIR_RUN_M
        cz = (i + 0.5) * STAIR_RISE_M
        n = i + 1
        pos = f"{cx:.3f} 0 {cz:.3f}"
        half = f"{half_run:.3f} {half_w:.3f} {half_rise:.3f}"
        lines.append(
            f'      <geom name="stair_step_{n:02d}" type="box" '
            f'size="{half}" pos="{pos}" material="mat_stair" friction="1.0 0.005 0.0001" />'
        )
        # Collision for LiDAR depth (matches office convex-hull convention).
        lines.append(
            f'      <geom name="stair_step_{n:02d}_collision" type="box" '
            f'size="{half}" pos="{pos}" material="mat_invisible" />'
        )
    lines.append("    </body>")
    return "\n".join(lines) + "\n"
