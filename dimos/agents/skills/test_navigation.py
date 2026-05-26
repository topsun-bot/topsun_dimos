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

import time
from types import SimpleNamespace
from typing import Any

from langchain_core.messages import HumanMessage
import numpy as np
import pytest

from dimos.agents.skills.navigation import NavigationSkillContainer
from dimos.core.core import rpc
from dimos.core.module import Module
from dimos.core.stream import Out
from dimos.msgs.geometry_msgs.PoseStamped import PoseStamped
from dimos.msgs.geometry_msgs.Quaternion import Quaternion
from dimos.msgs.geometry_msgs.Vector3 import Vector3, make_vector3
from dimos.msgs.nav_msgs.OccupancyGrid import CostValues, OccupancyGrid
from dimos.msgs.sensor_msgs.Image import Image
from dimos.navigation.base import NavigationState
from dimos.types.robot_location import RobotLocation
from dimos.types.spatial_record import RecordType, SpatialRecord


class FakeCamera(Module):
    color_image: Out[Image]


class FakeOdom(Module):
    odom: Out[PoseStamped]


class FakeCostmap(Module):
    global_costmap: Out[OccupancyGrid]


class StubSpatialMemory(Module):
    @rpc
    def tag_location(self, robot_location: RobotLocation) -> bool:
        return True

    @rpc
    def tag_location_with_image(self, robot_location: RobotLocation, image: Any) -> bool:
        return True

    @rpc
    def query_tagged_location(self, query: str) -> RobotLocation | None:
        return None

    @rpc
    def query_location_by_image(self, image: Any) -> RobotLocation | None:
        return None

    @rpc
    def query_by_text(self, text: str, limit: int = 5) -> list[dict[str, Any]]:
        return []

    @rpc
    def query_by_text_with_images(self, text: str, limit: int = 3) -> list[dict[str, Any]]:
        return []

    @rpc
    def get_memory_locations(self) -> list[dict[str, float | str]]:
        return []

    @rpc
    def get_room_image(self, location_id: str) -> Any:
        return None

    @rpc
    def get_room_images(self) -> list[dict[str, object]]:
        return []

    @rpc
    def get_image_by_id(self, frame_id: str) -> Any:
        return None

    @rpc
    def clear_all(self) -> dict[str, int]:
        return {}


class StubLandmarkMemory(Module):
    @rpc
    def record(self, record: Any) -> str:
        return ""

    @rpc
    def find_by_name(self, name: str) -> Any:
        return None

    @rpc
    def search_by_name(self, name: str) -> list[Any]:
        return []

    @rpc
    def query_by_text(self, text: str, limit: int = 5) -> list[Any]:
        return []

    @rpc
    def resolve_by_query(self, query: str) -> Any:
        return None

    @rpc
    def query_by_type(self, record_type: Any) -> list[Any]:
        return []

    @rpc
    def query_by_state(self, state: str) -> list[Any]:
        return []

    @rpc
    def get_all(self) -> list[Any]:
        return []

    @rpc
    def find_nearest(self, x: float, y: float, radius: float) -> Any:
        return None

    @rpc
    def update_state(self, record_id: str, new_state: str) -> bool:
        return True

    @rpc
    def get_by_id(self, record_id: str) -> Any:
        return None

    @rpc
    def save_snapshot(self, record_id: str, image_bytes: bytes) -> str | None:
        return None

    @rpc
    def save(self) -> bool:
        return True

    @rpc
    def load(self) -> bool:
        return True

    @rpc
    def clear_all(self) -> int:
        return 0


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


class StubObjectTracking(Module):
    @rpc
    def track(self, bbox: list[float]) -> dict[str, Any]:
        return {}

    @rpc
    def stop_track(self) -> bool:
        return True

    @rpc
    def is_tracking(self) -> bool:
        return False


_STUB_BLUEPRINTS = [
    StubSpatialMemory.blueprint(),
    StubLandmarkMemory.blueprint(),
    StubNavigation.blueprint(),
    StubObjectTracking.blueprint(),
]


class MockedStopNavSkill(NavigationSkillContainer):
    _skill_started = True

    def _cancel_goal_and_stop(self):
        pass


class MockedExploreNavSkill(NavigationSkillContainer):
    _skill_started = True

    def _start_exploration(self, timeout):
        return "Exploration completed successfuly"

    def _cancel_goal_and_stop(self):
        pass


class MockedSemanticNavSkill(NavigationSkillContainer):
    _skill_started = True

    def _navigate_by_tagged_location(self, query):
        return None

    def _navigate_to_object(self, query, **kwargs):
        return None

    def _navigate_using_semantic_map(self, query):
        return f"Successfuly arrived at '{query}'"


def _nav_container() -> NavigationSkillContainer:
    nav = object.__new__(NavigationSkillContainer)
    nav._skill_started = True
    return nav


class _FakeNavigation:
    def __init__(self) -> None:
        self.goals: list[PoseStamped] = []
        self.cancel_count = 0

    def set_goal(self, goal: PoseStamped) -> bool:
        self.goals.append(goal)
        return True

    def cancel_goal(self) -> bool:
        self.cancel_count += 1
        return True

    def is_goal_reached(self) -> bool:
        return True

    def get_state(self) -> NavigationState:
        return NavigationState.IDLE


class _FakeUnitree:
    def __init__(self) -> None:
        self.rotations: list[float] = []
        self.commands: list[str] = []

    def relative_move(self, forward: float, left: float, degrees: float) -> bool:
        self.rotations.append(degrees)
        return True

    def execute_sport_command(self, command: str) -> str:
        self.commands.append(command)
        return "ok"


class _FakeSpeak:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.calls: list[tuple[str, bool]] = []

    def speak(self, text: str, blocking: bool = True) -> str:
        self.calls.append((text, blocking))
        if self.fail:
            raise RuntimeError("tts failed")
        return "ok"


class _FakeTracker:
    def __init__(self) -> None:
        self.stopped = False

    def stop_track(self) -> None:
        self.stopped = True


class _FakeExplorer:
    def __init__(self) -> None:
        self.stopped = False

    def end_exploration(self) -> str:
        self.stopped = True
        return "Stopped exploration."


def _pose(x: float, y: float, yaw: float = 0.0) -> PoseStamped:
    return PoseStamped(
        position=make_vector3(x, y, 0.0),
        orientation=Quaternion.from_euler(Vector3(0.0, 0.0, yaw)),
        frame_id="map",
    )


def _free_costmap(size: int = 21, resolution: float = 0.5) -> OccupancyGrid:
    return OccupancyGrid(
        grid=np.zeros((size, size), dtype=np.int8),
        resolution=resolution,
        frame_id="map",
    )


def test_navigate_with_text_stops_after_known_object_landmark() -> None:
    nav = _nav_container()
    target = SpatialRecord(
        name="fire extinguisher",
        record_type=RecordType.LANDMARK,
        position=(2.0, 0.0, 0.0),
    )
    object_attempts: list[float] = []

    nav._navigate_by_tagged_location = lambda query: None
    nav._resolve_landmark_from_query = lambda query: target
    nav._navigate_to_landmark = lambda *args, **kwargs: "Arrived near landmark."
    nav._room_anchor_sweep_for_object = lambda query: None
    nav._query_memory_images_with_vlm = lambda query: None
    nav._navigate_using_semantic_map = lambda query: None

    def fake_object_nav(query: str, *, timeout: float = 30.0) -> str | None:
        object_attempts.append(timeout)
        return None

    nav._navigate_to_object = fake_object_nav

    assert nav.navigate_with_text("fire extinguisher") == "Arrived near landmark."
    assert object_attempts == []


def test_explore_memory_blindspot_sends_goal_from_costmap() -> None:
    nav = _nav_container()
    nav._latest_odom = _pose(5.0, 5.0)
    nav._latest_global_costmap = _free_costmap()
    nav._navigation = _FakeNavigation()
    nav._spatial_memory = SimpleNamespace(get_memory_locations=lambda: [])

    result = nav.explore_memory_blindspot(search_radius_m=2.0, coverage_radius_m=0.75)

    assert "Started navigating" in result
    assert len(nav._navigation.goals) == 1
    goal = nav._navigation.goals[0]
    assert goal.frame_id == "map"
    assert ((goal.position.x - 5.0) ** 2 + (goal.position.y - 5.0) ** 2) ** 0.5 >= 0.5


def test_explore_memory_blindspot_skips_currently_covered_area() -> None:
    nav = _nav_container()
    nav._latest_odom = _pose(5.0, 5.0)
    nav._latest_global_costmap = _free_costmap()
    nav._navigation = _FakeNavigation()
    nav._spatial_memory = SimpleNamespace(
        get_memory_locations=lambda: [
            {"frame_id": "near", "pos_x": 5.5, "pos_y": 5.0, "pos_z": 0.0, "timestamp": time.time()}
        ]
    )

    result = nav.explore_memory_blindspot(search_radius_m=1.0, coverage_radius_m=2.0)

    assert "coverage looks healthy" in result
    assert nav._navigation.goals == []


def test_patrol_memory_blindspots_stops_after_max_goals(monkeypatch: pytest.MonkeyPatch) -> None:
    nav = _nav_container()
    nav._latest_odom = _pose(5.0, 5.0)
    nav._latest_global_costmap = _free_costmap()
    nav._navigation = _FakeNavigation()
    nav._spatial_memory = SimpleNamespace(get_memory_locations=lambda: [])
    nav._memory_blindspot_patrol_stop = False
    monkeypatch.setattr("dimos.agents.skills.navigation.time.sleep", lambda seconds: None)

    result = nav.patrol_memory_blindspots(
        search_radius_m=2.0,
        coverage_radius_m=0.75,
        max_goals=1,
        max_duration_sec=5.0,
        recognize_on_arrival=False,
    )

    assert "visited 1 goal(s)" in result
    assert "max_goals=1 reached" in result
    assert len(nav._navigation.goals) == 1


def test_memory_blindspot_wait_reports_stuck_without_progress(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    nav = _nav_container()
    nav._latest_odom = _pose(5.0, 5.0)
    nav._navigation = _FakeNavigation()
    nav._navigation.is_goal_reached = lambda: False  # type: ignore[method-assign]
    nav._memory_blindspot_patrol_stop = False
    now = 100.0

    def fake_time() -> float:
        return now

    def fake_sleep(seconds: float) -> None:
        nonlocal now
        now += seconds

    monkeypatch.setattr("dimos.agents.skills.navigation.time.time", fake_time)
    monkeypatch.setattr("dimos.agents.skills.navigation.time.sleep", fake_sleep)

    status = nav._wait_for_memory_blindspot_goal(
        timeout_sec=30.0,
        stuck_timeout_sec=1.0,
        progress_epsilon_m=0.25,
    )

    assert status == "stuck"
    assert nav._navigation.cancel_count == 1


def test_patrol_memory_blindspots_blacklists_rejected_goal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    nav = _nav_container()
    first = {
        "pose": _pose(1.0, 0.0),
        "target_type": "memory_gap",
        "reason": "missing",
        "distance_m": 1.0,
    }
    second = {
        "pose": _pose(2.0, 0.0),
        "target_type": "memory_gap",
        "reason": "missing",
        "distance_m": 2.0,
    }
    nav._latest_odom = _pose(0.0, 0.0)
    nav._latest_global_costmap = _free_costmap()
    nav._spatial_memory = SimpleNamespace(get_memory_locations=lambda: [])
    nav._memory_blindspot_patrol_stop = False
    monkeypatch.setattr("dimos.agents.skills.navigation.time.sleep", lambda seconds: None)

    class RejectFirstNavigation(_FakeNavigation):
        def set_goal(self, goal: PoseStamped) -> bool:
            self.goals.append(goal)
            return len(self.goals) > 1

    nav._navigation = RejectFirstNavigation()

    def fake_find(**kwargs: Any) -> dict[str, Any]:
        excluded = kwargs.get("exclude_recent_goals") or []
        if any(abs(x - 1.0) < 0.01 and abs(y) < 0.01 for x, y in excluded):
            return second
        return first

    nav._find_nearest_memory_blindspot = fake_find  # type: ignore[method-assign]

    result = nav.patrol_memory_blindspots(
        max_goals=1,
        max_duration_sec=5.0,
        recognize_on_arrival=False,
    )

    assert "visited 1 goal(s)" in result
    assert [goal.position.x for goal in nav._navigation.goals] == [1.0, 2.0]


def test_patrol_memory_blindspots_blacklists_stuck_goal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    nav = _nav_container()
    first = {
        "pose": _pose(1.0, 0.0),
        "target_type": "memory_gap",
        "reason": "missing",
        "distance_m": 1.0,
    }
    second = {
        "pose": _pose(2.0, 0.0),
        "target_type": "memory_gap",
        "reason": "missing",
        "distance_m": 2.0,
    }
    nav._latest_odom = _pose(0.0, 0.0)
    nav._latest_global_costmap = _free_costmap()
    nav._navigation = _FakeNavigation()
    nav._spatial_memory = SimpleNamespace(get_memory_locations=lambda: [])
    nav._memory_blindspot_patrol_stop = False
    monkeypatch.setattr("dimos.agents.skills.navigation.time.sleep", lambda seconds: None)
    wait_statuses = ["stuck", "reached"]

    def fake_wait(*args: Any, **kwargs: Any) -> str:
        return wait_statuses.pop(0)

    def fake_find(**kwargs: Any) -> dict[str, Any]:
        excluded = kwargs.get("exclude_recent_goals") or []
        if any(abs(x - 1.0) < 0.01 and abs(y) < 0.01 for x, y in excluded):
            return second
        return first

    nav._wait_for_memory_blindspot_goal = fake_wait  # type: ignore[method-assign]
    nav._find_nearest_memory_blindspot = fake_find  # type: ignore[method-assign]

    result = nav.patrol_memory_blindspots(
        max_goals=1,
        max_duration_sec=5.0,
        recognize_on_arrival=False,
    )

    assert "visited 1 goal(s)" in result
    assert "stuck 1" in result
    assert [goal.position.x for goal in nav._navigation.goals] == [1.0, 2.0]


def test_blindspot_goal_rejects_occupied_cells() -> None:
    nav = _nav_container()
    grid = np.zeros((21, 21), dtype=np.int8)
    grid[9:12, 9:12] = CostValues.OCCUPIED
    nav._latest_odom = _pose(5.0, 5.0)
    nav._latest_global_costmap = OccupancyGrid(grid=grid, resolution=0.5, frame_id="map")
    nav._spatial_memory = SimpleNamespace(get_memory_locations=lambda: [])

    target = nav._find_nearest_memory_blindspot(search_radius_m=2.0, coverage_radius_m=0.5)

    assert target is not None
    gx, gy = target["grid"]
    assert int(grid[gy, gx]) != CostValues.OCCUPIED


def test_room_anchor_sweep_scans_rooms_until_object_found() -> None:
    nav = _nav_container()
    rooms = [
        SpatialRecord(name="office", record_type=RecordType.ROOM, position=(0.0, 0.0, 0.0)),
        SpatialRecord(name="lab", record_type=RecordType.ROOM, position=(4.0, 0.0, 0.0)),
    ]
    nav._landmark_memory = SimpleNamespace(
        query_by_type=lambda record_type: rooms,
        resolve_by_query=lambda q: None,
    )
    nav._unitree_skill_container = _FakeUnitree()
    visited: list[str] = []

    def fake_landmark_nav(target: SpatialRecord, **kwargs: Any) -> str:
        visited.append(target.name)
        return f"Arrived near {target.name}."

    def fake_object_nav(query: str, *, timeout: float = 30.0) -> str | None:
        if visited[-1] == "lab":
            return f"Successfully arrived at '{query}'"
        return None

    nav._navigate_to_landmark = fake_landmark_nav
    nav._navigate_to_object = fake_object_nav

    assert nav._room_anchor_sweep_for_object("toolbox") == "Successfully arrived at 'toolbox'"
    assert visited == ["office", "lab"]
    assert nav._unitree_skill_container.rotations == [360.0, 360.0]


def test_periodic_visual_drift_correction_soft_shifts_goal() -> None:
    nav = _nav_container()
    nav._drift_soft_m = 0.3
    nav._drift_hard_m = 1.0
    nav._room_visual_max_distance = 0.35
    nav._latest_image = SimpleNamespace(data=object())
    nav._latest_odom = _pose(0.0, 0.0)
    nav._navigation = _FakeNavigation()
    nav._spatial_memory = SimpleNamespace(
        query_location_by_image=lambda image: RobotLocation(
            name="office",
            position=(0.4, 0.0, 0.0),
            rotation=(0.0, 0.0, 0.0),
            metadata={"distance": 0.1},
        )
    )
    active_goal = _pose(2.0, 0.0)

    shifted, severe = nav._periodic_visual_drift_correction(active_goal, [0.0], 0.0)

    assert severe is False
    assert shifted.position.x == pytest.approx(2.4)
    assert shifted.position.y == pytest.approx(0.0)
    assert nav._navigation.goals[-1].position.x == pytest.approx(2.4)


def test_periodic_visual_drift_correction_cancels_on_severe_drift() -> None:
    nav = _nav_container()
    nav._drift_soft_m = 0.3
    nav._drift_hard_m = 1.0
    nav._room_visual_max_distance = 0.35
    nav._latest_image = SimpleNamespace(data=object())
    nav._latest_odom = _pose(0.0, 0.0)
    nav._navigation = _FakeNavigation()
    nav._spatial_memory = SimpleNamespace(
        query_location_by_image=lambda image: RobotLocation(
            name="office",
            position=(1.2, 0.0, 0.0),
            rotation=(0.0, 0.0, 0.0),
            metadata={"distance": 0.1},
        ),
        tag_location_with_image=lambda location, image: True,
    )
    active_goal = _pose(2.0, 0.0)

    shifted, severe = nav._periodic_visual_drift_correction(active_goal, [0.0], 0.0)

    assert shifted is active_goal
    assert severe is True
    assert nav._navigation.cancel_count == 1


def test_arrival_action_invokes_unitree_sport_command() -> None:
    nav = _nav_container()
    nav._navigation = _FakeNavigation()
    nav._unitree_skill_container = _FakeUnitree()

    result = nav._run_arrival_action("sit", "fire extinguisher")

    assert "executing arrival_action='sit'" in result
    assert nav._navigation.cancel_count == 1
    assert nav._unitree_skill_container.commands == ["Sit"]


def test_arrival_action_sit_point_invokes_sit_then_hello(monkeypatch: pytest.MonkeyPatch) -> None:
    nav = _nav_container()
    nav._navigation = _FakeNavigation()
    nav._unitree_skill_container = _FakeUnitree()
    monkeypatch.setattr("dimos.agents.skills.navigation.time.sleep", lambda seconds: None)

    result = nav._run_arrival_action("sit_point", "computer")

    assert "executing arrival_action='sit_point'" in result
    assert nav._navigation.cancel_count == 1
    assert nav._unitree_skill_container.commands == ["Sit", "Hello", "RecoveryStand"]


def test_arrival_action_point_recovers_after_gesture(monkeypatch: pytest.MonkeyPatch) -> None:
    nav = _nav_container()
    nav._navigation = _FakeNavigation()
    nav._unitree_skill_container = _FakeUnitree()
    monkeypatch.setattr("dimos.agents.skills.navigation.time.sleep", lambda seconds: None)

    result = nav._run_arrival_action("point", "computer")

    assert "executing arrival_action='point'" in result
    assert nav._unitree_skill_container.commands == ["Hello", "RecoveryStand"]


def test_object_landmark_accepts_safe_standoff_without_churn() -> None:
    nav = _nav_container()
    target = SpatialRecord(
        name="垃圾桶",
        record_type=RecordType.LANDMARK,
        position=(0.0, 0.0, 0.0),
    )
    nav._navigation = _FakeNavigation()
    nav._latest_odom = _pose(0.8, 0.0)
    nav._landmark_memory = SimpleNamespace(get_all=lambda: [target])
    nav._relocalize_interval_s = 30.0
    nav._coordinate_frame_stale_reason = lambda target: None
    nav._speak_skill = _FakeSpeak()

    result = nav._navigate_to_landmark(
        target,
        arrival_action="stop",
        arrival_distance=0.5,
        run_arrival_action=False,
    )

    assert "Arrived near" in result
    assert nav._navigation.goals == []
    assert nav._speak_skill.calls == [("找到了", False)]


def test_room_landmark_does_not_announce_object_found() -> None:
    nav = _nav_container()
    target = SpatialRecord(
        name="办公室",
        record_type=RecordType.ROOM,
        position=(0.0, 0.0, 0.0),
    )
    nav._navigation = _FakeNavigation()
    nav._latest_odom = _pose(1.5, 0.0)
    nav._landmark_memory = SimpleNamespace(get_all=lambda: [target])
    nav._spatial_memory = SimpleNamespace(query_location_by_image=lambda image: None)
    nav._latest_image = SimpleNamespace(data=object())
    nav._relocalize_interval_s = 30.0
    nav._coordinate_frame_stale_reason = lambda target: None
    nav._speak_skill = _FakeSpeak()

    result = nav._navigate_to_landmark(
        target,
        arrival_action="stop",
        arrival_distance=0.5,
        run_arrival_action=True,
        enable_visual_drift=True,
    )

    assert "arrival_action=stop" in result
    assert nav._speak_skill.calls == []


def test_navigate_to_object_announces_after_visual_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    nav = _nav_container()
    nav._navigation = _FakeNavigation()
    nav._speak_skill = _FakeSpeak()
    nav._object_tracking = SimpleNamespace(
        track=lambda bbox: {"status": "tracking_started"},
        is_tracking=lambda: True,
        stop_track=lambda: None,
    )
    nav._get_bbox_for_current_frame = lambda query: [0.0, 0.0, 100.0, 100.0]
    monkeypatch.setattr("dimos.agents.skills.navigation.time.sleep", lambda seconds: None)

    result = nav._navigate_to_object("水杯", timeout=1.0)

    assert result == "Successfully arrived at '水杯'"
    assert nav._speak_skill.calls == [("找到了", False)]


def test_object_found_announcement_failure_does_not_fail_navigation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    nav = _nav_container()
    nav._navigation = _FakeNavigation()
    nav._speak_skill = _FakeSpeak(fail=True)
    nav._object_tracking = SimpleNamespace(
        track=lambda bbox: {"status": "tracking_started"},
        is_tracking=lambda: True,
        stop_track=lambda: None,
    )
    nav._get_bbox_for_current_frame = lambda query: [0.0, 0.0, 100.0, 100.0]
    monkeypatch.setattr("dimos.agents.skills.navigation.time.sleep", lambda seconds: None)

    result = nav._navigate_to_object("水杯", timeout=1.0)

    assert result == "Successfully arrived at '水杯'"
    assert nav._speak_skill.calls == [("找到了", False)]


def test_stop_all_motion_cancels_tracking_and_recovers() -> None:
    nav = _nav_container()
    nav._navigation = _FakeNavigation()
    nav._unitree_skill_container = _FakeUnitree()
    nav._object_tracking = _FakeTracker()
    nav._frontier_explorer = _FakeExplorer()

    result = nav.stop_all_motion()

    assert "Stopped navigation and tracking" in result
    assert "Stopped exploration" in result
    assert nav._navigation.cancel_count == 1
    assert nav._object_tracking.stopped is True
    assert nav._frontier_explorer.stopped is True
    assert nav._unitree_skill_container.commands == ["RecoveryStand"]


def test_coordinate_frame_stale_detected_from_visual_room_mismatch() -> None:
    nav = _nav_container()
    nav._room_visual_max_distance = 0.35
    nav._latest_image = SimpleNamespace(data=object())
    nav._latest_odom = _pose(10.0, 0.0)
    room = SpatialRecord(name="办公室", record_type=RecordType.ROOM, position=(0.0, 0.0, 0.0))
    nav._landmark_memory = SimpleNamespace(
        resolve_by_query=lambda query: room,
        query_by_type=lambda record_type: [room],
    )
    nav._spatial_memory = SimpleNamespace(
        query_location_by_image=lambda image: RobotLocation(
            name="办公室",
            position=(0.0, 0.0, 0.0),
            rotation=(0.0, 0.0, 0.0),
            metadata={"distance": 0.1},
        )
    )

    reason = nav._coordinate_frame_stale_reason(room)

    assert reason is not None
    assert "old odom frame" in reason


@pytest.mark.slow
def test_stop_movement(agent_setup) -> None:
    history = agent_setup(
        blueprints=[
            FakeCamera.blueprint(),
            FakeOdom.blueprint(),
            FakeCostmap.blueprint(),
            MockedStopNavSkill.blueprint(),
            *_STUB_BLUEPRINTS,
        ],
        messages=[HumanMessage("Stop moving. Use the stop_movement tool.")],
    )

    assert "stopped" in history[-1].content.lower()


def test_start_exploration(agent_setup) -> None:
    history = agent_setup(
        blueprints=[
            FakeCamera.blueprint(),
            FakeOdom.blueprint(),
            FakeCostmap.blueprint(),
            MockedExploreNavSkill.blueprint(),
            *_STUB_BLUEPRINTS,
        ],
        messages=[
            HumanMessage("Take a look around for 10 seconds. Use the start_exploration tool.")
        ],
    )

    assert "explor" in history[-1].content.lower()


def test_go_to_semantic_location(agent_setup) -> None:
    history = agent_setup(
        blueprints=[
            FakeCamera.blueprint(),
            FakeOdom.blueprint(),
            FakeCostmap.blueprint(),
            MockedSemanticNavSkill.blueprint(),
            *_STUB_BLUEPRINTS,
        ],
        messages=[HumanMessage("Go to the bookshelf. Use the navigate_with_text tool.")],
    )

    assert "success" in history[-1].content.lower()
