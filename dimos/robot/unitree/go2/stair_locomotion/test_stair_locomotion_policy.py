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

from unitree_webrtc_connect.constants import RTC_TOPIC, SPORT_CMD

from dimos.core.global_config import GlobalConfig
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
from dimos.robot.unitree.go2.stair_locomotion.sport_api import API_WALK_STAIR


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

    def balance_stand(self) -> bool:
        self.publish_request(RTC_TOPIC["SPORT_MOD"], {"api_id": SPORT_CMD["BalanceStand"]})
        return True

    def free_walk(self) -> bool:
        self.publish_request(RTC_TOPIC["SPORT_MOD"], {"api_id": SPORT_CMD["FreeWalk"]})
        return True

    def set_obstacle_avoidance(self, enabled: bool = True) -> None:
        self.publish_request(
            RTC_TOPIC["OBSTACLES_AVOID"],
            {"api_id": 1001, "parameter": {"enable": int(enabled)}},
        )


class RpcLikeConnection(MockGo2Connection):
    """Simulates ``ModuleProxy``: unknown attrs raise ``RuntimeError``, not ``AttributeError``."""

    def __getattr__(self, name: str) -> Any:
        if name in ("publish_request", "balance_stand", "free_walk", "set_obstacle_avoidance"):
            return object.__getattribute__(self, name)
        raise RuntimeError("Actor connection not available - cannot send requests")


def test_enter_stair_mode_with_rpc_proxy_connection() -> None:
    conn = RpcLikeConnection()
    policy = StairLocomotionPolicy(
        conn,
        StairLocomotionConfig(sport_settle_s=0.0),
        global_config=GlobalConfig(simulation=True),
    )
    policy._sport.enter_stair_mode()
    api_ids = [r["data"]["api_id"] for r in conn.requests]
    assert API_WALK_STAIR in api_ids


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
    cfg = StairLocomotionConfig(sport_settle_s=0.0)
    policy = StairLocomotionPolicy(
        conn,
        cfg,
        global_config=GlobalConfig(simulation=True),
    )
    policy.start(corridor, path)
    policy._phase = StairPhase.ON_STAIR
    policy._sport.enter_stair_mode()
    policy._on_stair_ticks = 20

    odom = _odom_at(cl[0][0], cl[0][1], yaw=corridor.axis_yaw)
    twist = policy.step(odom)
    assert cfg.min_linear_x <= twist.linear.x <= cfg.max_linear_x


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
    policy = StairLocomotionPolicy(conn, global_config=GlobalConfig(simulation=True))
    policy.start(corridor, path)
    policy._phase = StairPhase.ON_STAIR
    policy._on_stair_ticks = 20

    odom = _odom_at(cl[-1][0], cl[-1][1], yaw=corridor.axis_yaw)
    twist = policy.step(odom)
    assert twist.linear.x < 0.0


def test_pitch_during_grace_does_not_abort() -> None:
    corridor = synthetic_stair_corridor_ascending()
    cl = corridor.centerline
    path = plan_in_corridor(
        Vector3(cl[0][0], cl[0][1], 0.0),
        Vector3(cl[-1][0], cl[-1][1], 0.0),
        corridor,
    )
    assert path is not None

    conn = MockGo2Connection()
    policy = StairLocomotionPolicy(conn, global_config=GlobalConfig(simulation=True))
    policy.start(corridor, path)
    policy._phase = StairPhase.ON_STAIR
    policy._on_stair_ticks = 3

    odom = _odom_at(cl[0][0], cl[0][1], pitch=math.radians(24))
    policy.step(odom)
    assert not policy.aborted
    assert policy.phase == StairPhase.ON_STAIR


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
    policy = StairLocomotionPolicy(conn, global_config=GlobalConfig(simulation=True))
    policy.start(corridor, path)
    policy._phase = StairPhase.ON_STAIR
    policy._on_stair_ticks = 40

    odom = _odom_at(cl[0][0], cl[0][1], pitch=math.radians(42))
    twist = policy.step(odom)
    assert twist.linear.x == 0.0
    assert twist.angular.z == 0.0


def test_approach_skips_to_align_when_already_in_corridor() -> None:
    corridor = synthetic_stair_corridor_ascending()
    cl = corridor.centerline
    mid = len(cl) // 2
    path = plan_in_corridor(
        Vector3(cl[0][0], cl[0][1], 0.0),
        Vector3(cl[-1][0], cl[-1][1], 0.0),
        corridor,
    )
    assert path is not None

    conn = MockGo2Connection()
    policy = StairLocomotionPolicy(conn, global_config=GlobalConfig(simulation=True))
    policy.start(corridor, path)
    assert policy._phase == StairPhase.APPROACH

    odom = _odom_at(cl[mid][0], cl[mid][1], yaw=corridor.axis_yaw)
    twist = policy.step(odom)
    assert policy._phase == StairPhase.ALIGN
    assert twist.linear.x != 0.0


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
    cfg = StairLocomotionConfig(sport_settle_s=0.0)
    policy = StairLocomotionPolicy(conn, cfg)
    policy.start(corridor, path)
    policy._sport.enter_stair_mode()
    api_ids = [r["data"]["api_id"] for r in conn.requests]
    assert API_WALK_STAIR in api_ids
    assert SPORT_CMD["FreeWalk"] in api_ids
    assert SPORT_CMD["BalanceStand"] in api_ids
    walk_stair_req = next(r for r in conn.requests if r["data"]["api_id"] == API_WALK_STAIR)
    assert walk_stair_req["data"]["parameter"]["data"] is True


def test_manual_stair_mode_uses_foot_raise_height() -> None:
    corridor = synthetic_stair_corridor_ascending()
    cl = corridor.centerline
    path = plan_in_corridor(
        Vector3(cl[0][0], cl[0][1], 0.0),
        Vector3(cl[-1][0], cl[-1][1], 0.0),
        corridor,
    )
    assert path is not None

    conn = MockGo2Connection()
    cfg = StairLocomotionConfig(sport_settle_s=0.0, stair_sport_mode="manual")
    policy = StairLocomotionPolicy(conn, cfg)
    policy._sport.enter_stair_mode()
    api_ids = [r["data"]["api_id"] for r in conn.requests]
    assert SPORT_CMD["FootRaiseHeight"] in api_ids
    assert API_WALK_STAIR not in api_ids
