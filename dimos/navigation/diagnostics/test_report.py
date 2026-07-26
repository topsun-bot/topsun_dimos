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
from typing import Any

import numpy as np

from dimos.navigation.diagnostics.report import (
    EXPECTED_PRODUCERS,
    _classify_avoidance,
    _collect_drops,
    _sample_path_costmap,
    analyze_run,
    build_session_summary,
    load_trace,
    reconstruct_sessions,
)
from dimos.navigation.diagnostics.rerun_export import _costmap_points

SESSION_ID = "nav-0001-20260724T120000.000"
BASE_NS = 10_000_000_000


def _event(name: str, producer: str, offset: int, **fields: Any) -> dict[str, Any]:
    return {
        "schema_version": "1.0",
        "event": name,
        "producer": producer,
        "monotonic_ns": BASE_NS + offset,
        "wall_ts": "2026-07-24T04:00:00.000+00:00",
        **fields,
    }


def _write_jsonl(path: Path, events: list[dict[str, Any]]) -> None:
    path.write_text(
        "".join(json.dumps(event) + "\n" for event in events),
        encoding="utf-8",
    )


def _make_run(tmp_path: Path, *, close_session: bool = True) -> Path:
    navigation = tmp_path / "navigation"
    plans = navigation / "plans"
    blobs = navigation / "blobs"
    plans.mkdir(parents=True)
    blobs.mkdir()
    (navigation / "manifest.json").write_text(
        json.dumps({"run_id": "run-test", "map_inputs": []}),
        encoding="utf-8",
    )
    path_payload = {
        "poses": [
            {"position": {"x": 0.0, "y": 0.0}},
            {"position": {"x": 1.0, "y": 0.0}},
            {"position": {"x": 2.0, "y": 0.0}},
        ]
    }
    for kind in ("raw", "smoothed"):
        (plans / f"{SESSION_ID}-plan-0001-{kind}.json").write_text(
            json.dumps(path_payload),
            encoding="utf-8",
        )
    np.save(blobs / "costmap-000001.npy", np.zeros((4, 4), dtype=np.uint8))
    np.save(
        blobs / "pointcloud-roi-000001.npy",
        np.array([[0.2, 0.1, 0.0]], dtype=np.float32),
    )

    planner_events = [
        _event(
            "trace_header",
            "planner",
            0,
            effective_trace_level="full",
        ),
        _event(
            "navigation_session_started",
            "planner",
            100,
            navigation_session_id=SESSION_ID,
            entry_source="rpc_set_goal",
        ),
        _event(
            "plan_started",
            "planner",
            200,
            navigation_session_id=SESSION_ID,
            plan_version=1,
            plan_reason="external_goal",
        ),
        _event(
            "planner_odom",
            "planner",
            300,
            navigation_session_id=SESSION_ID,
            plan_version=1,
            pose={"x": 0.0, "y": 0.0, "yaw": 0.0},
        ),
        _event(
            "planner_control_published",
            "planner",
            400,
            navigation_session_id=SESSION_ID,
            plan_version=1,
            command_generated_monotonic_ns=BASE_NS + 400,
            twist={"angular_z": 0.1},
        ),
        _event(
            "planner_odom",
            "planner",
            1_000_000_300,
            navigation_session_id=SESSION_ID,
            plan_version=1,
            pose={"x": 1.0, "y": 0.1, "yaw": 0.05},
        ),
        _event(
            "planner_control_published",
            "planner",
            1_000_000_400,
            navigation_session_id=SESSION_ID,
            plan_version=1,
            command_generated_monotonic_ns=BASE_NS + 1_000_000_400,
            twist={"angular_z": -0.1},
        ),
        _event(
            "blob_saved",
            "planner",
            1_100_000_000,
            navigation_session_id=SESSION_ID,
            plan_version=1,
            blob_kind="costmap",
            blob_path="blobs/costmap-000001.npy",
            metadata={"snapshot_kind": "binary_costmap"},
        ),
        _event(
            "blob_saved",
            "planner",
            1_200_000_000,
            navigation_session_id=SESSION_ID,
            plan_version=1,
            blob_kind="pointcloud",
            blob_path="blobs/pointcloud-roi-000001.npy",
            metadata={"source_kind": "raw_lidar", "frame_id": "world"},
        ),
    ]
    if close_session:
        planner_events.append(
            _event(
                "navigation_session_ended",
                "planner",
                2_000_000_000,
                navigation_session_id=SESSION_ID,
                terminal="arrived",
                reason="goal_tolerance_reached",
            )
        )
    planner_events.append(
        _event(
            "trace_footer",
            "planner",
            3_000_000_000,
            effective_trace_level="full",
        )
    )
    _write_jsonl(navigation / "planner-100.jsonl", planner_events)

    for producer in EXPECTED_PRODUCERS[1:]:
        events = [
            _event("trace_header", producer, 0, effective_trace_level="full"),
        ]
        if producer == "mux":
            events.append(
                _event(
                    "mux_command_published",
                    producer,
                    500,
                    source="navigation",
                    command_muxed_monotonic_ns=BASE_NS + 500,
                    twist={"angular_z": 0.1},
                )
            )
        if producer == "connection":
            events.extend(
                [
                    _event(
                        "webrtc_command_send",
                        producer,
                        600,
                        command_send_monotonic_ns=BASE_NS + 600,
                        twist={"angular_z": 0.1},
                        send_duration_ns=500_000,
                        send_accepted=True,
                        robot_execution_ack=False,
                    ),
                    _event(
                        "webrtc_loop_heartbeat",
                        producer,
                        700,
                        delay_ns=500_000,
                        interval_sec=0.1,
                    ),
                    _event(
                        "webrtc_loop_heartbeat",
                        producer,
                        1_000_000_700,
                        delay_ns=1_500_000,
                        interval_sec=0.1,
                    ),
                ]
            )
        events.append(_event("trace_footer", producer, 3_000_000_000, effective_trace_level="full"))
        _write_jsonl(navigation / f"{producer}-100.jsonl", events)
    return tmp_path


def test_reconstructs_open_session_as_process_exit(tmp_path: Path) -> None:
    trace = load_trace(_make_run(tmp_path, close_session=False))

    sessions = reconstruct_sessions(trace)

    assert len(sessions) == 1
    assert sessions[0].terminal == "aborted_process_exit"
    assert sessions[0].recovered_after_process_exit is True


def test_summary_preserves_evidence_limits_and_plan_metrics(tmp_path: Path) -> None:
    trace = load_trace(_make_run(tmp_path))
    session = reconstruct_sessions(trace)[0]

    summary, plans = build_session_summary(trace, session)

    assert summary["navigation_result"]["terminal"] == "arrived"
    assert summary["trajectory"]["odom_is_external_ground_truth"] is False
    assert summary["trajectory"]["initial_planned_path_length_m"] == 2.0
    assert summary["trajectory"]["latest_planned_path_length_m"] == 2.0
    assert summary["trajectory"]["planned_path_lengths_are_not_summed_across_replans"]
    assert summary["control_chain"]["send_is_robot_execution_ack"] is False
    heartbeat = summary["control_chain"]["event_loop_heartbeat"]
    assert heartbeat["sample_count"] == 2
    assert heartbeat["delay_ms"]["p99"] == 1.49
    assert summary["control_chain"]["cross_worker_matching"]["matched_end_to_end"] == 1
    assert summary["avoidance"]["classification"] == "INSUFFICIENT_EVIDENCE"
    assert summary["integrity"]["missing_producers"] == []
    assert plans[0].metrics is not None
    assert plans[0].metrics.cross_track_max_m == 0.1


def test_analyze_run_generates_all_offline_artifacts(tmp_path: Path) -> None:
    run_dir = _make_run(tmp_path)

    report_dir = analyze_run(run_dir, session_id=SESSION_ID)[0]

    assert (report_dir / "summary.json").is_file()
    assert (report_dir / "report.md").is_file()
    assert (report_dir / "trace.rrd").is_file()
    assert {path.name for path in (report_dir / "plots").glob("*.png")} == {
        "planned-vs-actual.png",
        "cross-track-error.png",
        "heading-and-command.png",
        "latency-and-jitter.png",
        "costmap-overlay.png",
        "obstacle-timeline.png",
    }
    report = (report_dir / "report.md").read_text(encoding="utf-8")
    assert "not external ground truth" in report
    assert "not proof that the robot executed" in report


def test_rerun_costmap_uses_nested_planner_snapshot_geometry() -> None:
    grid = np.array([[100, 0], [0, 50]], dtype=np.int8)
    metadata = {
        "snapshot_kind": "astar_navigation_costmap",
        "costmap": {
            "resolution": 0.5,
            "origin": {
                "position": {"x": 10.0, "y": 20.0},
                "yaw": 0.0,
            },
        },
    }

    points = _costmap_points(grid, metadata)

    assert points["occupied"].tolist() == [[10.25, 20.25]]
    assert points["inflated"].tolist() == [[10.75, 20.75]]


def test_path_costmap_analysis_classifies_raw_and_smoothed_collisions() -> None:
    metadata = {
        "costmap": {
            "resolution": 1.0,
            "origin": {
                "position": {"x": 10.0, "y": 20.0},
                "yaw": 0.0,
            },
        }
    }
    grid = np.zeros((3, 3), dtype=np.int8)
    grid[0, 1] = 100
    safe = _sample_path_costmap(
        grid,
        metadata,
        np.array([[10.1, 21.1], [11.1, 21.1]], dtype=np.float64),
    )
    colliding = _sample_path_costmap(
        grid,
        metadata,
        np.array([[10.1, 20.1], [11.1, 20.1]], dtype=np.float64),
    )

    raw_result = _classify_avoidance(
        events=[],
        obstacle_events=[],
        blob_kinds={"costmap"},
        send_events=[],
        path_costmap=[{"raw_path": colliding, "smoothed_path": colliding}],
    )
    smoothed_result = _classify_avoidance(
        events=[],
        obstacle_events=[],
        blob_kinds={"costmap"},
        send_events=[],
        path_costmap=[{"raw_path": safe, "smoothed_path": colliding}],
    )

    assert raw_result[0] == "RAW_PATH_COLLISION"
    assert smoothed_result[0] == "SMOOTHING_COLLISION"


def test_path_costmap_analysis_applies_origin_rotation_and_unknown_cells() -> None:
    grid = np.zeros((2, 2), dtype=np.int8)
    grid[0, 1] = -1
    metadata = {
        "resolution": 1.0,
        "origin": {"x": 3.0, "y": 4.0, "yaw": np.pi / 2},
    }
    # Local grid cell (column=1,row=0) rotates to world x=2.5,y=5.5.
    result = _sample_path_costmap(
        grid,
        metadata,
        np.array([[2.5, 5.5]], dtype=np.float64),
    )
    classification = _classify_avoidance(
        events=[],
        obstacle_events=[],
        blob_kinds={"costmap"},
        send_events=[],
        path_costmap=[{"raw_path": result, "smoothed_path": result}],
    )

    assert result["unknown_cells"] == 1
    assert classification[0] == "UNKNOWN_SPACE_TRAVERSED"


def test_avoidance_classification_detects_stop_propagation_and_transport_delay() -> None:
    obstacle = _event("path_clearance_obstacle", "planner", 100_000_000)
    stop = _event("planner_stop_command_published", "planner", 90_000_000)
    continued = _event(
        "mux_command_published",
        "mux",
        200_000_000,
        source="navigation",
        twist={"linear_x": 0.2},
    )
    stop_result = _classify_avoidance(
        events=[stop, obstacle, continued],
        obstacle_events=[obstacle],
        blob_kinds={"pointcloud", "costmap"},
        send_events=[],
        path_costmap=[],
    )
    delayed_send = _event(
        "webrtc_command_send",
        "connection",
        300_000_000,
        send_duration_ns=300_000_000,
    )
    delay_result = _classify_avoidance(
        events=[stop, obstacle, delayed_send],
        obstacle_events=[obstacle],
        blob_kinds={"pointcloud", "costmap"},
        send_events=[delayed_send],
        path_costmap=[],
    )

    assert stop_result[0] == "STOP_NOT_PROPAGATED"
    assert delay_result[0] == "CONTROL_TRANSPORT_DELAY"


def test_drop_aggregation_preserves_missing_time_windows() -> None:
    event = _event(
        "trace_drop_summary",
        "planner",
        100,
        drops={
            "scalar_queue_full": {
                "count": 3,
                "first_wall_ts": "2026-07-24T04:00:01Z",
                "last_wall_ts": "2026-07-24T04:00:02Z",
            }
        },
        _trace_file="planner-100.jsonl",
    )

    drops = _collect_drops([event])

    reason = drops["planner"]["scalar_queue_full"]
    assert reason["count"] == 3
    assert reason["windows"] == [
        {
            "first_wall_ts": "2026-07-24T04:00:01Z",
            "last_wall_ts": "2026-07-24T04:00:02Z",
            "trace_file": "planner-100.jsonl",
        }
    ]


def test_avoidance_classification_distinguishes_missing_and_stale_lidar() -> None:
    header = _event(
        "trace_header",
        "connection",
        0,
        effective_trace_level="full",
    )
    obstacle = _event(
        "path_clearance_obstacle",
        "planner",
        5_000_000_000,
    )
    stale_lidar = _event(
        "connection_raw_lidar",
        "connection",
        1_000_000_000,
    )

    missing = _classify_avoidance(
        events=[header, obstacle],
        obstacle_events=[obstacle],
        blob_kinds={"costmap"},
        send_events=[],
        path_costmap=[],
    )
    stale = _classify_avoidance(
        events=[header, stale_lidar, obstacle],
        obstacle_events=[obstacle],
        blob_kinds={"costmap", "pointcloud"},
        send_events=[],
        path_costmap=[],
    )

    assert missing[0] == "SENSOR_MISSING"
    assert stale[0] == "POINTCLOUD_STALE"


def test_avoidance_classification_accepts_layer_provenance() -> None:
    obstacle = _event(
        "path_clearance_obstacle",
        "planner",
        100,
        tf_misaligned=True,
        evidence_level="CORRELATED",
        root_cause_reason="frames disagree",
    )
    result = _classify_avoidance(
        events=[obstacle],
        obstacle_events=[obstacle],
        blob_kinds={"pointcloud", "costmap"},
        send_events=[],
        path_costmap=[],
    )
    assert result == ("TF_MISALIGNED", "CORRELATED", "frames disagree")


def test_avoidance_classification_reports_missing_costmap_as_sensor_failure() -> None:
    obstacle = _event(
        "path_clearance_obstacle",
        "planner",
        100,
        decision={"reason": "costmap_missing"},
    )
    result = _classify_avoidance(
        events=[obstacle],
        obstacle_events=[obstacle],
        blob_kinds=set(),
        send_events=[],
        path_costmap=[],
    )
    assert result[0] == "SENSOR_MISSING"


def test_avoidance_classification_detects_path_costmap_frame_mismatch() -> None:
    result = _classify_avoidance(
        events=[],
        obstacle_events=[_event("path_clearance_obstacle", "planner", 100)],
        blob_kinds={"costmap"},
        send_events=[],
        path_costmap=[{"frame_consistent": False}],
    )
    assert result == (
        "TF_MISALIGNED",
        "CORRELATED",
        "planned path and its A* costmap use different frame_id values",
    )
