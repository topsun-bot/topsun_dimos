# Copyright 2025-2026 Dimensional Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");

import json

import pytest

from dimos.msgs.geometry_msgs import PoseStamped, Quaternion
from dimos.msgs.geometry_msgs.Vector3 import make_vector3
from dimos.navigation.patrol.patrol_vis import PatrolVisModule


def _make_pose(x: float, y: float) -> PoseStamped:
    return PoseStamped(
        position=make_vector3(x, y, 0),
        orientation=Quaternion(0.0, 0.0, 0.0, 1.0),
        frame_id="map",
    )


class TestPatrolVisModule:
    def test_instantiation(self):
        mod = PatrolVisModule()
        assert mod._vis_port == 7779

    def test_custom_port(self):
        mod = PatrolVisModule(vis_port=8080)
        assert mod._vis_port == 8080

    def test_emit_without_connection(self):
        mod = PatrolVisModule()
        mod._sio = None
        mod._emit("test_event", {"key": "value"})
        # Should not raise

    def test_on_pose_rate_limiting(self):
        mod = PatrolVisModule()
        mod._sio = None
        mod._last_pose_emit = 0.0

        mod._on_pose(_make_pose(1, 2))
        assert mod._last_pose_emit > 0

        first_emit = mod._last_pose_emit
        mod._on_pose(_make_pose(3, 4))
        # Should be rate-limited (same timestamp)
        assert mod._last_pose_emit == first_emit

    def test_on_status_parses_json(self):
        mod = PatrolVisModule()
        mod._sio = None
        status = json.dumps({
            "state": "navigating",
            "route_name": "test",
            "visited_waypoints": ["a", "b"],
            "total_waypoints": 5,
        })
        mod._on_status(status)
        assert mod._visited_count == 2
        assert mod._total_waypoints == 5

    def test_on_status_invalid_json(self):
        mod = PatrolVisModule()
        mod._sio = None
        mod._on_status("not json")
        # Should not raise

    def test_on_event_accumulates(self):
        mod = PatrolVisModule()
        mod._sio = None
        event = json.dumps({"type": "waypoint_arrived", "waypoint": "test"})
        mod._on_event(event)
        mod._on_event(event)
        assert len(mod._event_log) == 2

    def test_on_event_log_max_size(self):
        mod = PatrolVisModule()
        mod._sio = None
        for i in range(60):
            event = json.dumps({"type": "test", "i": i})
            mod._on_event(event)
        assert len(mod._event_log) == 50  # maxlen=50

    def test_set_route_visualization(self):
        mod = PatrolVisModule()
        mod._sio = None
        route_data = {"waypoints": [{"name": "a", "x": 1, "y": 2}]}
        assert mod.set_route_visualization(route_data) is True
        assert mod._current_route_data == route_data

    def test_show_patrol_status_no_data(self):
        mod = PatrolVisModule()
        result = mod.show_patrol_status()
        assert "no active patrol" in result

    def test_show_patrol_status_with_data(self):
        mod = PatrolVisModule()
        mod._current_route_data = {"route_name": "test_route"}
        mod._visited_count = 3
        mod._total_waypoints = 6
        mod._event_log.append({"type": "test"})
        result = mod.show_patrol_status()
        assert "test_route" in result
        assert "3/6" in result
