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

"""Infer MVP sill height from robot pose in the MuJoCo office1 test corridor."""

from __future__ import annotations

from dataclasses import dataclass
import math

from dimos.msgs.geometry_msgs.PoseStamped import PoseStamped

# North corridor band in office1 MVP layout (spawn ≈ (-1, 1), +Y north).
_MVP_CORRIDOR_X = -1.12
_MVP_CORRIDOR_X_TOLERANCE_M = 1.2
_MVP_CORRIDOR_Y_MIN = 0.5
_MVP_CORRIDOR_Y_MAX = 5.5


@dataclass(frozen=True)
class MvpSill:
    """Static navigation-test threshold in office1 north corridor."""

    name: str
    x: float
    y: float
    height_cm: float
    capture_radius_m: float = 0.55
    forward_lookahead_m: float = 1.0
    lateral_tolerance_m: float = 0.9


# Must match `dimos/simulation/mujoco/model.py` sill centers (spawn ≈ (-1, 1), +Y north).
MVP_SILLS: tuple[MvpSill, ...] = (
    MvpSill(
        "mvp_threshold_low",
        -1.12,
        1.45,
        5.0,
        capture_radius_m=0.65,
        forward_lookahead_m=0.7,
        lateral_tolerance_m=0.5,
    ),
    MvpSill("mvp_threshold_medium", -1.12, 3.15, 20.0),
    MvpSill("mvp_threshold_high", -1.12, 4.85, 58.0),
)

# Legacy geom names (pre-rename) map to the same heights for fake LiDAR sampling.
MVP_SILLS_BY_LEGACY_GEOM: dict[str, MvpSill] = {
    "mvp_obstacle_low_step": MVP_SILLS[0],
    "mvp_obstacle_medium_cylinder": MVP_SILLS[1],
    "mvp_obstacle_tall_box": MVP_SILLS[2],
    "mvp_threshold_low": MVP_SILLS[0],
    "mvp_threshold_medium": MVP_SILLS[1],
    "mvp_threshold_high": MVP_SILLS[2],
}


def in_mvp_corridor(robot_x: float, robot_y: float) -> bool:
    """True when the robot is in the north MVP test corridor."""
    if abs(robot_x - _MVP_CORRIDOR_X) > _MVP_CORRIDOR_X_TOLERANCE_M:
        return False
    return _MVP_CORRIDOR_Y_MIN <= robot_y <= _MVP_CORRIDOR_Y_MAX


def infer_forward_obstacle_height_cm(
    robot_x: float,
    robot_y: float,
    *,
    forward_yaw: float | None = None,
) -> float | None:
    """Return sill height (cm) when the robot is near an MVP threshold ahead."""
    if forward_yaw is None:
        best: tuple[float, float] | None = None
        for sill in MVP_SILLS:
            dist = math.hypot(sill.x - robot_x, sill.y - robot_y)
            if dist > sill.capture_radius_m:
                continue
            if best is None or dist < best[0]:
                best = (dist, sill.height_cm)
        if best is None:
            return None
        return best[1]

    fx = math.cos(forward_yaw)
    fy = math.sin(forward_yaw)
    best_forward: float | None = None
    best_height: float | None = None

    for sill in MVP_SILLS:
        dx = sill.x - robot_x
        dy = sill.y - robot_y
        forward_dist = dx * fx + dy * fy
        lateral_dist = abs(-dx * fy + dy * fx)
        if forward_dist < -0.15:
            continue

        in_corridor = (
            forward_dist <= sill.forward_lookahead_m and lateral_dist <= sill.lateral_tolerance_m
        )
        in_ball = math.hypot(dx, dy) <= sill.capture_radius_m
        if not (in_corridor or in_ball):
            continue

        if best_forward is None or forward_dist < best_forward:
            best_forward = forward_dist
            best_height = sill.height_cm

    return best_height


def resolve_obstacle_height_cm(
    *,
    configured_height_cm: float | None,
    current_odom: PoseStamped | None,
) -> float | None:
    """Prefer sill inference in the MVP corridor; fall back to CLI-injected height."""
    if current_odom is not None:
        inferred = infer_forward_obstacle_height_cm(
            current_odom.position.x,
            current_odom.position.y,
            forward_yaw=current_odom.orientation.euler[2],
        )
        if inferred is not None:
            return inferred
        if in_mvp_corridor(current_odom.position.x, current_odom.position.y):
            # Do not treat corridor obstacles as the low-cross CLI default (e.g. 8 cm).
            return None
    return configured_height_cm


def nearest_non_cross_sill_distance_m(
    robot_x: float,
    robot_y: float,
) -> tuple[float, float] | None:
    """Return (distance_m, height_cm) to the nearest medium/high sill, if any."""
    best: tuple[float, float] | None = None
    for sill in MVP_SILLS[1:]:
        dist = math.hypot(sill.x - robot_x, sill.y - robot_y)
        if best is None or dist < best[0]:
            best = (dist, sill.height_cm)
    return best
