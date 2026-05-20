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

from dimos.core.module import ModuleConfig


class StepOverConfig(ModuleConfig):
    """Configuration for StepOverModule — indoor step-over-obstacle behavior.

    All distance units are meters unless otherwise noted.
    """

    # ── Geometry thresholds ──────────────────────────────────────────
    max_step_height_m: float = 0.10
    """Maximum obstacle top height relative to local ground to consider step-over."""

    min_belly_clearance_m: float = 0.12
    """Minimum clearance between robot underbody and obstacle top to allow step-over."""

    max_ledge_depth_m: float = 0.20
    """Maximum obstacle depth along the path direction to consider step-over."""

    isolated_obstacle_width_min_m: float = 0.10
    """Minimum width of an isolated obstacle to count as an obstacle (narrower = noise)."""

    isolated_obstacle_width_max_m: float = 0.50
    """Maximum width of an isolated obstacle to attempt step-over."""

    min_gap_width_m: float = 0.40
    """Minimum gap width between two obstacles for the robot to pass through."""

    ignore_noise_m: float = 0.05
    """Height variations below this threshold are treated as sensor noise and ignored."""

    # ── Distance parameters ──────────────────────────────────────────
    analyze_distance_m: float = 1.0
    """Start analyzing the ROI strip this far ahead of the robot."""

    execute_distance_m: float = 0.4
    """Start executing step-over behaviour (slowdown / allow passage) this far ahead."""

    roi_start_m: float = 0.25
    """Forward distance from base_link where the ROI strip begins."""

    roi_end_m: float = 0.55
    """Forward distance from base_link where the ROI strip ends."""

    roi_width_margin_m: float = 0.1
    """Extra lateral margin added to robot_width when sampling the ROI strip."""

    # ── Noise suppression (constraint 3) ─────────────────────────────
    stable_frames_required: int = 5
    """Number of consecutive frames the step-over condition must hold before triggering."""

    morph_open_iterations: int = 1
    """Number of morphological opening iterations for spatial noise removal in the ROI."""

    # ── Robot body ───────────────────────────────────────────────────
    body_bottom_height_m: float = 0.22
    """Height of the robot's underbody above ground in normal walking stance.

    Used to compute belly clearance over obstacles: g = body_bottom_height_m - h.
    Calibrate on real hardware.
    """

    # ── Planner hysteresis (constraint 2) ────────────────────────────
    planner_near_enter_m: float = 1.3
    """Distance to goal below which the planner switches to gradient (near) cost map."""

    planner_far_exit_m: float = 1.7
    """Distance to goal above which the planner switches back to voronoi (far) cost map."""

    # ── Speech ───────────────────────────────────────────────────────
    blocked_speech_text: str = "前方障碍无法通过"
    """Fixed phrase spoken when an obstacle cannot be stepped over and no detour exists."""
