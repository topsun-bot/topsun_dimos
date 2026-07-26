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

from datetime import datetime, timezone

from dimos.navigation.diagnostics.session import NavigationSessionTracker


def test_session_tracks_replans_and_terminal() -> None:
    tracker = NavigationSessionTracker(
        now=lambda: datetime(2026, 7, 23, 15, 47, 3, 583000, tzinfo=timezone.utc)
    )

    (started,) = tracker.begin("rpc_set_goal")
    first_plan = tracker.next_plan("initial_goal")
    second_plan = tracker.next_plan("obstacle_found")
    ended = tracker.end("arrived", reason="goal_tolerance_reached")

    assert started.context.navigation_session_id == "nav-0001-20260723T154703.583"
    assert started.context.session_event_seq == 1
    assert first_plan is not None
    assert first_plan.plan_version == 1
    assert second_plan is not None
    assert second_plan.plan_version == 2
    assert second_plan.navigation_session_id == started.context.navigation_session_id
    assert ended is not None
    assert ended.terminal == "arrived"
    assert ended.context.plan_version == 2
    assert not tracker.active


def test_new_goal_supersedes_active_session_and_remains_unique() -> None:
    tracker = NavigationSessionTracker(
        now=lambda: datetime(2026, 7, 23, 15, 47, 3, 583000, tzinfo=timezone.utc)
    )
    (first_started,) = tracker.begin("goal_request")

    superseded, second_started = tracker.begin("clicked_point")

    assert superseded.terminal == "superseded"
    assert superseded.context.navigation_session_id == first_started.context.navigation_session_id
    assert second_started.context.navigation_session_id.startswith("nav-0002-")
    assert second_started.entry_source == "clicked_point"


def test_internal_retry_does_not_close_session() -> None:
    tracker = NavigationSessionTracker(now=lambda: datetime(2026, 7, 23, tzinfo=timezone.utc))
    (started,) = tracker.begin("target")

    plan = tracker.next_plan("veered_off_path")

    assert tracker.active
    assert plan is not None
    assert plan.navigation_session_id == started.context.navigation_session_id
    assert tracker.current is not None
    assert tracker.current.plan_version == 1


def test_open_session_is_left_for_offline_process_exit_recovery() -> None:
    tracker = NavigationSessionTracker(now=lambda: datetime(2026, 7, 23, tzinfo=timezone.utc))
    tracker.begin("goal_request")

    assert tracker.current is not None
    assert tracker.active
