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
from typing import Any

from dimos.msgs.geometry_msgs.PoseStamped import PoseStamped
from dimos.msgs.geometry_msgs.Quaternion import Quaternion
from dimos.msgs.geometry_msgs.Twist import Twist
from dimos.msgs.geometry_msgs.Vector3 import Vector3
from dimos.msgs.nav_msgs.Path import Path
from dimos.navigation.stairs.contracts import StairPhase
from dimos.navigation.stairs.fixtures_corridor import (
    synthetic_stair_corridor_ascending,
    synthetic_stair_corridor_descending,
)
from dimos.navigation.stairs.plan_in_corridor import plan_in_corridor
from dimos.robot.unitree.go2.stair_locomotion.config import StairLocomotionConfig
from dimos.robot.unitree.go2.stair_locomotion.locomotion_policy import StairLocomotionPolicy


class MockGo2Connection:
    def __init__(self) -> None:
        self.moves: list[Twist] = []
        self.requests: list[dict[str, Any]] = []

    def move(self, twist: Twist, duration: float = 0.0) -> bool:
        self.moves.append(twist)
        return True

    def publish_request(self, topic: str, data: dict[str, Any]) -> dict[str, Any]:
        self.requests.append({"topic": topic, "data": data})
        return {}


def _odom_at(x: float, y: float, yaw: float = 0.0, pitch: float = 0.0) -> PoseStamped:
    return PoseStamped(
        position=Vector3(x, y, 0.0),
        orientation=Quaternion.from_euler(Vector3(0.0, pitch, yaw)),
    )


def test_on_stair_linear_speed_bounded() -> None:
    corridor = synthetic_stair_corridor_ascending()
    cl = corridor.centerline
    path = plan_in_corridor(
        Vector3(cl[0][0], cl[0][1], 0.0),
        Vector3(cl[-1][0], cl[-1][1], 0.0),
        corridor,
    )
    assert path is not None

    conn = MockGo2Connection()
    cfg = StairLocomotionConfig()
    policy = StairLocomotionPolicy(conn, cfg)
    policy.start(corridor, path)

    policy._phase = StairPhase.ON_STAIR
    policy._configure_sport_for_stairs()

    odom = _odom_at(cl[0][0], cl[0][1], yaw=corridor.axis_yaw)
    twist = policy.step(odom)
    assert cfg.min_linear_x <= twist.linear.x <= cfg.max_linear_x
    assert len(conn.moves) >= 1


def test_descending_negative_linear_x() -> None:
    corridor = synthetic_stair_corridor_descending()
    cl = corridor.centerline
    path = plan_in_corridor(
        Vector3(cl[-1][0], cl[-1][1], 0.0),
        Vector3(cl[0][0], cl[0][1], 0.0),
        corridor,
    )
    assert path is not None

    conn = MockGo2Connection()
    policy = StairLocomotionPolicy(conn)
    policy.start(corridor, path)
    policy._phase = StairPhase.ON_STAIR

    odom = _odom_at(cl[-1][0], cl[-1][1], yaw=corridor.axis_yaw)
    twist = policy.step(odom)
    assert twist.linear.x < 0.0


def test_pitch_exceeds_threshold_stops() -> None:
    corridor = synthetic_stair_corridor_ascending()
    cl = corridor.centerline
    path = plan_in_corridor(
        Vector3(cl[0][0], cl[0][1], 0.0),
        Vector3(cl[-1][0], cl[-1][1], 0.0),
        corridor,
    )
    assert path is not None

    conn = MockGo2Connection()
    policy = StairLocomotionPolicy(conn)
    policy.start(corridor, path)
    policy._phase = StairPhase.ON_STAIR

    odom = _odom_at(cl[0][0], cl[0][1], pitch=math.radians(30))
    twist = policy.step(odom)
    assert twist.linear.x == 0.0
    assert twist.angular.z == 0.0
    assert conn.moves[-1].linear.x == 0.0


def test_sport_apis_called_on_enter_on_stair() -> None:
    corridor = synthetic_stair_corridor_ascending()
    cl = corridor.centerline
    path = plan_in_corridor(
        Vector3(cl[0][0], cl[0][1], 0.0),
        Vector3(cl[-1][0], cl[-1][1], 0.0),
        corridor,
    )
    assert path is not None

    conn = MockGo2Connection()
    policy = StairLocomotionPolicy(conn)
    policy.start(corridor, path)
    policy._configure_sport_for_stairs()
    api_ids = [r["data"]["api_id"] for r in conn.requests]
    assert 1014 in api_ids
    assert 1013 in api_ids
