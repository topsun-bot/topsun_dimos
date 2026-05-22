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

"""MuJoCo threshold (door-sill portal) XML fragments for office scenes."""

from __future__ import annotations

# Portal: 3 m wide door (along Y) + 5 cm sill; wings seal to building edges on ±Y only.
THRESHOLD_DEPTH_M = 0.05
THRESHOLD_OPENING_WIDTH_M = 3.0
THRESHOLD_SILL_HEIGHT_M = 0.05
# Per-portal sill heights (m) for portals 1→3: 5 cm / 7 cm / 15 cm (near → far along −X).
THRESHOLD_SILL_HEIGHTS_M: tuple[float, ...] = (0.05, 0.07, 0.15)
THRESHOLD_SPACING_M = 2.0

OFFICE_WORLD_Y_MIN = -10.75
OFFICE_WORLD_Y_MAX = 8.75

THRESHOLD_FRAME_DEPTH_M = 0.25
THRESHOLD_FRAME_PANEL_HEIGHT_M = 2.5
THRESHOLD_LINTEL_THICKNESS_M = 0.08
THRESHOLD_PANEL_THICKNESS_M = 0.05

# Nearest portal (5 cm) at −1 m; each +1 index is 2 m farther along −X (10 cm, then 15 cm).
THRESHOLD_FIRST_CENTER_XYZ = (-1.0, 2.0, 0.0)
THRESHOLD_PORTAL_COUNT = 3

THRESHOLD_MATERIAL_XML = (
    '    <material name="mat_threshold" rgba="0.45 0.42 0.38 1" '
    'texture="tex_woodFloor_diffuse" shininess="0.1953125" />\n'
)


def thresholds_asset_xml() -> str:
    return THRESHOLD_MATERIAL_XML


def _box_geom_pair(
    name: str,
    half_x: float,
    half_y: float,
    half_z: float,
    cx: float,
    cy: float,
    cz: float,
) -> list[str]:
    half = f"{half_x:.3f} {half_y:.3f} {half_z:.3f}"
    pos = f"{cx:.3f} {cy:.3f} {cz:.3f}"
    friction = 'friction="1.0 0.005 0.0001"'
    return [
        f'      <geom name="{name}" type="box" size="{half}" pos="{pos}" '
        f'material="mat_threshold" {friction} />',
        f'      <geom name="{name}_collision" type="box" size="{half}" pos="{pos}" '
        f'material="mat_invisible" />',
    ]


def _wing_cheeks(
    name: str,
    center_x: float,
    y_lo: float,
    y_hi: float,
    half_frame_x: float,
    half_panel_z: float,
    panel_center_z: float,
    tag: str,
) -> list[str]:
    """Thin slabs on ±X of the frame, only over a Y span (never across the door)."""
    span = y_hi - y_lo
    if span < 0.05:
        return []
    cy = (y_lo + y_hi) / 2.0
    half_cheek_x = THRESHOLD_PANEL_THICKNESS_M / 2.0
    lines: list[str] = []
    for side, cheek_x in (
        ("front", center_x - half_frame_x - half_cheek_x),
        ("back", center_x + half_frame_x + half_cheek_x),
    ):
        lines.extend(
            _box_geom_pair(
                f"{name}_cheek_{tag}_{side}",
                half_cheek_x,
                span / 2.0,
                half_panel_z,
                cheek_x,
                cy,
                panel_center_z,
            )
        )
    return lines


def _portal_geoms(
    name: str, center_x: float, center_y: float, sill_height_m: float
) -> list[str]:
    """3 m door opening + sill; solid wings to building edge; no wall across the opening."""
    lines: list[str] = []
    half_open = THRESHOLD_OPENING_WIDTH_M / 2.0
    opening_y_lo = center_y - half_open
    opening_y_hi = center_y + half_open

    half_sill_z = sill_height_m / 2.0
    lines.extend(
        _box_geom_pair(
            f"{name}_sill",
            THRESHOLD_DEPTH_M / 2.0,
            half_open,
            half_sill_z,
            center_x,
            center_y,
            half_sill_z,
        )
    )

    half_frame_x = THRESHOLD_FRAME_DEPTH_M / 2.0
    half_panel_z = THRESHOLD_FRAME_PANEL_HEIGHT_M / 2.0
    panel_center_z = half_panel_z

    # Side walls (Y wings): building edge → door jamb.
    left_span = opening_y_lo - OFFICE_WORLD_Y_MIN
    if left_span > 0.05:
        left_cy = (OFFICE_WORLD_Y_MIN + opening_y_lo) / 2.0
        lines.extend(
            _box_geom_pair(
                f"{name}_wing_left",
                half_frame_x,
                left_span / 2.0,
                half_panel_z,
                center_x,
                left_cy,
                panel_center_z,
            )
        )
        lines.extend(
            _wing_cheeks(
                name,
                center_x,
                OFFICE_WORLD_Y_MIN,
                opening_y_lo,
                half_frame_x,
                half_panel_z,
                panel_center_z,
                "left",
            )
        )

    right_span = OFFICE_WORLD_Y_MAX - opening_y_hi
    if right_span > 0.05:
        right_cy = (opening_y_hi + OFFICE_WORLD_Y_MAX) / 2.0
        lines.extend(
            _box_geom_pair(
                f"{name}_wing_right",
                half_frame_x,
                right_span / 2.0,
                half_panel_z,
                center_x,
                right_cy,
                panel_center_z,
            )
        )
        lines.extend(
            _wing_cheeks(
                name,
                center_x,
                opening_y_hi,
                OFFICE_WORLD_Y_MAX,
                half_frame_x,
                half_panel_z,
                panel_center_z,
                "right",
            )
        )

    # Lintel above the 3 m door only (opening stays clear below this).
    lines.extend(
        _box_geom_pair(
            f"{name}_lintel",
            half_frame_x,
            half_open,
            THRESHOLD_LINTEL_THICKNESS_M / 2.0,
            center_x,
            center_y,
            THRESHOLD_FRAME_PANEL_HEIGHT_M - THRESHOLD_LINTEL_THICKNESS_M / 2.0,
        )
    )

    return lines


def thresholds_worldbody_xml() -> str:
    x0, y0, _ = THRESHOLD_FIRST_CENTER_XYZ
    lines = [
        '    <body name="thresholds">',
        f"      <!-- {THRESHOLD_PORTAL_COUNT} portals: {THRESHOLD_OPENING_WIDTH_M:.1f} m door; "
        f"sills {', '.join(f'{h * 100:.0f}cm' for h in THRESHOLD_SILL_HEIGHTS_M)} -->",
    ]
    for i in range(THRESHOLD_PORTAL_COUNT):
        cx = x0 - i * THRESHOLD_SPACING_M
        sill_h = THRESHOLD_SILL_HEIGHTS_M[i] if i < len(THRESHOLD_SILL_HEIGHTS_M) else THRESHOLD_SILL_HEIGHT_M
        lines.extend(_portal_geoms(f"threshold_{i + 1}", cx, y0, sill_h))
    lines.append("    </body>")
    return "\n".join(lines) + "\n"
