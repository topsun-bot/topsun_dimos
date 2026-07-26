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

import json
from pathlib import Path
from unittest.mock import patch

from dimos.core.global_config import GlobalConfig
from dimos.msgs.geometry_msgs.PoseStamped import PoseStamped
from dimos.msgs.geometry_msgs.Quaternion import Quaternion
from dimos.msgs.geometry_msgs.Vector3 import Vector3
from dimos.msgs.nav_msgs.Path import Path as NavigationPath
from dimos.navigation.replanning_a_star.global_planner import GlobalPlanner


def _pose(x: float, y: float) -> PoseStamped:
    return PoseStamped(
        ts=123.0 + x,
        frame_id="world",
        position=Vector3(x, y, 0.0),
        orientation=Quaternion(0.0, 0.0, 0.0, 1.0),
    )


def test_planner_trace_reconstructs_session_and_plan_artifacts(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("DIMOS_RUN_LOG_DIR", str(tmp_path))
    config = GlobalConfig(
        navigation_trace_level="summary",
        navigation_trace_min_free_disk_bytes=0,
    )
    with (
        patch("dimos.navigation.replanning_a_star.global_planner.NavigationMap"),
        patch("dimos.navigation.replanning_a_star.global_planner.LocalPlanner"),
    ):
        planner = GlobalPlanner(config)

    current = _pose(0.0, 0.0)
    goal = _pose(2.0, 0.0)
    planner.handle_odom(current)
    with patch.object(planner, "_plan_path"):
        planner.handle_goal_request(goal, entry_source="rpc_set_goal")
    context = planner._trace_next_plan("initial_goal", current, goal)
    assert context is not None
    planner._active_plan = context
    raw = NavigationPath(ts=130.0, frame_id="world", poses=[current, goal])
    smoothed = NavigationPath(
        ts=131.0,
        frame_id="world",
        poses=[current, _pose(1.0, 0.0), goal],
    )
    planner._trace_plan_completed(raw, smoothed, goal.position)
    planner.cancel_goal(arrived=True)
    planner._trace.close()

    trace_path = next((tmp_path / "navigation").glob("planner-*.jsonl"))
    events = [json.loads(line) for line in trace_path.read_text(encoding="utf-8").splitlines()]
    started = next(event for event in events if event["event"] == "navigation_session_started")
    ended = next(event for event in events if event["event"] == "navigation_session_ended")
    artifacts = [event for event in events if event["event"] == "json_artifact_saved"]

    assert started["entry_source"] == "rpc_set_goal"
    assert ended["terminal"] == "arrived"
    assert ended["navigation_session_id"] == started["navigation_session_id"]
    assert {artifact["artifact_kind"] for artifact in artifacts} == {
        "raw_path",
        "smoothed_path",
    }
    for artifact in artifacts:
        artifact_path = tmp_path / "navigation" / artifact["artifact_path"]
        payload = json.loads(artifact_path.read_text(encoding="utf-8"))
        assert payload["navigation_session_id"] == started["navigation_session_id"]
        assert payload["plan_version"] == 1
