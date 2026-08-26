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

import math

import numpy as np
import pytest

from dimos.core.global_config import GlobalConfig
from dimos.msgs.geometry_msgs.PoseStamped import PoseStamped
from dimos.msgs.geometry_msgs.Quaternion import Quaternion
from dimos.msgs.geometry_msgs.Vector3 import Vector3
from dimos.navigation.replanning_a_star.controllers import PController


def test_rotate_respects_controller_velocity_bounds_on_hardware() -> None:
    controller = PController(
        GlobalConfig(_env_file=None, simulation=""),
        speed=0.55,
        control_frequency=10.0,
    )

    small_error = controller.rotate(0.1)
    large_error = controller.rotate(10.0)

    assert small_error.angular.z == pytest.approx(0.2)
    assert large_error.angular.z == pytest.approx(0.55)


def test_simulation_yaw_deadband_floor_does_not_exceed_controller_speed() -> None:
    controller = PController(
        GlobalConfig(_env_file=None, simulation="mujoco"),
        speed=0.55,
        control_frequency=10.0,
    )

    positive = controller.rotate(0.1)
    negative = controller.rotate(-0.1)
    large = controller.rotate(10.0)

    assert positive.angular.z == pytest.approx(0.55)
    assert negative.angular.z == pytest.approx(-0.55)
    assert large.angular.z == pytest.approx(0.55)


def test_mujoco_path_following_uses_stop_turn_drive() -> None:
    controller = PController(
        GlobalConfig(_env_file=None, simulation="mujoco"),
        speed=0.55,
        control_frequency=10.0,
    )
    odom = PoseStamped(
        position=Vector3(0.0, 0.0, 0.0),
        orientation=Quaternion.from_euler(Vector3(0.0, 0.0, 0.0)),
    )

    aligned = controller.advance(
        np.array([math.cos(math.radians(10)), math.sin(math.radians(10))]),
        odom,
    )
    misaligned = controller.advance(
        np.array([math.cos(math.radians(30)), math.sin(math.radians(30))]),
        odom,
    )

    assert aligned.linear.x > 0.0
    assert aligned.angular.z == 0.0
    assert misaligned.linear.x == 0.0
    assert misaligned.angular.z == pytest.approx(0.55)
