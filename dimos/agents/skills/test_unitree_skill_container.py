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

import difflib
from typing import Any
from unittest.mock import MagicMock

from langchain_core.messages import HumanMessage
from unitree_webrtc_connect.constants import RTC_TOPIC

from dimos.core.core import rpc
from dimos.core.module import Module
from dimos.msgs.geometry_msgs.PoseStamped import PoseStamped
from dimos.msgs.geometry_msgs.Twist import Twist
from dimos.navigation.base import NavigationState
from dimos.robot.unitree.unitree_skill_container import _UNITREE_COMMANDS, UnitreeSkillContainer


class StubNavigation(Module):
    @rpc
    def set_goal(self, goal: PoseStamped) -> bool:
        return True

    @rpc
    def get_state(self) -> NavigationState:
        return NavigationState.IDLE

    @rpc
    def is_goal_reached(self) -> bool:
        return False

    @rpc
    def cancel_goal(self) -> bool:
        return True


class StubGO2Connection(Module):
    @rpc
    def move(self, twist: Twist, duration: float = 0.0) -> bool:
        return True

    @rpc
    def publish_request(self, topic: str, data: dict[str, Any]) -> dict[Any, Any]:
        return {}


class MockedUnitreeSkill(UnitreeSkillContainer):
    pass


def test_pounce(agent_setup) -> None:
    history = agent_setup(
        blueprints=[
            MockedUnitreeSkill.blueprint(),
            StubNavigation.blueprint(),
            StubGO2Connection.blueprint(),
        ],
        messages=[HumanMessage("Pounce! Use the execute_sport_command tool.")],
    )

    response = history[-1].content.lower()
    assert "pounce" in response


def test_did_you_mean() -> None:
    suggestions = difflib.get_close_matches("Pounce", _UNITREE_COMMANDS.keys(), n=3, cutoff=0.6)
    assert "FrontPounce" in suggestions
    assert "Pose" in suggestions


def test_go2_dance_sport_uses_publish_request() -> None:
    fake_conn = MagicMock()
    fake_conn.publish_request = MagicMock(return_value={})
    fake_conn.move = MagicMock(return_value=True)
    ctrl = MagicMock()
    ctrl.unitree_connection_type = "webrtc"
    cfg = MagicMock()
    cfg.g = ctrl

    bot = object.__new__(UnitreeSkillContainer)
    bot.config = cfg
    bot._connection = fake_conn

    UnitreeSkillContainer.go2_dance(bot, phrase_seconds=0.0)

    topic = RTC_TOPIC["SPORT_MOD"]
    expected_ids = (
        _UNITREE_COMMANDS["Hello"][0],
        _UNITREE_COMMANDS["Content"][0],
        _UNITREE_COMMANDS["WiggleHips"][0],
    )
    calls = fake_conn.publish_request.call_args_list
    assert len(calls) == len(expected_ids)
    for call, exp in zip(calls, expected_ids, strict=True):
        assert call.args[0] == topic
        assert call.args[1] == {"api_id": exp}
    fake_conn.move.assert_not_called()


def test_go2_dance_mujoco_uses_move() -> None:
    fake_conn = MagicMock()
    fake_conn.publish_request = MagicMock(return_value={})
    fake_conn.move = MagicMock(return_value=True)
    ctrl = MagicMock()
    ctrl.unitree_connection_type = "mujoco"
    cfg = MagicMock()
    cfg.g = ctrl

    bot = object.__new__(UnitreeSkillContainer)
    bot.config = cfg
    bot._connection = fake_conn

    UnitreeSkillContainer.go2_dance(bot, phrase_seconds=0.1)

    assert fake_conn.move.call_count == 6
    fake_conn.publish_request.assert_not_called()
