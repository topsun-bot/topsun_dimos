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
import math
from typing import Any

from langchain_core.messages import HumanMessage
import pytest

from dimos.core.core import rpc
from dimos.core.module import Module
from dimos.msgs.geometry_msgs.PoseStamped import PoseStamped
from dimos.msgs.geometry_msgs.Quaternion import Quaternion
from dimos.msgs.geometry_msgs.Transform import Transform
from dimos.msgs.geometry_msgs.Twist import Twist
from dimos.msgs.geometry_msgs.Vector3 import Vector3
from dimos.navigation.base import NavigationState
from dimos.robot.unitree.unitree_skill_container import (
    _UNITREE_COMMANDS,
    UnitreeSkillContainer,
    _RotationStopResult,
)


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


@pytest.fixture
def unitree_skill_container():
    container = UnitreeSkillContainer()
    try:
        yield container
    finally:
        container._close_module()


def _yaw_transform(yaw_deg: float, ts: float) -> Transform:
    return Transform(
        rotation=Quaternion.from_euler(Vector3(0.0, 0.0, math.radians(yaw_deg))),
        frame_id="world",
        child_frame_id="base_link",
        ts=ts,
    )


def _patch_rotation_clock(mocker) -> None:
    clock = {"now": 0.0}
    mocker.patch(
        "dimos.robot.unitree.unitree_skill_container.time.monotonic",
        side_effect=lambda: clock["now"],
    )
    mocker.patch(
        "dimos.robot.unitree.unitree_skill_container.time.sleep",
        side_effect=lambda seconds: clock.__setitem__("now", clock["now"] + seconds),
    )


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


def test_rotation_stop_counts_tail_and_waits_for_stable_new_tf(
    unitree_skill_container,
    monkeypatch: pytest.MonkeyPatch,
    mocker,
) -> None:
    monkeypatch.setenv("DIMOS_ROTATE_SETTLE_TIMEOUT_S", "1.0")
    _patch_rotation_clock(mocker)
    transforms = iter(
        [
            _yaw_transform(45.0, 1.1),
            _yaw_transform(52.0, 1.2),
            _yaw_transform(58.0, 1.3),
            _yaw_transform(58.0, 1.4),
            _yaw_transform(58.0, 1.5),
            _yaw_transform(58.0, 1.6),
            _yaw_transform(58.0, 1.7),
        ]
    )
    last_transform = _yaw_transform(58.0, 1.7)
    tf = mocker.Mock()
    tf.get.side_effect = lambda *_args: next(transforms, last_transform)
    connection = mocker.Mock()
    connection.move.return_value = True
    mocker.patch.object(unitree_skill_container, "_tf", tf)
    mocker.patch.object(unitree_skill_container, "_connection", connection, create=True)
    stop_twist = Twist(
        linear=Vector3(0.0, 0.0, 0.0),
        angular=Vector3(0.0, 0.0, 0.0),
    )

    result = unitree_skill_container._stop_rotation_and_wait_for_settle(
        stop_twist=stop_twist,
        accumulated=math.radians(38.0),
        last_yaw=math.radians(38.0),
        last_tf_ts=1.0,
        phase="test",
    )

    assert result.settled is True
    assert math.degrees(result.accumulated) == pytest.approx(58.0)
    assert result.new_tf_samples == 7
    assert result.reason == "settled_by_tf"
    assert connection.move.call_count >= 2
    assert all(call.args[0].angular.z == 0.0 for call in connection.move.call_args_list)


def test_rotation_stop_rejects_repeated_tf_as_no_new_feedback(
    unitree_skill_container,
    monkeypatch: pytest.MonkeyPatch,
    mocker,
) -> None:
    monkeypatch.setenv("DIMOS_ROTATE_SETTLE_TIMEOUT_S", "0.25")
    _patch_rotation_clock(mocker)
    tf = mocker.Mock()
    tf.get.return_value = _yaw_transform(10.0, 1.0)
    connection = mocker.Mock()
    connection.move.return_value = True
    mocker.patch.object(unitree_skill_container, "_tf", tf)
    mocker.patch.object(unitree_skill_container, "_connection", connection, create=True)

    result = unitree_skill_container._stop_rotation_and_wait_for_settle(
        stop_twist=Twist(
            linear=Vector3(0.0, 0.0, 0.0),
            angular=Vector3(0.0, 0.0, 0.0),
        ),
        accumulated=math.radians(10.0),
        last_yaw=math.radians(10.0),
        last_tf_ts=1.0,
        phase="test",
    )

    assert result.settled is False
    assert result.new_tf_samples == 0
    assert result.reason == "no_new_tf"
    assert connection.move.call_count >= 2


def test_rotation_stop_rejects_continuously_moving_tf(
    unitree_skill_container,
    monkeypatch: pytest.MonkeyPatch,
    mocker,
) -> None:
    monkeypatch.setenv("DIMOS_ROTATE_SETTLE_TIMEOUT_S", "0.6")
    _patch_rotation_clock(mocker)
    moving = [_yaw_transform(float(yaw), 1.0 + yaw / 10.0) for yaw in range(1, 11)]
    transforms = iter(moving)
    tf = mocker.Mock()
    tf.get.side_effect = lambda *_args: next(transforms, moving[-1])
    connection = mocker.Mock()
    connection.move.return_value = True
    mocker.patch.object(unitree_skill_container, "_tf", tf)
    mocker.patch.object(unitree_skill_container, "_connection", connection, create=True)

    result = unitree_skill_container._stop_rotation_and_wait_for_settle(
        stop_twist=Twist(
            linear=Vector3(0.0, 0.0, 0.0),
            angular=Vector3(0.0, 0.0, 0.0),
        ),
        accumulated=0.0,
        last_yaw=0.0,
        last_tf_ts=1.0,
        phase="test",
    )

    assert result.settled is False
    assert result.new_tf_samples == 10
    assert result.reason == "yaw_still_moving"


def test_rotate_uses_settled_tail_angle_for_final_result(
    unitree_skill_container,
    monkeypatch: pytest.MonkeyPatch,
    mocker,
) -> None:
    monkeypatch.setenv("DIMOS_ROTATE_SETTLE_ENABLED", "true")
    tf = mocker.Mock()
    tf.get.side_effect = [
        _yaw_transform(0.0, 1.0),
        _yaw_transform(40.0, 2.0),
    ]
    navigation = mocker.Mock()
    connection = mocker.Mock()
    mocker.patch.object(unitree_skill_container, "_tf", tf)
    mocker.patch.object(unitree_skill_container, "_navigation", navigation, create=True)
    mocker.patch.object(unitree_skill_container, "_connection", connection, create=True)
    stop_result = _RotationStopResult(
        settled=True,
        accumulated=math.radians(58.0),
        last_yaw=math.radians(58.0),
        last_tf_ts=2.5,
        new_tf_samples=6,
        zero_send_count=5,
        settle_duration_s=0.5,
        yaw_span_deg=0.2,
        max_yaw_rate_deg_s=1.0,
        reason="settled_by_tf",
    )
    settle = mocker.patch.object(
        unitree_skill_container,
        "_stop_rotation_and_wait_for_settle",
        return_value=stop_result,
    )

    result = unitree_skill_container.rotate_in_place_degrees(40.0)

    assert result is False
    settle.assert_called_once()
    assert math.degrees(settle.call_args.kwargs["accumulated"]) == pytest.approx(40.0)


def test_rotate_requires_zero_only_preflight_after_unconfirmed_stop(
    unitree_skill_container,
    monkeypatch: pytest.MonkeyPatch,
    mocker,
) -> None:
    monkeypatch.setenv("DIMOS_ROTATE_SETTLE_ENABLED", "true")
    unitree_skill_container._rotation_stop_unconfirmed = True
    tf = mocker.Mock()
    tf.get.return_value = _yaw_transform(10.0, 1.0)
    navigation = mocker.Mock()
    connection = mocker.Mock()
    mocker.patch.object(unitree_skill_container, "_tf", tf)
    mocker.patch.object(unitree_skill_container, "_navigation", navigation, create=True)
    mocker.patch.object(unitree_skill_container, "_connection", connection, create=True)
    preflight_result = _RotationStopResult(
        settled=False,
        accumulated=0.0,
        last_yaw=math.radians(10.0),
        last_tf_ts=1.0,
        new_tf_samples=0,
        zero_send_count=4,
        settle_duration_s=4.0,
        yaw_span_deg=0.0,
        max_yaw_rate_deg_s=math.inf,
        reason="no_new_tf",
    )
    settle = mocker.patch.object(
        unitree_skill_container,
        "_stop_rotation_and_wait_for_settle",
        return_value=preflight_result,
    )

    result = unitree_skill_container.rotate_in_place_degrees(40.0)

    assert result is False
    assert settle.call_args.kwargs["phase"] == "preflight"
    connection.move.assert_not_called()


def test_rotate_missing_tf_sends_zero_and_requires_preflight(
    unitree_skill_container,
    monkeypatch: pytest.MonkeyPatch,
    mocker,
) -> None:
    monkeypatch.setenv("DIMOS_ROTATE_SETTLE_ENABLED", "true")
    tf = mocker.Mock()
    tf.get.return_value = None
    navigation = mocker.Mock()
    connection = mocker.Mock()
    connection.move.return_value = True
    mocker.patch.object(unitree_skill_container, "_tf", tf)
    mocker.patch.object(unitree_skill_container, "_navigation", navigation, create=True)
    mocker.patch.object(unitree_skill_container, "_connection", connection, create=True)
    mocker.patch(
        "dimos.robot.unitree.unitree_skill_container.time.sleep",
        return_value=None,
    )

    result = unitree_skill_container.rotate_in_place_degrees(40.0)

    assert result is False
    assert unitree_skill_container._rotation_stop_unconfirmed is True
    assert connection.move.call_count == 3
    assert all(call.args[0].angular.z == 0.0 for call in connection.move.call_args_list)
