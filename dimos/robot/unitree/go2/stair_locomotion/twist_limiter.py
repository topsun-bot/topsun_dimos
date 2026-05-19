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

from __future__ import annotations

import math

from dimos.msgs.geometry_msgs.Twist import Twist
from dimos.msgs.geometry_msgs.Vector3 import Vector3
from dimos.navigation.stairs.contracts import StairCorridor
from dimos.robot.unitree.go2.stair_locomotion.config import StairLocomotionConfig
from dimos.utils.trigonometry import angle_diff


def apply_stair_twist_limit(
    twist: Twist,
    config: StairLocomotionConfig,
    *,
    on_stair: bool,
) -> Twist:
    """Clamp body-forward speed; zero lateral velocity while on stairs."""
    lin_x = twist.linear.x
    if on_stair:
        if lin_x >= 0:
            lin_x = max(config.min_linear_x, min(config.max_linear_x, lin_x))
        else:
            lin_x = -max(config.min_linear_x, min(config.max_linear_x, abs(lin_x)))
        return Twist(
            linear=Vector3(lin_x, 0.0, 0.0),
            angular=twist.angular,
        )

    max_abs = config.max_linear_x
    lin_x = max(-max_abs, min(max_abs, lin_x))
    return Twist(linear=Vector3(lin_x, twist.linear.y, twist.linear.z), angular=twist.angular)


def twist_along_corridor(
    speed_mps: float,
    robot_yaw: float,
    corridor: StairCorridor,
    config: StairLocomotionConfig,
    *,
    yaw_gain: float,
    on_stair: bool,
    sim_min_forward_ratio: float | None = None,
) -> Twist:
    """Body-frame cmd_vel with forward speed along the stair corridor axis."""
    yaw_err = angle_diff(corridor.axis_yaw, robot_yaw)
    # Project corridor-axis speed onto body forward (DimOS linear.x convention).
    forward = speed_mps * math.cos(yaw_err)
    if sim_min_forward_ratio is not None and abs(yaw_err) < math.pi * 0.55:
        forward = max(forward, speed_mps * sim_min_forward_ratio)
    twist = Twist(
        linear=Vector3(forward, 0.0, 0.0),
        angular=Vector3(0.0, 0.0, yaw_gain * yaw_err),
    )
    return apply_stair_twist_limit(twist, config, on_stair=on_stair)


def stair_aware_limit(
    twist: Twist,
    config: StairLocomotionConfig | None,
    *,
    stair_navigation_enabled: bool,
    on_stair: bool,
) -> Twist:
    """Optional wrapper for PController output when stair mode is active."""
    if not stair_navigation_enabled or config is None:
        return twist
    return apply_stair_twist_limit(twist, config, on_stair=on_stair)
