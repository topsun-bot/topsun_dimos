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

from dimos.agents.skills.navigation import MemoryBlindspotVisitState, NavigationSkillContainer
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


def test_patrol_memory_blindspots_caps_goal_wait_to_remaining_duration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    nav = _nav_container()
    target = {
        "pose": _pose(1.0, 0.0),
        "target_type": "memory_gap",
        "reason": "missing",
        "distance_m": 1.0,
    }
    nav._latest_odom = _pose(0.0, 0.0)
    nav._latest_global_costmap = _free_costmap()
    nav._navigation = _FakeNavigation()
    nav._spatial_memory = SimpleNamespace(get_memory_locations=lambda: [])
    nav._memory_blindspot_patrol_stop = False
    captured_timeouts: list[float] = []

    monkeypatch.setattr("dimos.agents.skills.navigation.time.time", lambda: 100.0)
    monkeypatch.setattr("dimos.agents.skills.navigation.time.sleep", lambda seconds: None)
    nav._find_nearest_memory_blindspot = lambda **kwargs: target  # type: ignore[method-assign]

    def fake_wait(timeout_sec: float, *args: Any, **kwargs: Any) -> str:
        captured_timeouts.append(timeout_sec)
        return "stopped"

    nav._wait_for_memory_blindspot_goal = fake_wait  # type: ignore[method-assign]

    nav.patrol_memory_blindspots(
        max_duration_sec=5.0,
        goal_timeout_sec=90.0,
        recognize_on_arrival=False,
    )

    assert captured_timeouts == [5.0]


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
    wait_statuses = ["stuck", "reached", "reached"]

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
    assert "timed out 0" in result
    assert "stuck 1" in result
    assert "recovered 1" in result
    assert [nav._navigation.goals[0].position.x, nav._navigation.goals[-1].position.x] == [
        1.0,
        2.0,
    ]


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


def test_blindspot_region_prefers_deep_corridor_goal() -> None:
    nav = _nav_container()
    grid = np.full((7, 21), CostValues.OCCUPIED, dtype=np.int8)
    grid[2:5, 1:20] = CostValues.FREE
    nav._latest_odom = _pose(1.0, 1.5)
    nav._latest_global_costmap = OccupancyGrid(grid=grid, resolution=0.5, frame_id="map")
    nav._spatial_memory = SimpleNamespace(get_memory_locations=lambda: [])

    target = nav._find_nearest_memory_blindspot(
        search_radius_m=6.0,
        coverage_radius_m=0.5,
        clearance_m=0.0,
    )

    assert target is not None
    assert target["region_cell_count"] > 1
    assert target["target_selection"] == "region_deep_point"
    assert target["pose"].position.x >= 6.0


def test_wait_for_new_memory_frame_uses_frame_count(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    nav = _nav_container()
    calls = 0
    now = 100.0

    def fake_time() -> float:
        return now

    def fake_sleep(seconds: float) -> None:
        nonlocal now
        now += seconds

    def fake_locations() -> list[dict[str, float | str]]:
        nonlocal calls
        calls += 1
        if calls == 1:
            return [{"frame_id": "old", "pos_x": 0.0, "pos_y": 0.0, "timestamp": 90.0}]
        return [
            {"frame_id": "old", "pos_x": 0.0, "pos_y": 0.0, "timestamp": 90.0},
            {"frame_id": "new", "pos_x": 1.0, "pos_y": 0.0, "timestamp": 99.0},
        ]

    nav._spatial_memory = SimpleNamespace(get_memory_locations=fake_locations)
    nav._memory_blindspot_patrol_stop = False
    monkeypatch.setattr("dimos.agents.skills.navigation.time.time", fake_time)
    monkeypatch.setattr("dimos.agents.skills.navigation.time.sleep", fake_sleep)

    assert nav._wait_for_new_memory_frame(100.0, previous_frame_count=1, timeout_sec=1.0)


def test_region_failure_penalty_matches_stable_fingerprint() -> None:
    nav = _nav_container()
    grid = np.zeros((11, 11), dtype=np.int8)
    nav._latest_odom = _pose(0.0, 0.0)
    nav._latest_global_costmap = OccupancyGrid(grid=grid, resolution=0.5, frame_id="map")
    nav._spatial_memory = SimpleNamespace(get_memory_locations=lambda: [])

    first = nav._find_nearest_memory_blindspot(search_radius_m=3.0, coverage_radius_m=0.5)
    assert first is not None
    failed_regions = [
        MemoryBlindspotVisitState(
            region_id=str(first["region_id"]),
            region_fingerprint=str(first["region_fingerprint"]),
            target_pose=(
                float(first["pose"].position.x),
                float(first["pose"].position.y),
                float(first["pose"].position.z),
            ),
            status="stuck",
            timestamp=time.time(),
            cooldown_until=time.time() + 60.0,
        )
    ]

    second = nav._find_nearest_memory_blindspot(
        search_radius_m=3.0,
        coverage_radius_m=0.5,
        failed_regions=failed_regions,
    )

    assert second is not None
    assert float(second["score"]) >= float(first["score"]) + 2.0


def test_region_failure_attempt_limit_skips_cooldown_region() -> None:
    nav = _nav_container()
    grid = np.zeros((11, 11), dtype=np.int8)
    nav._latest_odom = _pose(0.0, 0.0)
    nav._latest_global_costmap = OccupancyGrid(grid=grid, resolution=0.5, frame_id="map")
    nav._spatial_memory = SimpleNamespace(get_memory_locations=lambda: [])

    first = nav._find_nearest_memory_blindspot(search_radius_m=3.0, coverage_radius_m=0.5)
    assert first is not None
    failed_regions = [
        MemoryBlindspotVisitState(
            region_id=str(first["region_id"]),
            region_fingerprint=str(first["region_fingerprint"]),
            target_pose=(
                float(first["pose"].position.x),
                float(first["pose"].position.y),
                float(first["pose"].position.z),
            ),
            status="stuck",
            timestamp=time.time(),
            cooldown_until=time.time() + 60.0,
            attempts=2,
        )
    ]

    second = nav._find_nearest_memory_blindspot(
        search_radius_m=3.0,
        coverage_radius_m=0.5,
        failed_regions=failed_regions,
        max_region_attempts=2,
    )

    assert second is None


def test_blindspot_recovery_snaps_from_unsafe_start_cell() -> None:
    nav = _nav_container()
    grid = np.zeros((7, 7), dtype=np.int8)
    grid[3, 3] = CostValues.OCCUPIED
    nav._latest_odom = _pose(1.5, 1.5)
    nav._latest_global_costmap = OccupancyGrid(grid=grid, resolution=0.5, frame_id="map")
    nav._navigation = _FakeNavigation()
    nav._unitree_skill_container = None
    nav._memory_blindspot_patrol_stop = False

    recovered = nav._attempt_memory_blindspot_recovery(
        timeout_sec=1.0,
        clearance_m=0.0,
        recovery_radius_m=2.0,
        recovery_min_move_m=0.1,
    )

    assert recovered
    assert nav._navigation.cancel_count == 1
    assert nav._navigation.goals
    goal_grid = nav._latest_global_costmap.world_to_grid(
        (
            nav._navigation.goals[-1].position.x,
            nav._navigation.goals[-1].position.y,
            nav._navigation.goals[-1].position.z,
        )
    )
    assert int(grid[round(goal_grid.y), round(goal_grid.x)]) != CostValues.OCCUPIED


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
    # _rotate_scan_in_place now sweeps in 6×60° steps (a single 360° request was
    # a no-op due to yaw wrapping in the planner). Sweeping office then lab
    # produces 12 rotation calls of 60° each.
    assert nav._unitree_skill_container.rotations == [60.0] * 12


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
    # Pretend the camera sees the trash can; this test is about arrival logic,
    # not visual acquisition.
    nav._visual_acquire_object = lambda name, stored_yaw=None, **_kw: f"Visually acquired '{name}'"

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


def test_visual_acquire_succeeds_when_target_already_in_view() -> None:
    """If the VLM sees the target in the current frame, do not rotate at all."""
    nav = _nav_container()
    nav._unitree_skill_container = _FakeUnitree()
    nav._latest_image = SimpleNamespace(data=object())
    nav._latest_odom = _pose(0.0, 0.0, yaw=0.0)
    nav._vlm_object_present_in_view = lambda query, **_kw: True

    result = nav._visual_acquire_object("垃圾桶", stored_yaw=None)

    assert result is not None
    assert "Visually acquired '垃圾桶'" == result
    assert nav._unitree_skill_container.rotations == []


def test_visual_acquire_finds_target_after_partial_rotation() -> None:
    """VLM misses on the first frame, then hits on the 3rd 60° step (i.e. 180°)."""
    nav = _nav_container()
    nav._unitree_skill_container = _FakeUnitree()
    nav._latest_image = SimpleNamespace(data=object())
    nav._latest_odom = _pose(0.0, 0.0, yaw=0.0)

    # Sequence: current view miss, step1 miss, step2 miss, step3 HIT.
    sightings = iter([False, False, False, True])
    nav._vlm_object_present_in_view = lambda query, **_kw: next(sightings)

    result = nav._visual_acquire_object("垃圾桶", stored_yaw=None)

    assert result is not None
    assert "180° scan" in result
    # Robot rotated 3 times of 60° before sighting.
    assert nav._unitree_skill_container.rotations == [60.0, 60.0, 60.0]


def test_visual_acquire_fails_after_full_six_step_sweep() -> None:
    """Full 6x60deg sweep with no VLM hits → return None (failure)."""
    nav = _nav_container()
    nav._unitree_skill_container = _FakeUnitree()
    nav._latest_image = SimpleNamespace(data=object())
    nav._latest_odom = _pose(0.0, 0.0, yaw=0.0)
    nav._vlm_object_present_in_view = lambda query, **_kw: False  # VLM never sees the target

    result = nav._visual_acquire_object("人", stored_yaw=None)

    assert result is None
    # 6 rotation steps of 60° each (no extra rotation since stored_yaw=None).
    assert nav._unitree_skill_container.rotations == [60.0] * 6


def test_visual_acquire_turns_to_stored_bearing_before_first_check() -> None:
    """When a stored bearing is given, the first action is to face it (>5° delta)."""
    nav = _nav_container()
    nav._unitree_skill_container = _FakeUnitree()
    nav._latest_image = SimpleNamespace(data=object())
    nav._latest_odom = _pose(0.0, 0.0, yaw=0.0)  # robot facing 0 rad
    nav._vlm_object_present_in_view = lambda query, **_kw: True  # hits after turn

    import math as _m

    result = nav._visual_acquire_object("垃圾桶", stored_yaw=_m.radians(45.0))

    assert result == "Visually acquired '垃圾桶'"
    # Exactly one bearing-alignment rotation (~45° within rounding), then VLM hit.
    assert len(nav._unitree_skill_container.rotations) == 1
    assert nav._unitree_skill_container.rotations[0] == pytest.approx(45.0, abs=0.1)


def test_visual_acquire_passes_description_to_presence_check() -> None:
    """When ``_visual_acquire_object`` is given a description, it must reach
    the per-frame VLM call so same-radical confusions (水瓶 ≠ 水桶) get
    disambiguated by the model on the same single round-trip."""
    nav = _nav_container()
    nav._unitree_skill_container = _FakeUnitree()
    nav._latest_image = SimpleNamespace(data=object())
    nav._latest_odom = _pose(0.0, 0.0, yaw=0.0)

    captured: list[dict[str, object]] = []

    def _stub(query: str, **kwargs: object) -> bool:
        captured.append({"query": query, **kwargs})
        return True

    nav._vlm_object_present_in_view = _stub

    nav._visual_acquire_object("水瓶", stored_yaw=None, description="桌上的透明矿泉水瓶")

    assert captured == [{"query": "水瓶", "description": "桌上的透明矿泉水瓶"}]


def test_verify_object_in_view_returns_yes_when_visually_acquired() -> None:
    """verify_object_in_view is a thin wrapper around _visual_acquire_object."""
    nav = _nav_container()
    nav._latest_image = SimpleNamespace(data=object())
    nav._landmark_memory = SimpleNamespace(resolve_by_query=lambda _q: None)
    nav._visual_acquire_object = lambda name, stored_yaw=None, **_kw: f"Visually acquired '{name}'"

    result = nav.verify_object_in_view("展示板")

    assert result.startswith("YES:")
    assert "Visually acquired '展示板'" in result


def test_verify_object_in_view_forwards_landmark_state_as_description() -> None:
    """Regression for 水瓶 vs 水桶: when a landmark with a stored ``state``
    description exists, ``verify_object_in_view`` must forward that string
    to the VLM presence check on the same round-trip — that hint is what
    lets the model reject same-radical confusions without a second call."""
    nav = _nav_container()
    nav._latest_image = SimpleNamespace(data=object())

    landmark = SpatialRecord(
        name="水瓶",
        record_type=RecordType.LANDMARK,
        position=(0.0, 0.0, 0.0),
        state="桌上的透明矿泉水瓶 (views [0, 5])",
    )
    nav._landmark_memory = SimpleNamespace(resolve_by_query=lambda _q: landmark)

    captured: list[dict[str, object]] = []

    def _stub_visual_acquire(name: str, stored_yaw: float | None = None, **kwargs: object) -> str:
        captured.append({"name": name, "stored_yaw": stored_yaw, **kwargs})
        return f"Visually acquired '{name}'"

    nav._visual_acquire_object = _stub_visual_acquire

    nav.verify_object_in_view("水瓶")

    assert captured == [
        {
            "name": "水瓶",
            "stored_yaw": None,
            "description": "桌上的透明矿泉水瓶 (views [0, 5])",
        }
    ]


def test_verify_object_in_view_returns_no_when_not_visible() -> None:
    """When the visual acquire returns None, the skill must tell the agent
    clearly that the object is NOT here and hint the next step."""
    nav = _nav_container()
    nav._latest_image = SimpleNamespace(data=object())
    nav._landmark_memory = SimpleNamespace(resolve_by_query=lambda _q: None)
    nav._visual_acquire_object = lambda name, stored_yaw=None, **_kw: None

    result = nav.verify_object_in_view("展示板")

    assert result.startswith("NO:")
    assert "'展示板'" in result
    assert "navigate_with_text" in result


def test_verify_object_in_view_handles_missing_camera() -> None:
    """No camera image → quick NO without trying to scan."""
    nav = _nav_container()
    nav._latest_image = None

    result = nav.verify_object_in_view("展示板")

    assert result.startswith("NO:")
    assert "no camera image" in result.lower()


def test_object_landmark_reports_failure_when_camera_cannot_see() -> None:
    """When the camera cannot see the landmark, do NOT run the arrival_action
    and return a clear failure so the agent can react (e.g. detect_objects_in_view)."""
    nav = _nav_container()
    target = SpatialRecord(
        name="人",
        record_type=RecordType.LANDMARK,
        position=(0.0, 0.0, 0.0),
    )
    nav._navigation = _FakeNavigation()
    nav._latest_odom = _pose(0.8, 0.0)
    nav._landmark_memory = SimpleNamespace(get_all=lambda: [target])
    nav._relocalize_interval_s = 30.0
    nav._coordinate_frame_stale_reason = lambda target: None
    nav._unitree_skill_container = _FakeUnitree()
    nav._visual_acquire_object = lambda name, stored_yaw=None, **_kw: None  # camera sees nothing

    result = nav._navigate_to_landmark(
        target,
        arrival_action="point",
        arrival_distance=0.5,
        run_arrival_action=True,
    )

    assert "could not visually acquire" in result
    assert "arrival_action skipped" in result
    assert nav._unitree_skill_container.commands == []


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
