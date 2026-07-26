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

from dimos.agents.skills.navigation import (
    NavigationSkillContainer,
    _EnrouteHitOutcome,
    _EnrouteHitStatus,
    _ObjectSearchContext,
    _SearchFrameSnapshot,
    _SearchHit,
    _VisualLockResult,
    _VisualLockStatus,
)
from dimos.core.core import rpc
from dimos.core.module import Module
from dimos.core.stream import Out
from dimos.msgs.geometry_msgs.PoseStamped import PoseStamped
from dimos.msgs.geometry_msgs.Quaternion import Quaternion
from dimos.msgs.geometry_msgs.Vector3 import Vector3, make_vector3
from dimos.msgs.sensor_msgs.Image import Image
from dimos.navigation.base import NavigationState
from dimos.types.robot_location import RobotLocation
from dimos.types.spatial_record import RecordType, SpatialRecord


class FakeCamera(Module):
    color_image: Out[Image]


class FakeOdom(Module):
    odom: Out[PoseStamped]


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
    nav._sweep_skip_rooms = set()
    nav._latest_image = None
    nav._latest_odom = None
    nav._memory_session_id = "test_session"
    nav._relocalization = None
    nav._ensure_search_runtime()
    return nav


def _confirmed_visual_lock(
    target_name: str,
    *,
    ts: float = 12.0,
    bbox: tuple[float, float, float, float] = (35.0, 10.0, 65.0, 50.0),
) -> _VisualLockResult:
    return _VisualLockResult(
        status=_VisualLockStatus.CONFIRMED,
        message=f"Visually confirmed '{target_name}' in a fresh centered frame",
        image=Image.from_numpy(np.zeros((60, 100, 3), dtype=np.uint8), ts=ts),
        bbox=bbox,
        offset_deg=0.0,
        checks=1,
    )


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

    def rotate_in_place_degrees(self, degrees: float) -> bool:
        self.rotations.append(degrees)
        return True

    def relative_move(self, forward: float, left: float, degrees: float) -> bool:
        self.rotations.append(degrees)
        return True

    def execute_sport_command(self, command: str) -> str:
        self.commands.append(command)
        return "ok"


def test_rotate_in_place_prefers_closed_loop_spin() -> None:
    nav = _nav_container()
    fake = _FakeUnitree()
    nav._unitree_skill_container = fake

    assert nav._rotate_in_place_degrees(120.0) is True
    assert fake.rotations == [120.0]


def test_scan_room_for_object_uses_search_rotation_steps(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DIMOS_ROTATION_STEP_DEG", "100")
    monkeypatch.setattr(time, "sleep", lambda _seconds: None)
    nav = _nav_container()
    fake = _FakeUnitree()
    nav._unitree_skill_container = fake
    nav._latest_odom = PoseStamped(
        position=make_vector3(0.0, 0.0, 0.0),
        orientation=Quaternion.from_euler(Vector3(0.0, 0.0, 0.0)),
    )
    nav._detect_and_servo = lambda _name: None  # type: ignore[method-assign]

    assert nav._scan_room_for_object("电脑") is None
    assert fake.rotations == [80.0, 80.0, 80.0, 80.0, 80.0]


def test_nav_fallback_default_is_object_room(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DIMOS_NAV_FALLBACK", raising=False)
    nav = _nav_container()
    assert nav._nav_fallback_strategy() == "object_room"


def test_search_snapshot_uses_odom_nearest_to_image_timestamp(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DIMOS_SEARCH_POSE_SYNC_TOLERANCE_S", "0.2")
    nav = _nav_container()
    now = time.time()
    nav._on_odom(
        PoseStamped(
            ts=now - 0.15,
            position=make_vector3(1.0, 0.0, 0.0),
            orientation=Quaternion.from_euler(Vector3(0.0, 0.0, 0.0)),
        )
    )
    nav._on_odom(
        PoseStamped(
            ts=now + 0.04,
            position=make_vector3(2.0, 0.0, 0.0),
            orientation=Quaternion.from_euler(Vector3(0.0, 0.0, 0.0)),
        )
    )
    nav._on_color_image(Image.from_numpy(np.zeros((40, 60, 3), dtype=np.uint8), ts=now))
    context = _ObjectSearchContext(search_id="search_sync", query="垃圾桶", active_leg_id=3)

    snapshot = nav._build_search_snapshot(context, leg_id=3)

    assert snapshot is not None
    assert snapshot.capture_pose_world.position.x == pytest.approx(2.0)
    assert snapshot.image_ts == pytest.approx(now)


def test_enroute_vlm_waits_for_navigation_displacement_before_detecting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DIMOS_SEARCH_START_DISPLACEMENT_M", "0.2")
    monkeypatch.setenv("DIMOS_SEARCH_VLM_INTERVAL_S", "0.1")
    calls: list[str] = []

    def fake_bbox(_model: Any, _image: Image, query: str):
        calls.append(query)
        return (40.0, 10.0, 60.0, 35.0)

    def run_thread_inline(*, target: Any, args: tuple[Any, ...], **_kwargs: Any) -> Any:
        return SimpleNamespace(start=lambda: target(*args))

    monkeypatch.setattr("dimos.agents.skills.navigation.get_object_bbox_from_image", fake_bbox)
    monkeypatch.setattr("dimos.agents.skills.navigation.threading.Thread", run_thread_inline)
    nav = _nav_container()
    nav._vl_model = object()
    now = time.time()
    nav._on_odom(_pose(0.0, 0.0))
    nav._on_color_image(Image.from_numpy(np.zeros((50, 100, 3), dtype=np.uint8), ts=now))
    context = nav._new_object_search_context("垃圾桶")
    nav._begin_search_leg(context, "历史位置")

    nav._maybe_submit_enroute_vlm(context)
    assert calls == []
    assert context.vlm_in_flight is False

    moved_at = time.time()
    nav._on_odom(
        PoseStamped(
            ts=moved_at,
            position=make_vector3(0.3, 0.0, 0.0),
            orientation=Quaternion.from_euler(Vector3(0.0, 0.0, 0.0)),
        )
    )
    nav._on_color_image(Image.from_numpy(np.zeros((50, 100, 3), dtype=np.uint8), ts=moved_at))
    nav._maybe_submit_enroute_vlm(context)

    assert context.hit_event.wait(timeout=1.0)
    assert calls == ["垃圾桶"]
    assert context.hit is not None
    assert context.hit.snapshot.capture_pose_world.position.x == pytest.approx(0.3)


def test_stale_leg_vlm_result_cannot_interrupt_current_route(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "dimos.agents.skills.navigation.get_object_bbox_from_image",
        lambda _model, _image, _query: (10.0, 10.0, 30.0, 30.0),
    )
    nav = _nav_container()
    nav._vl_model = object()
    now = time.time()
    image = Image.from_numpy(np.zeros((50, 100, 3), dtype=np.uint8), ts=now)
    snapshot = _SearchFrameSnapshot(
        search_id="search_stale",
        leg_id=1,
        image=image,
        image_ts=now,
        capture_pose_world=_pose(0.5, 0.0),
        map_metadata={"relocalization_bound": False},
        submitted_at=now,
    )
    context = _ObjectSearchContext(
        search_id="search_stale",
        query="饮水机",
        active_leg_id=2,
        monitor_enabled=True,
        vlm_in_flight=True,
    )

    nav._run_enroute_vlm(context, snapshot)

    assert context.hit is None
    assert context.hit_event.is_set() is False
    assert context.vlm_in_flight is False


def test_same_leg_vlm_result_remains_valid_after_route_stops_submitting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "dimos.agents.skills.navigation.get_object_bbox_from_image",
        lambda _model, _image, _query: (10.0, 10.0, 30.0, 30.0),
    )
    nav = _nav_container()
    nav._vl_model = object()
    now = time.time()
    snapshot = _SearchFrameSnapshot(
        search_id="search_late",
        leg_id=4,
        image=Image.from_numpy(np.zeros((50, 100, 3), dtype=np.uint8), ts=now),
        image_ts=now,
        capture_pose_world=_pose(0.5, 0.0),
        map_metadata={"relocalization_bound": False},
        submitted_at=now,
    )
    context = _ObjectSearchContext(
        search_id="search_late",
        query="饮水机",
        active_leg_id=4,
        monitor_enabled=False,
        vlm_in_flight=True,
    )

    nav._run_enroute_vlm(context, snapshot)

    assert context.hit_event.is_set()
    assert context.hit is not None
    assert context.hit.snapshot.capture_pose_world.position.x == pytest.approx(0.5)


def test_enroute_hit_returns_to_capture_pose_faces_and_greets_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DIMOS_SEARCH_CANCEL_WAIT_S", "0")
    monkeypatch.setenv("DIMOS_SEARCH_REWIND_THRESHOLD_M", "0.4")
    monkeypatch.setattr("dimos.agents.skills.navigation.time.sleep", lambda _seconds: None)
    nav = _nav_container()
    nav._navigation = _FakeNavigation()
    nav._unitree_skill_container = _FakeUnitree()
    nav._latest_odom = _pose(2.0, 0.0, yaw=0.0)
    confirmed_image = Image.from_numpy(
        np.full((60, 100, 3), 127, dtype=np.uint8),
        ts=time.time() + 1.0,
    )
    monkeypatch.setattr(
        nav,
        "_confirm_enroute_hit",
        lambda context: _VisualLockResult(
            status=_VisualLockStatus.CONFIRMED,
            message=f"Visually confirmed '{context.query}' in a fresh centered frame",
            image=confirmed_image,
            bbox=(35.0, 10.0, 65.0, 50.0),
            offset_deg=0.0,
            checks=1,
        ),
    )
    monkeypatch.setattr(
        "dimos.agents.skills.navigation._timestamped_snapshot_stem",
        lambda record_id: f"20260716-111032_{record_id}",
    )
    recorded: list[SpatialRecord] = []
    saved_snapshots: list[tuple[str, bytes]] = []
    existing = SpatialRecord(
        name="灭火器",
        record_type=RecordType.LANDMARK,
        record_id="rec_existing_object",
    )
    nav._landmark_memory = SimpleNamespace(
        query_by_type=lambda _record_type: [],
        find_by_name=lambda _name: existing,
        save_snapshot=lambda record_id, image_bytes: (
            saved_snapshots.append((record_id, image_bytes)) or f"/snapshots/{record_id}.jpg"
        ),
        record=lambda record: recorded.append(record) or existing.record_id,
    )
    now = time.time()
    image = Image.from_numpy(np.zeros((60, 100, 3), dtype=np.uint8), ts=now)
    snapshot = _SearchFrameSnapshot(
        search_id="search_return",
        leg_id=1,
        image=image,
        image_ts=now,
        capture_pose_world=_pose(0.0, 0.0, yaw=0.0),
        map_metadata={"relocalization_bound": False},
        submitted_at=now - 2.0,
    )
    object_yaw_world, _ = nav._object_yaws_from_bbox(
        snapshot,
        (70.0, 10.0, 90.0, 50.0),
    )
    hit = _SearchHit(
        snapshot=snapshot,
        bbox=(70.0, 10.0, 90.0, 50.0),
        detected_at=now,
        object_yaw_world=object_yaw_world,
        object_yaw_map=None,
    )
    context = _ObjectSearchContext(search_id="search_return", query="灭火器", hit=hit)
    context.hit_event.set()

    outcome = nav._finish_enroute_hit(context)

    assert outcome.status == _EnrouteHitStatus.CONFIRMED
    assert "returned to the capture viewpoint" in outcome.message
    assert len(nav._navigation.goals) == 1
    assert nav._navigation.goals[0].position.x == pytest.approx(0.0)
    assert nav._unitree_skill_container.rotations[0] < 0.0
    assert nav._unitree_skill_container.commands == ["Hello", "RecoveryStand"]
    assert len(recorded) == 1
    assert recorded[0].name == "灭火器"
    assert recorded[0].position[0] == pytest.approx(0.0)
    assert recorded[0].metadata["position_semantics"] == "observation_viewpoint"
    assert recorded[0].metadata["confirmation_image_ts"] == pytest.approx(confirmed_image.ts)
    assert recorded[0].metadata["confirmation_bbox"] == [35.0, 10.0, 65.0, 50.0]
    assert recorded[0].image_snapshot_path == "/snapshots/20260716-111032_rec_existing_object.jpg"
    assert len(saved_snapshots) == 1
    assert saved_snapshots[0][0] == "20260716-111032_rec_existing_object"
    assert saved_snapshots[0][1].startswith(b"\xff\xd8")


def test_room_initial_snapshot_uses_timestamped_filename(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    nav = _nav_container()
    nav._latest_odom = _pose(1.0, 2.0, yaw=0.3)
    nav._latest_image = Image.from_numpy(np.zeros((60, 100, 3), dtype=np.uint8))
    nav._spatial_memory = SimpleNamespace(
        tag_location=lambda _location: True,
        tag_location_with_image=lambda _location, _image: True,
    )
    recorded: list[SpatialRecord] = []
    saved_snapshots: list[str] = []
    nav._landmark_memory = SimpleNamespace(
        save_snapshot=lambda record_id, _image_bytes: (
            saved_snapshots.append(record_id) or f"/snapshots/{record_id}.jpg"
        ),
        record=lambda record: recorded.append(record) or record.record_id,
    )
    monkeypatch.setattr(
        "dimos.agents.skills.navigation._timestamped_snapshot_stem",
        lambda record_id: f"20260716-111032_{record_id}",
    )
    monkeypatch.setattr(
        "dimos.agents.skills.navigation.threading.Thread",
        lambda **_kwargs: SimpleNamespace(start=lambda: None),
    )

    result = nav.tag_location("办公室", num_photos=0)

    assert result.startswith("Tagged '办公室'")
    assert len(recorded) == 1
    expected_stem = f"20260716-111032_{recorded[0].record_id}"
    assert saved_snapshots == [expected_stem]
    assert recorded[0].image_snapshot_path == f"/snapshots/{expected_stem}.jpg"


def test_enroute_confirmation_uses_fresh_bbox_and_rechecks_after_servo(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DIMOS_SEARCH_CONFIRM_MAX_CHECKS", "2")
    nav = _nav_container()
    nav._vl_model = object()
    nav._unitree_skill_container = _FakeUnitree()
    nav._latest_image = Image.from_numpy(np.zeros((60, 100, 3), dtype=np.uint8), ts=10.0)
    frames = iter(
        [
            Image.from_numpy(np.zeros((60, 100, 3), dtype=np.uint8), ts=11.0),
            Image.from_numpy(np.zeros((60, 100, 3), dtype=np.uint8), ts=12.0),
        ]
    )
    boxes = iter([(65.0, 10.0, 95.0, 50.0), (35.0, 10.0, 65.0, 50.0)])

    def fake_wait() -> Image:
        return next(frames)

    monkeypatch.setattr(nav, "_wait_for_fresh_search_image", fake_wait)
    monkeypatch.setattr(
        "dimos.agents.skills.navigation.get_object_bbox_from_image",
        lambda _model, _image, _query: next(boxes),
    )
    context = _ObjectSearchContext(search_id="search_confirm", query="灭火器")

    confirmation = nav._confirm_enroute_hit(context)

    assert confirmation.confirmed
    assert confirmation.message == "Visually confirmed '灭火器' in a fresh centered frame"
    assert confirmation.image is not None
    assert confirmation.image.ts == pytest.approx(12.0)
    assert confirmation.bbox == (35.0, 10.0, 65.0, 50.0)
    assert confirmation.checks == 2
    assert len(nav._unitree_skill_container.rotations) == 1
    assert nav._unitree_skill_container.rotations[0] < 0.0


def test_enroute_confirmation_waits_past_cached_camera_frame(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DIMOS_SEARCH_CONFIRM_SETTLE_S", "0")
    nav = _nav_container()
    nav._latest_image = Image.from_numpy(np.zeros((60, 100, 3), dtype=np.uint8), ts=10.0)
    published = False

    def publish_next_frame(_seconds: float) -> None:
        nonlocal published
        if not published:
            published = True
            nav._on_color_image(Image.from_numpy(np.zeros((60, 100, 3), dtype=np.uint8), ts=11.0))

    monkeypatch.setattr("dimos.agents.skills.navigation.time.sleep", publish_next_frame)

    image = nav._wait_for_fresh_search_image()

    assert image is not None
    assert image.ts == pytest.approx(11.0)
    assert published is True


def test_fresh_frame_gate_rejects_new_callback_with_stale_timestamp(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DIMOS_SEARCH_CONFIRM_SETTLE_S", "0")
    monkeypatch.setenv("DIMOS_SEARCH_CONFIRM_FRAME_TIMEOUT_S", "0.1")
    nav = _nav_container()
    nav._latest_image = Image.from_numpy(np.zeros((60, 100, 3), dtype=np.uint8), ts=10.0)
    ticks = iter([0.0, 0.0, 0.01, 1.0])
    published = False

    def publish_stale_frame(_seconds: float) -> None:
        nonlocal published
        if not published:
            published = True
            nav._on_color_image(Image.from_numpy(np.ones((60, 100, 3), dtype=np.uint8), ts=10.0))

    monkeypatch.setattr("dimos.agents.skills.navigation.time.monotonic", lambda: next(ticks))
    monkeypatch.setattr("dimos.agents.skills.navigation.time.sleep", publish_stale_frame)

    image = nav._wait_for_fresh_search_image()

    assert image is None
    assert published is True
    assert nav._latest_image_sequence == 1


def test_visual_lock_rotation_success_but_fresh_frame_miss_is_unconfirmed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    nav = _nav_container()
    nav._vl_model = object()
    nav._unitree_skill_container = _FakeUnitree()
    fresh = Image.from_numpy(np.zeros((60, 100, 3), dtype=np.uint8), ts=11.0)
    monkeypatch.setattr(nav, "_wait_for_fresh_search_image", lambda: fresh)
    monkeypatch.setattr(
        "dimos.agents.skills.navigation.get_object_bbox_from_image",
        lambda _model, _image, _query: None,
    )

    result = nav._confirm_visual_lock("灭火器", trace_id="test")

    assert result.status == _VisualLockStatus.UNCONFIRMED
    assert result.reason == "vlm_miss_or_bad_bbox"
    assert result.checks == 1
    assert nav._unitree_skill_container.commands == []


def test_visual_lock_stops_after_max_checks_when_target_never_centers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DIMOS_SEARCH_CONFIRM_MAX_CHECKS", "2")
    nav = _nav_container()
    nav._vl_model = object()
    nav._unitree_skill_container = _FakeUnitree()
    frames = iter(
        [
            Image.from_numpy(np.zeros((60, 100, 3), dtype=np.uint8), ts=11.0),
            Image.from_numpy(np.zeros((60, 100, 3), dtype=np.uint8), ts=12.0),
        ]
    )
    monkeypatch.setattr(nav, "_wait_for_fresh_search_image", lambda: next(frames))
    monkeypatch.setattr(
        "dimos.agents.skills.navigation.get_object_bbox_from_image",
        lambda _model, _image, _query: (65.0, 10.0, 95.0, 50.0),
    )

    result = nav._confirm_visual_lock("灭火器", trace_id="test")

    assert result.status == _VisualLockStatus.UNCONFIRMED
    assert result.reason == "not_centered"
    assert result.checks == 2
    assert len(nav._unitree_skill_container.rotations) == 1
    assert nav._unitree_skill_container.commands == []


def test_detect_and_servo_freezes_source_frame_and_saves_confirmed_frame(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    nav = _nav_container()
    source = Image.from_numpy(np.zeros((60, 100, 3), dtype=np.uint8), ts=10.0)
    nav._on_color_image(source)
    nav._vl_model = object()
    existing = SpatialRecord(
        name="灭火器",
        record_type=RecordType.LANDMARK,
        record_id="rec_extinguisher",
    )
    saved: list[str] = []
    nav._landmark_memory = SimpleNamespace(
        find_by_name=lambda _name: existing,
        save_snapshot=lambda stem, _data: saved.append(stem) or f"/snapshots/{stem}.jpg",
    )
    detector_images: list[Image] = []
    servo_images: list[Image] = []

    def detect(_model: Any, image: Image, _query: str) -> tuple[float, float, float, float]:
        detector_images.append(image)
        nav._on_color_image(Image.from_numpy(np.zeros((120, 200, 3), dtype=np.uint8), ts=11.0))
        return (65.0, 10.0, 95.0, 50.0)

    def servo(_bbox: tuple[float, float, float, float], image: Image) -> bool:
        servo_images.append(image)
        return True

    monkeypatch.setattr(
        "dimos.agents.skills.navigation.get_object_bbox_from_image",
        detect,
    )
    monkeypatch.setattr(nav, "_servo_to_bbox", servo)
    monkeypatch.setattr(
        nav,
        "_confirm_visual_lock",
        lambda _target, **_kwargs: _confirmed_visual_lock("灭火器"),
    )
    monkeypatch.setattr(
        "dimos.agents.skills.navigation._timestamped_snapshot_stem",
        lambda record_id: f"20260724-120000_{record_id}",
    )

    result = nav._detect_and_servo("灭火器")

    assert result is not None
    assert result.confirmed
    assert detector_images[0].ts == pytest.approx(10.0)
    assert servo_images[0].ts == pytest.approx(10.0)
    assert servo_images[0].data.shape == (60, 100, 3)
    assert saved == ["20260724-120000_rec_extinguisher"]
    assert result.snapshot_path == "/snapshots/20260724-120000_rec_extinguisher.jpg"


def test_unconfirmed_enroute_hit_does_not_store_greet_or_finish_search(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DIMOS_SEARCH_CANCEL_WAIT_S", "0")
    monkeypatch.setenv("DIMOS_SEARCH_REWIND_THRESHOLD_M", "0.4")
    monkeypatch.setattr("dimos.agents.skills.navigation.time.sleep", lambda _seconds: None)
    nav = _nav_container()
    nav._navigation = _FakeNavigation()
    nav._unitree_skill_container = _FakeUnitree()
    nav._latest_odom = _pose(2.0, 0.0)
    recorded: list[SpatialRecord] = []
    nav._landmark_memory = SimpleNamespace(
        query_by_type=lambda _record_type: [],
        record=lambda record: recorded.append(record) or record.record_id,
    )
    monkeypatch.setattr(
        nav,
        "_confirm_enroute_hit",
        lambda _context: _VisualLockResult(
            status=_VisualLockStatus.UNCONFIRMED,
            message="not confirmed",
            reason="vlm_miss_or_bad_bbox",
        ),
    )
    now = time.time()
    snapshot = _SearchFrameSnapshot(
        search_id="search_unconfirmed",
        leg_id=1,
        image=Image.from_numpy(np.zeros((60, 100, 3), dtype=np.uint8), ts=now),
        image_ts=now,
        capture_pose_world=_pose(0.0, 0.0),
        map_metadata={"relocalization_bound": False},
        submitted_at=now,
    )
    hit = _SearchHit(
        snapshot=snapshot,
        bbox=(35.0, 10.0, 65.0, 50.0),
        detected_at=now,
        object_yaw_world=0.0,
        object_yaw_map=None,
    )
    context = _ObjectSearchContext(
        search_id="search_unconfirmed",
        query="灭火器",
        hit=hit,
    )
    context.hit_event.set()

    outcome = nav._finish_enroute_hit(context)

    assert outcome.status == _EnrouteHitStatus.UNCONFIRMED
    assert "resuming the original search route" in outcome.message
    assert recorded == []
    assert nav._unitree_skill_container.commands == []
    assert context.cancel_event.is_set() is False
    assert context.hit is None
    assert context.hit_event.is_set() is False
    assert context.terminal_result is None


def test_unconfirmed_enroute_hit_resumes_same_target_without_monitoring(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    nav = _nav_container()
    target = SpatialRecord(
        name="历史灭火器位置",
        record_type=RecordType.LANDMARK,
        position=(2.0, 0.0, 0.0),
    )
    context = _ObjectSearchContext(search_id="search_resume", query="灭火器")
    monkeypatch.setattr(
        nav,
        "_finish_enroute_hit",
        lambda _context: _EnrouteHitOutcome(
            _EnrouteHitStatus.UNCONFIRMED,
            "not confirmed",
        ),
    )
    resumed: list[dict[str, Any]] = []

    def fake_navigate(_target: SpatialRecord, **kwargs: Any) -> str:
        resumed.append(kwargs)
        return "continued to historical target"

    monkeypatch.setattr(nav, "_navigate_to_landmark", fake_navigate)

    result = nav._finish_or_resume_enroute_hit(
        context,
        target,
        arrival_action="point",
        arrival_distance=0.5,
        run_arrival_action=True,
        relocalize_interval=None,
        enable_visual_drift=False,
    )

    assert result == "continued to historical target"
    assert len(resumed) == 1
    assert resumed[0]["search_context"] is None
    assert resumed[0]["arrival_action"] == "point"


def test_enroute_hit_reprojects_map_capture_pose_with_current_relocalization() -> None:
    nav = _nav_container()
    current_world_from_map = np.eye(4)
    current_world_from_map[0, 3] = 10.0
    nav._relocalization = SimpleNamespace(
        is_relocalized=lambda: True,
        get_current_map_key=lambda: "office_map",
        get_current_map_file=lambda: "office_map.pc2.lcm",
        get_world_to_map=lambda: SimpleNamespace(to_matrix=lambda: current_world_from_map),
    )
    now = time.time()
    snapshot = _SearchFrameSnapshot(
        search_id="search_map",
        leg_id=1,
        image=Image.from_numpy(np.zeros((60, 100, 3), dtype=np.uint8), ts=now),
        image_ts=now,
        capture_pose_world=_pose(99.0, 99.0, yaw=0.0),
        map_metadata={
            "relocalization_bound": True,
            "map_key": "office_map",
            "pose_map": {
                "position": [1.0, 2.0, 0.0],
                "rotation": [0.0, 0.0, 0.2],
            },
        },
        submitted_at=now,
    )
    hit = _SearchHit(
        snapshot=snapshot,
        bbox=(40.0, 10.0, 60.0, 50.0),
        detected_at=now,
        object_yaw_world=0.3,
        object_yaw_map=0.7,
    )

    capture_pose = nav._capture_pose_for_hit(hit)

    assert capture_pose is not None
    assert capture_pose.position.x == pytest.approx(11.0)
    assert capture_pose.position.y == pytest.approx(2.0)
    assert capture_pose.orientation.to_euler().z == pytest.approx(0.7)


def test_room_sweep_forwards_enroute_search_and_stops_after_terminal_hit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    nav = _nav_container()
    room = SpatialRecord(
        name="会议室B",
        record_type=RecordType.ROOM,
        position=(4.0, 0.0, 0.0),
    )
    nav._landmark_memory = SimpleNamespace(
        query_by_type=lambda _record_type: [room],
        resolve_by_query=lambda _query: None,
    )
    context = _ObjectSearchContext(search_id="search_rooms", query="灭火器")
    scanned_rooms: list[str] = []

    def fake_navigation(target: SpatialRecord, **kwargs: Any) -> str:
        assert kwargs["search_context"] is context
        context.terminal_result = "Found '灭火器' en route"
        context.terminal_status = _EnrouteHitStatus.CONFIRMED
        return context.terminal_result

    monkeypatch.setattr(nav, "_navigate_to_landmark", fake_navigation)
    monkeypatch.setattr(
        nav,
        "_scan_room_for_object",
        lambda query, **_kwargs: scanned_rooms.append(query) or None,
    )

    result = nav._room_anchor_sweep_for_object("灭火器", search_context=context)

    assert result is not None
    assert result.confirmed
    assert result.message == "Found '灭火器' en route"
    assert scanned_rooms == []


def test_search_context_skips_current_frame_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    nav = _nav_container()
    context = _ObjectSearchContext(search_id="search_no_current", query="木箱")
    current_frame_calls: list[str] = []
    monkeypatch.setattr(nav, "_nav_fallback_strategy", lambda: "semantic")
    monkeypatch.setattr(nav, "_nav_fallback_step_landmark", lambda _query, _ctx=None: None)
    monkeypatch.setattr(
        nav,
        "_nav_fallback_step_in_frame",
        lambda query: current_frame_calls.append(query) or "unexpected hit",
    )
    monkeypatch.setattr(
        nav,
        "_nav_fallback_step_room_sweep",
        lambda _query, _ctx=None: "room search started",
    )

    result = nav._run_navigate_fallback_chain("木箱", context)

    assert result == "room search started"
    assert current_frame_calls == []


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


def test_navigate_with_text_keeps_arrival_only_search_when_enroute_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("DIMOS_ENROUTE_OBJECT_SEARCH_ENABLED", raising=False)
    monkeypatch.setenv("DIMOS_NAV_FALLBACK", "object_room")
    nav = _nav_container()
    target = SpatialRecord(
        name="垃圾桶",
        record_type=RecordType.LANDMARK,
        position=(2.0, 0.0, 0.0),
    )
    received_contexts: list[_ObjectSearchContext | None] = []
    monkeypatch.setattr(nav, "_resolve_landmark_from_query", lambda _query: target)

    def fake_navigation(_target: SpatialRecord, **kwargs: Any) -> str:
        received_contexts.append(kwargs.get("search_context"))
        return "Arrived near landmark."

    monkeypatch.setattr(nav, "_navigate_to_landmark", fake_navigation)

    result = nav.navigate_with_text("垃圾桶")

    assert result == "Arrived near landmark."
    assert received_contexts == [None]
    assert nav._active_object_search is None


def test_navigate_with_text_enables_enroute_search_only_when_opted_in(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DIMOS_ENROUTE_OBJECT_SEARCH_ENABLED", "true")
    monkeypatch.setenv("DIMOS_NAV_FALLBACK", "object_room")
    nav = _nav_container()
    target = SpatialRecord(
        name="垃圾桶",
        record_type=RecordType.LANDMARK,
        position=(2.0, 0.0, 0.0),
    )
    received_contexts: list[_ObjectSearchContext | None] = []
    monkeypatch.setattr(nav, "_resolve_landmark_from_query", lambda _query: target)

    def fake_navigation(_target: SpatialRecord, **kwargs: Any) -> str:
        received_contexts.append(kwargs.get("search_context"))
        return "Arrived near landmark."

    monkeypatch.setattr(nav, "_navigate_to_landmark", fake_navigation)

    result = nav.navigate_with_text("垃圾桶")

    assert result == "Arrived near landmark."
    assert len(received_contexts) == 1
    assert isinstance(received_contexts[0], _ObjectSearchContext)
    assert received_contexts[0].cancel_event.is_set()
    assert nav._active_object_search is None


def test_scan_room_rotation_uses_list_recognition_not_query(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DIMOS_ROTATION_STEP_DEG", "180")
    nav = _nav_container()
    fake = _FakeUnitree()
    nav._unitree_skill_container = fake
    nav._latest_odom = PoseStamped(
        position=make_vector3(0.0, 0.0, 0.0),
        orientation=Quaternion.from_euler(Vector3(0.0, 0.0, 0.0)),
    )
    calls: list[str] = []

    def fake_query(_name: str) -> _VisualLockResult | None:
        calls.append("query")
        return None

    def fake_list(_name: str) -> _VisualLockResult | None:
        calls.append("list")
        return _confirmed_visual_lock("垃圾桶")

    nav._detect_and_servo = fake_query  # type: ignore[method-assign]
    nav._detect_and_servo_by_list_recognition = fake_list  # type: ignore[method-assign]

    result = nav._scan_room_for_object("垃圾桶")
    assert result is not None
    assert result.confirmed
    assert result.message == "Visually confirmed '垃圾桶' in a fresh centered frame"
    assert calls == ["query", "list"]
    assert len(fake.rotations) == 1


def test_pick_object_from_recognition_picks_largest_bbox() -> None:
    nav = _nav_container()
    items = [
        {"name": "垃圾桶", "bbox": [100, 100, 200, 200]},
        {"name": "垃圾桶", "bbox": [880, 646, 1000, 1000]},
    ]
    picked = nav._pick_object_from_recognition(items, "垃圾桶")
    assert picked is not None
    assert picked["bbox"] == [880, 646, 1000, 1000]


def test_list_recognition_skips_when_target_not_in_results() -> None:
    nav = _nav_container()
    nav._latest_image = Image.from_numpy(
        __import__("numpy").zeros((64, 64, 3), dtype=__import__("numpy").uint8)
    )
    nav._recognize_objects_simple = lambda _img: [  # type: ignore[method-assign]
        {"name": "门", "bbox": [10, 10, 100, 100]},
        {"name": "椅子", "bbox": [200, 200, 300, 300]},
    ]
    assert nav._detect_and_servo_by_list_recognition("垃圾桶") is None


def test_list_recognition_candidate_uses_common_visual_confirmation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    nav = _nav_container()
    nav._on_color_image(Image.from_numpy(np.zeros((60, 100, 3), dtype=np.uint8), ts=10.0))
    nav._recognize_objects_simple = lambda _image: [  # type: ignore[method-assign]
        {"name": "垃圾桶", "bbox": [350, 100, 650, 900]}
    ]
    nav._landmark_memory = SimpleNamespace(
        find_by_name=lambda _name: None,
        save_snapshot=lambda _stem, _data: "",
    )
    confirmed_queries: list[str] = []

    def confirm(target_name: str, **_kwargs: Any) -> _VisualLockResult:
        confirmed_queries.append(target_name)
        return _confirmed_visual_lock(target_name)

    monkeypatch.setattr(nav, "_confirm_visual_lock", confirm)

    result = nav._detect_and_servo_by_list_recognition("垃圾桶")

    assert result is not None
    assert result.confirmed
    assert confirmed_queries == ["垃圾桶"]


def test_room_sweep_does_not_greet_unconfirmed_candidate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    nav = _nav_container()
    nav._navigation = _FakeNavigation()
    nav._unitree_skill_container = _FakeUnitree()
    monkeypatch.setattr(
        nav,
        "_room_anchor_sweep_for_object",
        lambda _query, **_kwargs: _VisualLockResult(
            status=_VisualLockStatus.UNCONFIRMED,
            message="not confirmed",
            reason="vlm_miss_or_bad_bbox",
        ),
    )

    result = nav._nav_fallback_step_room_sweep("灭火器")

    assert result is None
    assert nav._unitree_skill_container.commands == []


def test_room_anchor_sweep_scans_rooms_until_object_found(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(time, "sleep", lambda _seconds: None)
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

    def fake_detect(query: str) -> _VisualLockResult | None:
        if visited[-1] == "lab":
            return _confirmed_visual_lock(query)
        return None

    nav._navigate_to_landmark = fake_landmark_nav
    nav._detect_and_servo = fake_detect

    result = nav._room_anchor_sweep_for_object("toolbox")
    assert result is not None
    assert result.confirmed
    assert result.message == "Visually confirmed 'toolbox' in a fresh centered frame"
    assert visited == ["office", "lab"]
    assert nav._unitree_skill_container.rotations == [70.0, 70.0, 70.0, 70.0, 70.0, 70.0]


def test_tracking_arrival_requires_live_visual_confirmation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    nav = _nav_container()
    nav._navigation = _FakeNavigation()
    nav._latest_image = Image.from_numpy(np.zeros((60, 100, 3), dtype=np.uint8), ts=10.0)
    stops: list[bool] = []
    nav._object_tracking = SimpleNamespace(
        track=lambda _bbox: {"status": "tracking_started"},
        is_tracking=lambda: True,
        stop_track=lambda: stops.append(True),
    )
    monkeypatch.setattr(
        nav,
        "_get_bbox_for_current_frame",
        lambda _query, **_kwargs: (35.0, 10.0, 65.0, 50.0),
    )
    monkeypatch.setattr(
        nav,
        "_confirm_visual_lock",
        lambda _query, **_kwargs: _VisualLockResult(
            status=_VisualLockStatus.UNCONFIRMED,
            message="fresh frame miss",
            reason="vlm_miss_or_bad_bbox",
        ),
    )
    monkeypatch.setattr("dimos.agents.skills.navigation.time.sleep", lambda _seconds: None)

    result = nav._navigate_to_object("灭火器", timeout=1.0)

    assert result is None
    assert stops == [True]


def test_vlm_memory_candidate_is_not_hit_without_live_confirmation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    nav = _nav_container()
    nav._navigation = _FakeNavigation()
    nav._spatial_memory = SimpleNamespace(
        query_by_text_with_images=lambda _query, limit=3: [
            {
                "image": np.zeros((60, 100, 3), dtype=np.uint8),
                "metadata": {"pos_x": 1.0, "pos_y": 2.0},
            }
        ],
        get_room_images=lambda: [],
    )
    nav._vlm_query_all_images = lambda _images, _prompt: "[0]"  # type: ignore[method-assign]
    nav._resolve_landmark_from_query = lambda _query: SpatialRecord(  # type: ignore[method-assign]
        name="灭火器",
        record_type=RecordType.LANDMARK,
        rotation=(0.0, 0.0, 0.5),
    )
    nav._visual_acquire_object = lambda _query, _yaw=None: None  # type: ignore[method-assign]
    monkeypatch.setattr("dimos.agents.skills.navigation.time.sleep", lambda _seconds: None)

    result = nav._query_memory_images_with_vlm("灭火器")

    assert result is None
    assert len(nav._navigation.goals) == 1


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


def test_object_landmark_accepts_safe_standoff_without_churn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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
    monkeypatch.setattr(nav, "_visual_acquire_object", lambda _name, _yaw=None: None)

    result = nav._navigate_to_landmark(
        target,
        arrival_action="stop",
        arrival_distance=0.5,
        run_arrival_action=False,
    )

    assert "Arrived near" in result
    assert nav._navigation.goals == []


def test_object_landmark_does_not_run_arrival_action_without_visual_confirmation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    nav = _nav_container()
    target = SpatialRecord(
        name="灭火器",
        record_type=RecordType.LANDMARK,
        position=(0.0, 0.0, 0.0),
    )
    nav._navigation = _FakeNavigation()
    nav._unitree_skill_container = _FakeUnitree()
    nav._latest_odom = _pose(0.8, 0.0)
    nav._landmark_memory = SimpleNamespace(get_all=lambda: [target])
    nav._relocalize_interval_s = 30.0
    nav._coordinate_frame_stale_reason = lambda _target: None
    monkeypatch.setattr(
        nav,
        "_visual_acquire_object",
        lambda _name, _yaw=None: _VisualLockResult(
            status=_VisualLockStatus.UNCONFIRMED,
            message="fresh frame miss",
            reason="vlm_miss_or_bad_bbox",
        ),
    )

    result = nav._navigate_to_landmark(
        target,
        arrival_action="point",
        arrival_distance=0.5,
        run_arrival_action=True,
    )

    assert "could not visually acquire" in result
    assert "arrival_action not run" in result
    assert nav._unitree_skill_container.commands == []


def test_object_landmark_runs_arrival_action_once_after_visual_confirmation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    nav = _nav_container()
    target = SpatialRecord(
        name="灭火器",
        record_type=RecordType.LANDMARK,
        position=(0.0, 0.0, 0.0),
    )
    nav._navigation = _FakeNavigation()
    nav._unitree_skill_container = _FakeUnitree()
    nav._latest_odom = _pose(0.8, 0.0)
    nav._landmark_memory = SimpleNamespace(get_all=lambda: [target])
    nav._relocalize_interval_s = 30.0
    nav._coordinate_frame_stale_reason = lambda _target: None
    monkeypatch.setattr(
        nav,
        "_visual_acquire_object",
        lambda _name, _yaw=None: _confirmed_visual_lock("灭火器"),
    )
    monkeypatch.setattr("dimos.agents.skills.navigation.time.sleep", lambda _seconds: None)

    result = nav._navigate_to_landmark(
        target,
        arrival_action="point",
        arrival_distance=0.5,
        run_arrival_action=True,
    )

    assert "Visually confirmed '灭火器'" in result
    assert nav._unitree_skill_container.commands == ["Hello", "RecoveryStand"]


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
            MockedSemanticNavSkill.blueprint(),
            *_STUB_BLUEPRINTS,
        ],
        messages=[HumanMessage("Go to the bookshelf. Use the navigate_with_text tool.")],
    )

    assert "success" in history[-1].content.lower()
