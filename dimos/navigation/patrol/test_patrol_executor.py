# Copyright 2025-2026 Dimensional Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");

import json
import sys
import time

import pytest

from dimos.msgs.geometry_msgs import PoseStamped, Quaternion
from dimos.msgs.geometry_msgs.Vector3 import make_vector3
from dimos.navigation.patrol.patrol_executor import PatrolExecutor
from dimos.navigation.patrol.types import PatrolRoute, PatrolState, PatrolStateEnum, Waypoint


def _make_pose(x: float, y: float) -> PoseStamped:
    return PoseStamped(
        position=make_vector3(x, y, 0),
        orientation=Quaternion(0.0, 0.0, 0.0, 1.0),
        frame_id="map",
    )


def _make_waypoint(name: str, x: float, y: float, loiter_time: float = 0.1) -> Waypoint:
    return Waypoint(
        name=name,
        pose=_make_pose(x, y),
        loiter_time=loiter_time,
    )


class TestPatrolTypes:
    def test_patrol_state_enum(self):
        assert PatrolStateEnum.IDLE.value == "idle"
        assert PatrolStateEnum.NAVIGATING.value == "navigating"
        assert PatrolStateEnum.COMPLETED.value == "completed"

    def test_waypoint_creation(self):
        wp = _make_waypoint("test", 1.0, 2.0)
        assert wp.name == "test"
        assert wp.pose.position.x == 1.0
        assert wp.loiter_time == 0.1

    def test_patrol_route(self):
        route = PatrolRoute(name="test_route")
        route.waypoints.append(_make_waypoint("a", 0, 0))
        route.waypoints.append(_make_waypoint("b", 1, 1))
        assert len(route.waypoints) == 2
        assert route.loop is False

    def test_patrol_state(self):
        state = PatrolState()
        assert state.state == PatrolStateEnum.IDLE
        assert state.route is None
        assert state.visited_waypoints == []


class TestPatrolExecutor:
    def test_instantiation(self):
        ex = PatrolExecutor()
        assert ex._waypoint_timeout == 60.0

    def test_create_route(self):
        ex = PatrolExecutor()
        assert ex.create_route("test") is True
        assert ex.create_route("test") is False  # duplicate

    def test_add_waypoint(self):
        ex = PatrolExecutor()
        ex.create_route("test")
        wp = _make_waypoint("wp1", 1.0, 2.0)
        assert ex.add_waypoint("test", wp) is True
        assert ex.add_waypoint("nonexistent", wp) is False

    def test_remove_waypoint(self):
        ex = PatrolExecutor()
        ex.create_route("test")
        ex.add_waypoint("test", _make_waypoint("wp1", 1, 2))
        ex.add_waypoint("test", _make_waypoint("wp2", 3, 4))
        assert ex.remove_waypoint("test", "wp1") is True
        routes = ex.list_routes()
        assert len(routes[0].waypoints) == 1
        assert routes[0].waypoints[0].name == "wp2"

    def test_list_routes(self):
        ex = PatrolExecutor()
        ex.create_route("route_a")
        ex.create_route("route_b")
        routes = ex.list_routes()
        assert len(routes) == 2

    def test_get_patrol_state_idle(self):
        ex = PatrolExecutor()
        state = ex.get_patrol_state()
        assert state.state == PatrolStateEnum.IDLE

    def test_save_and_load_route(self, tmp_path):
        ex = PatrolExecutor()
        ex.create_route("save_test")
        ex.add_waypoint("save_test", _make_waypoint("wp1", 1.0, 2.0))
        ex.add_waypoint("save_test", _make_waypoint("wp2", 3.0, 4.0))

        import dimos.navigation.patrol.patrol_executor as _pe_mod
        pe = sys.modules.get("dimos.navigation.patrol.patrol_executor", _pe_mod)

        original_dir = pe._ROUTES_DIR
        pe._ROUTES_DIR = tmp_path
        try:
            assert ex.save_route("save_test") is True
            assert ex.load_route("save_test") is True
            routes = ex.list_routes()
            assert len(routes[0].waypoints) == 2
        finally:
            pe._ROUTES_DIR = original_dir

    def test_load_nonexistent_route(self):
        ex = PatrolExecutor()
        assert ex.load_route("nonexistent") is False


class TestPatrolExecutorSkills:
    def test_create_patrol_route_skill(self):
        ex = PatrolExecutor()
        result = ex.create_patrol_route("nightly")
        assert "Created empty patrol route 'nightly'" in result

    def test_create_duplicate_route_skill(self):
        ex = PatrolExecutor()
        ex.create_patrol_route("nightly")
        result = ex.create_patrol_route("nightly")
        assert "already exists" in result

    def test_add_patrol_waypoint_skill(self):
        ex = PatrolExecutor()
        ex.create_patrol_route("test")
        result = ex.add_patrol_waypoint("test", "entrance", 3.5, 1.2)
        assert "Added waypoint 'entrance'" in result
        assert "3.5" in result

    def test_add_waypoint_to_nonexistent_route(self):
        ex = PatrolExecutor()
        result = ex.add_patrol_waypoint("nope", "wp", 1, 2)
        assert "not found" in result

    def test_add_current_position_as_waypoint_skill(self):
        ex = PatrolExecutor()
        ex.create_patrol_route("test")
        ex._current_pose = _make_pose(0.5, 1.5)
        result = ex.add_current_position_as_waypoint("test", "here")
        assert "Added waypoint 'here'" in result
        assert "0.5" in result

    def test_add_current_position_no_pose(self):
        ex = PatrolExecutor()
        ex.create_patrol_route("test")
        result = ex.add_current_position_as_waypoint("test", "here")
        assert "No position data" in result

    def test_list_patrol_routes_skill(self):
        ex = PatrolExecutor()
        result = ex.list_patrol_routes()
        assert "No patrol routes" in result

        ex.create_patrol_route("route_a")
        ex.add_patrol_waypoint("route_a", "wp1", 1, 2)
        result = ex.list_patrol_routes()
        assert "route_a" in result
        assert "1 waypoints" in result

    def test_get_patrol_status_idle(self):
        ex = PatrolExecutor()
        result = ex.get_patrol_status()
        assert "No patrol is currently active" in result

    def test_stop_patrol_when_idle(self):
        ex = PatrolExecutor()
        result = ex.stop_patrol()
        assert "No patrol is currently running" in result

    def test_save_patrol_route_skill(self, tmp_path):
        ex = PatrolExecutor()
        result = ex.save_patrol_route("nonexistent")
        assert "not found" in result

        pe = sys.modules["dimos.navigation.patrol.patrol_executor"]
        original_dir = pe._ROUTES_DIR
        pe._ROUTES_DIR = tmp_path
        try:
            ex.create_patrol_route("test")
            ex.add_patrol_waypoint("test", "wp1", 1, 2)
            result = ex.save_patrol_route("test")
            assert "Saved route 'test'" in result
        finally:
            pe._ROUTES_DIR = original_dir
