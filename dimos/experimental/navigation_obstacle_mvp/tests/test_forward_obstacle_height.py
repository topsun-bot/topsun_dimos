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

from __future__ import annotations

from itertools import pairwise
import math

from dimos.experimental.navigation_obstacle_mvp.forward_obstacle_height import (
    MVP_SILLS,
    in_mvp_corridor,
    infer_forward_obstacle_height_cm,
    resolve_obstacle_height_cm,
)
from dimos.msgs.geometry_msgs.PoseStamped import PoseStamped
from dimos.msgs.geometry_msgs.Quaternion import Quaternion
from dimos.msgs.geometry_msgs.Vector3 import Vector3


def test_mvp_sill_center_spacing_at_least_1_5m() -> None:
    for left, right in pairwise(MVP_SILLS):
        spacing = math.hypot(right.x - left.x, right.y - left.y)
        assert spacing >= 1.5


def test_infer_low_sill_near_spawn() -> None:
    height = infer_forward_obstacle_height_cm(-1.0, 1.35, forward_yaw=math.pi / 2)
    assert height == 5.0


def test_infer_medium_sill() -> None:
    height = infer_forward_obstacle_height_cm(-1.05, 3.10, forward_yaw=math.pi / 2)
    assert height == 20.0


def test_infer_medium_sill_off_corridor_center() -> None:
    """Regression: lateral offset on medium approach must not fall back to low-cross CLI height."""
    height = infer_forward_obstacle_height_cm(-1.92, 3.04, forward_yaw=math.pi / 2)
    assert height == 20.0


def test_infer_high_sill() -> None:
    height = infer_forward_obstacle_height_cm(-1.05, 4.80, forward_yaw=math.pi / 2)
    assert height == 58.0


def test_resolve_prefers_inference_over_cli() -> None:
    pose = PoseStamped(
        position=Vector3(x=-1.0, y=3.10, z=0.0),
        orientation=Quaternion.from_euler(Vector3(0.0, 0.0, math.pi / 2)),
    )
    resolved = resolve_obstacle_height_cm(configured_height_cm=8.0, current_odom=pose)
    assert resolved == 20.0


def test_resolve_falls_back_to_configured() -> None:
    pose = PoseStamped(position=Vector3(x=0.0, y=0.0, z=0.0))
    resolved = resolve_obstacle_height_cm(configured_height_cm=8.0, current_odom=pose)
    assert resolved == 8.0


def test_resolve_in_corridor_without_sill_match_returns_none() -> None:
    pose = PoseStamped(
        position=Vector3(x=-1.92, y=2.0, z=0.0),
        orientation=Quaternion.from_euler(Vector3(0.0, 0.0, math.pi / 2)),
    )
    assert in_mvp_corridor(pose.position.x, pose.position.y)
    resolved = resolve_obstacle_height_cm(configured_height_cm=8.0, current_odom=pose)
    assert resolved is None
