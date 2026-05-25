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

import math
from typing import Any, cast

import pytest

from dimos.core.global_config import GlobalConfig
from dimos.msgs.geometry_msgs.PoseStamped import PoseStamped
from dimos.msgs.geometry_msgs.Quaternion import Quaternion
from dimos.msgs.geometry_msgs.Vector3 import Vector3
from dimos.navigation.replanning_a_star.local_planner import LocalPlanner, StopMessage
from dimos.navigation.replanning_a_star.navigation_map import NavigationMap


def _make_local_planner(config: GlobalConfig) -> LocalPlanner:
    return LocalPlanner(config, cast("NavigationMap", cast("Any", object())), goal_tolerance=0.2)


def test_obstacle_mvp_config_defaults_to_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OBSTACLE_CROSSING_MVP", raising=False)
    monkeypatch.delenv("OBSTACLE_CROSSING_MVP_HEIGHT_CM", raising=False)

    config = GlobalConfig(_env_file=None)

    assert config.obstacle_crossing_mvp is False
    assert config.obstacle_crossing_mvp_height_cm is None


def test_obstacle_ahead_with_mvp_disabled_keeps_existing_replan_behavior() -> None:
    planner = _make_local_planner(GlobalConfig(obstacle_crossing_mvp=False))
    messages: list[StopMessage] = []
    planner.stopped_navigating.subscribe(messages.append)

    should_stop = planner._handle_obstacle_ahead(None)

    assert should_stop is True
    assert messages == ["obstacle_found"]


def test_obstacle_ahead_cross_continues_local_planning() -> None:
    planner = _make_local_planner(
        GlobalConfig(obstacle_crossing_mvp=True, obstacle_crossing_mvp_height_cm=10.0)
    )
    messages: list[StopMessage] = []
    planner.stopped_navigating.subscribe(messages.append)

    should_stop = planner._handle_obstacle_ahead(None)

    assert should_stop is False
    assert messages == []


def test_obstacle_ahead_medium_pose_detours_despite_low_cli_height() -> None:
    planner = _make_local_planner(
        GlobalConfig(obstacle_crossing_mvp=True, obstacle_crossing_mvp_height_cm=8.0)
    )
    messages: list[StopMessage] = []
    planner.stopped_navigating.subscribe(messages.append)
    pose = PoseStamped(
        position=Vector3(x=-1.92, y=3.04, z=0.0),
        orientation=Quaternion.from_euler(Vector3(0.0, 0.0, math.pi / 2)),
    )

    should_stop = planner._handle_obstacle_ahead(pose)

    assert should_stop is True
    assert messages == ["obstacle_found"]


def test_mvp_detour_preempt_at_06m_from_medium_sill() -> None:
    planner = _make_local_planner(
        GlobalConfig(obstacle_crossing_mvp=True, obstacle_crossing_mvp_height_cm=8.0)
    )
    messages: list[StopMessage] = []
    planner.stopped_navigating.subscribe(messages.append)
    planner._state = "path_following"
    pose = PoseStamped(
        position=Vector3(x=-1.12, y=2.65, z=0.0),
        orientation=Quaternion.from_euler(Vector3(0.0, 0.0, math.pi / 2)),
    )

    should_hold = planner._try_mvp_detour_preempt(pose)

    assert should_hold is True
    assert messages == ["obstacle_found"]
    assert planner._mvp_detour_hold_active is True


def test_mvp_detour_preempt_skips_when_farther_than_06m() -> None:
    planner = _make_local_planner(
        GlobalConfig(obstacle_crossing_mvp=True, obstacle_crossing_mvp_height_cm=8.0)
    )
    planner._state = "path_following"
    pose = PoseStamped(
        position=Vector3(x=-1.12, y=2.4, z=0.0),
        orientation=Quaternion.from_euler(Vector3(0.0, 0.0, math.pi / 2)),
    )

    assert planner._try_mvp_detour_preempt(pose) is False


@pytest.mark.parametrize("height_cm", [None, 10.01, 50.0])
def test_obstacle_ahead_detour_requests_replan(height_cm: float | None) -> None:
    planner = _make_local_planner(
        GlobalConfig(obstacle_crossing_mvp=True, obstacle_crossing_mvp_height_cm=height_cm)
    )
    messages: list[StopMessage] = []
    planner.stopped_navigating.subscribe(messages.append)

    should_stop = planner._handle_obstacle_ahead(None)

    assert should_stop is True
    assert messages == ["obstacle_found"]


def test_obstacle_ahead_stop_aborts_navigation_without_replan() -> None:
    planner = _make_local_planner(
        GlobalConfig(obstacle_crossing_mvp=True, obstacle_crossing_mvp_height_cm=50.01)
    )
    messages: list[StopMessage] = []
    planner.stopped_navigating.subscribe(messages.append)

    should_stop = planner._handle_obstacle_ahead(None)

    assert should_stop is True
    assert messages == ["obstacle_abort"]
