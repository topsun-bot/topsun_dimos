# Copyright 2026 Dimensional Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

from __future__ import annotations

from typing import Any

from dimos.core.global_config import GlobalConfig
from dimos.msgs.geometry_msgs.PoseStamped import PoseStamped
from dimos.msgs.geometry_msgs.Quaternion import Quaternion
from dimos.msgs.geometry_msgs.Twist import Twist
from dimos.msgs.geometry_msgs.Vector3 import Vector3
from dimos.robot.unitree.go2.stair_locomotion.config import StairLocomotionConfig
from dimos.robot.unitree.go2.stair_locomotion.sport_api import Go2StairSportController
from dimos.simulation.mujoco.build_scene_stairs import NUM_STEPS, STAIR_START_X, TREAD_M, stair_top_goal_xy
from unitree_webrtc_connect.constants import RTC_TOPIC


class _MockConnection:
    moves: list[Twist]
    sport_payloads: list[dict[str, Any]]

    def __init__(self) -> None:
        self.moves = []
        self.sport_payloads = []

    def move(self, twist: Twist, duration: float = 0.0) -> bool:
        self.moves.append(twist)
        return True

    def balance_stand(self) -> bool:
        return True

    def free_walk(self) -> bool:
        return True

    def set_obstacle_avoidance(self, enabled: bool = True) -> None:
        pass

    def publish_request(self, topic: str, data: dict[str, Any]) -> dict[str, Any]:
        if topic == RTC_TOPIC["SPORT_MOD"]:
            self.sport_payloads.append(data)
        return {"status": "ok"}


class _MockNavigation:
    last_goal: PoseStamped | None = None

    def set_goal(self, goal: PoseStamped) -> bool:
        self.last_goal = goal
        return True

    def get_state(self) -> Any:
        from dimos.navigation.base import NavigationState

        return NavigationState.IDLE

    def is_goal_reached(self) -> bool:
        return False

    def cancel_goal(self) -> bool:
        return True


def test_stair_top_goal_xy_matches_last_tread() -> None:
    x, y = stair_top_goal_xy()
    expected_x = STAIR_START_X + (NUM_STEPS - 0.5) * TREAD_M
    assert abs(x - expected_x) < 0.02
    assert y == 0.0


def test_climb_stairs_skill_logic() -> None:
    """Exercise sport + navigation wiring without starting the full Module (LCM)."""
    conn = _MockConnection()
    nav = _MockNavigation()
    sport = Go2StairSportController(
        conn,
        StairLocomotionConfig(),
        global_config=GlobalConfig(simulation=True, stair_navigation=True),
    )
    sport.enter_stair_mode()
    top_x, top_y = stair_top_goal_xy()
    nav.set_goal(
        PoseStamped(
            position=Vector3(top_x, top_y, 0.0),
            orientation=Quaternion.from_euler(Vector3(0.0, 0.0, 0.0)),
            frame_id="world",
        )
    )
    assert nav.last_goal is not None
    assert abs(nav.last_goal.position.x - top_x) < 0.01
    assert any(p.get("api_id") == 1049 for p in conn.sport_payloads)
    sport.exit_stair_mode()


def test_move_skill_calls_connection_move() -> None:
    conn = _MockConnection()
    twist = Twist(linear=Vector3(0.3, 0.0, 0.0), angular=Vector3(0.0, 0.0, 0.1))
    conn.move(twist, duration=1.0)
    assert conn.moves[0].linear.x == 0.3
