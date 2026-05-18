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

from dimos.msgs.geometry_msgs.Twist import Twist
from dimos.msgs.geometry_msgs.Vector3 import Vector3
from dimos.robot.unitree.go2.stair_locomotion.config import StairLocomotionConfig


def apply_stair_twist_limit(
    twist: Twist,
    config: StairLocomotionConfig,
    *,
    on_stair: bool,
) -> Twist:
    """Clamp linear.x for stair traversal; zero lateral velocity on stairs."""
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
