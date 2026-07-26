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

"""Offline reconstruction and reporting for navigation traces."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
from itertools import pairwise
import json
from pathlib import Path
from typing import Any, cast

import numpy as np
from numpy.typing import NDArray

from dimos.navigation.diagnostics.metrics import (
    ResponseLagResult,
    TrajectoryMetrics,
    calculate_trajectory_metrics,
    estimate_closed_loop_response_lag,
    project_to_polyline,
)

EXPECTED_PRODUCERS = (
    "planner",
    "mux",
    "connection",
    "costmapper",
    "relocalization",
)
ROOT_CAUSE_CLASSES = (
    "SENSOR_MISSING",
    "POINTCLOUD_STALE",
    "TF_MISALIGNED",
    "MERGE_MISSING_OBSTACLE",
    "COSTMAP_CLASSIFICATION_MISS",
    "INFLATION_INSUFFICIENT",
    "UNKNOWN_SPACE_TRAVERSED",
    "RAW_PATH_COLLISION",
    "SMOOTHING_COLLISION",
    "PATH_CLEARANCE_MISS",
    "STOP_NOT_PROPAGATED",
    "CONTROL_TRANSPORT_DELAY",
    "ROBOT_EXECUTION_MISMATCH",
    "INSUFFICIENT_EVIDENCE",
)


@dataclass(frozen=True, slots=True)
class ParsedTrace:
    run_dir: Path
    navigation_dir: Path
    manifest: dict[str, Any]
    events: tuple[dict[str, Any], ...]
    files: tuple[Path, ...]
    parse_errors: tuple[dict[str, Any], ...]


@dataclass(frozen=True, slots=True)
class SessionWindow:
    session_id: str
    start_monotonic_ns: int
    end_monotonic_ns: int
    start_wall_ts: str | None
    end_wall_ts: str | None
    entry_source: str
    terminal: str
    terminal_reason: str
    recovered_after_process_exit: bool


@dataclass(frozen=True, slots=True)
class PlanAnalysis:
    plan_version: int
    plan_reason: str
    raw_path: NDArray[np.float64] | None
    smoothed_path: NDArray[np.float64] | None
    odom_xy: NDArray[np.float64]
    odom_ts: NDArray[np.float64]
    odom_yaw: NDArray[np.float64]
    command_ts: NDArray[np.float64]
    command_angular_z: NDArray[np.float64]
    metrics: TrajectoryMetrics | None


def load_trace(run_dir: Path) -> ParsedTrace:
    """Load producer JSONL files while preserving evidence about corrupt tails."""
    resolved = run_dir.expanduser().resolve()
    navigation_dir = resolved / "navigation"
    if not navigation_dir.is_dir():
        raise ValueError(f"navigation trace directory not found: {navigation_dir}")

    manifest_path = navigation_dir / "manifest.json"
    manifest: dict[str, Any] = {}
    if manifest_path.is_file():
        try:
            loaded = json.loads(manifest_path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                manifest = loaded
        except (OSError, json.JSONDecodeError):
            manifest = {}

    events: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    files = tuple(sorted(navigation_dir.glob("*.jsonl")))
    for path in files:
        try:
            with path.open(encoding="utf-8") as stream:
                for line_number, line in enumerate(stream, start=1):
                    if not line.strip():
                        continue
                    try:
                        event = json.loads(line)
                    except json.JSONDecodeError as exc:
                        errors.append(
                            {
                                "file": path.name,
                                "line": line_number,
                                "error": f"{type(exc).__name__}: {exc}",
                            }
                        )
                        continue
                    if isinstance(event, dict):
                        event["_trace_file"] = path.name
                        events.append(event)
                    else:
                        errors.append(
                            {
                                "file": path.name,
                                "line": line_number,
                                "error": "JSON value is not an object",
                            }
                        )
        except OSError as exc:
            errors.append({"file": path.name, "line": None, "error": str(exc)})

    events.sort(key=_event_sort_key)
    return ParsedTrace(
        run_dir=resolved,
        navigation_dir=navigation_dir,
        manifest=manifest,
        events=tuple(events),
        files=files,
        parse_errors=tuple(errors),
    )


def reconstruct_sessions(trace: ParsedTrace) -> tuple[SessionWindow, ...]:
    """Rebuild sessions and recover open sessions as process-exit aborts."""
    starts: dict[str, dict[str, Any]] = {}
    ends: dict[str, dict[str, Any]] = {}
    for event in trace.events:
        session_id = event.get("navigation_session_id")
        if not isinstance(session_id, str):
            continue
        if event.get("event") == "navigation_session_started":
            starts[session_id] = event
        elif event.get("event") == "navigation_session_ended":
            ends[session_id] = event

    last_ns = max((_monotonic_ns(event) for event in trace.events), default=0)
    last_wall = next(
        (
            str(event["wall_ts"])
            for event in reversed(trace.events)
            if event.get("wall_ts") is not None
        ),
        None,
    )
    sessions: list[SessionWindow] = []
    for session_id, start in sorted(starts.items(), key=lambda item: _monotonic_ns(item[1])):
        end = ends.get(session_id)
        recovered = end is None
        end_ns = _monotonic_ns(end) if end is not None else last_ns
        sessions.append(
            SessionWindow(
                session_id=session_id,
                start_monotonic_ns=_monotonic_ns(start),
                end_monotonic_ns=max(_monotonic_ns(start), end_ns),
                start_wall_ts=_optional_string(start.get("wall_ts")),
                end_wall_ts=(
                    _optional_string(end.get("wall_ts")) if end is not None else last_wall
                ),
                entry_source=str(start.get("entry_source", "UNKNOWN")),
                terminal=(
                    str(end.get("terminal", "UNKNOWN"))
                    if end is not None
                    else "aborted_process_exit"
                ),
                terminal_reason=(
                    str(end.get("reason", "unspecified"))
                    if end is not None
                    else "planner_trace_ended_without_terminal_event"
                ),
                recovered_after_process_exit=recovered,
            )
        )
    return tuple(sessions)


def analyze_run(
    run_dir: Path,
    *,
    session_id: str | None = None,
    create_rerun: bool = True,
) -> tuple[Path, ...]:
    """Generate complete offline reports for one or all navigation sessions."""
    trace = load_trace(run_dir)
    sessions = reconstruct_sessions(trace)
    if session_id is not None:
        sessions = tuple(session for session in sessions if session.session_id == session_id)
        if not sessions:
            raise ValueError(f"navigation session not found: {session_id}")
    if not sessions:
        raise ValueError("no navigation sessions found in planner traces")

    outputs: list[Path] = []
    for session in sessions:
        outputs.append(_write_session_report(trace, session, create_rerun=create_rerun))
    return tuple(outputs)


def build_session_summary(
    trace: ParsedTrace,
    session: SessionWindow,
) -> tuple[dict[str, Any], tuple[PlanAnalysis, ...]]:
    """Build the machine-readable summary without writing files."""
    events = tuple(event for event in trace.events if _in_session(event, session))
    plans = _analyze_plans(trace, session, events)
    producer_health = _producer_health(trace)
    all_metrics = tuple(plan.metrics for plan in plans if plan.metrics is not None)
    response_lag = _response_lag(plans)
    drops = _collect_drops(trace.events)
    levels = _producer_levels(trace.events)
    blob_events = tuple(event for event in events if event.get("event") == "blob_saved")
    blob_kinds = {str(event.get("blob_kind")) for event in blob_events}
    pointcloud_sources = {
        str(metadata.get("source_kind"))
        for event in blob_events
        if event.get("blob_kind") == "pointcloud"
        and isinstance((metadata := event.get("metadata")), Mapping)
        and metadata.get("source_kind") is not None
    }
    costmap_events = tuple(event for event in events if event.get("event") == "costmap_published")
    tf_events = tuple(
        event for event in events if event.get("event") == "relocalization_tf_published"
    )
    obstacle_events = tuple(
        event for event in events if event.get("event") == "path_clearance_obstacle"
    )
    send_events = tuple(event for event in events if event.get("event") == "webrtc_command_send")
    heartbeat = _event_loop_heartbeat_summary(events)
    control_chain = _match_control_chain(events)
    path_costmap = _path_costmap_analysis(trace, session.session_id, plans, blob_events)
    ack_events = tuple(
        event
        for event in events
        if event.get("event")
        in {"go2_avoidance_configuration", "unitree_avoidance_switch_response"}
    )

    cte_rms, cte_p95, cte_max = _aggregate_cte(all_metrics)
    snake_candidates = [
        {
            **asdict(candidate),
            "threshold_status": "candidate_only_unless_field_calibrated",
        }
        for metrics in all_metrics
        for candidate in metrics.snake_candidates
    ]
    planned_lengths = [
        metrics.planned_path_length_m for plan in plans if (metrics := plan.metrics) is not None
    ]
    planned_length = planned_lengths[-1] if planned_lengths else 0.0
    odom_length = sum(metrics.odom_estimated_path_length_m for metrics in all_metrics)
    overshoot = max((metrics.overshoot_m for metrics in all_metrics), default=0.0)
    angular_flips = sum(metrics.angular_flip_count for metrics in all_metrics)

    root_cause, root_evidence, root_reason = _classify_avoidance(
        events=events,
        obstacle_events=obstacle_events,
        blob_kinds=blob_kinds,
        send_events=send_events,
        path_costmap=path_costmap,
    )
    map_hashes = _offline_map_hashes(trace.manifest)
    plan_summaries = [_plan_summary(plan) for plan in plans]
    summary: dict[str, Any] = {
        "schema_version": "1.0",
        "generated_wall_ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
        "run_id": trace.manifest.get("run_id", trace.run_dir.name),
        "navigation_session_id": session.session_id,
        "navigation_result": {
            "terminal": session.terminal,
            "reason": session.terminal_reason,
            "entry_source": session.entry_source,
            "recovered_after_process_exit": session.recovered_after_process_exit,
        },
        "time_window": {
            "start_monotonic_ns": session.start_monotonic_ns,
            "end_monotonic_ns": session.end_monotonic_ns,
            "start_wall_ts": session.start_wall_ts,
            "end_wall_ts": session.end_wall_ts,
        },
        "trajectory": {
            "planned_path_length_m": planned_length,
            "initial_planned_path_length_m": (planned_lengths[0] if planned_lengths else 0.0),
            "latest_planned_path_length_m": planned_length,
            "planned_path_lengths_are_not_summed_across_replans": True,
            "odom_estimated_path_length_m": odom_length,
            "odom_is_external_ground_truth": False,
            "cross_track_error_m": {
                "rms": cte_rms,
                "p95": cte_p95,
                "max": cte_max,
                "evidence": "OBSERVED" if all_metrics else "UNKNOWN",
            },
            "overshoot_m": overshoot,
            "overshoot_observed": overshoot > 0.0,
            "angular_flip_count": angular_flips,
            "snake_candidates": snake_candidates,
            "snake_thresholds": {
                "minimum_amplitude_m": 0.05,
                "minimum_cross_track_zero_crossings": 3,
                "minimum_angular_flips": 3,
                "calibrated_on_robot": False,
                "interpretation": "candidate intervals, not an absolute yes/no conclusion",
            },
        },
        "plans": plan_summaries,
        "replanning": {
            "count": max(0, len(plans) - 1),
            "reasons": [plan.plan_reason for plan in plans[1:]],
        },
        "environment_changes": {
            "costmap_event_count": len(costmap_events),
            "costmap_changed": _events_changed(costmap_events, "fingerprint"),
            "tf_event_count": len(tf_events),
            "tf_changed": len(tf_events) > 1,
        },
        "avoidance": {
            "classification": root_cause,
            "classification_vocabulary": list(ROOT_CAUSE_CLASSES),
            "evidence": root_evidence,
            "reason": root_reason,
            "path_clearance_obstacle_events": len(obstacle_events),
            "observed_blob_kinds": sorted(blob_kinds),
            "observed_pointcloud_sources": sorted(pointcloud_sources),
            "switch_observations": [_avoidance_event(event) for event in ack_events],
            "path_costmap_analysis": path_costmap,
            "evidence_chain": _obstacle_evidence_chain(events, blob_events),
        },
        "control_chain": {
            "planner_command_count": sum(len(plan.command_ts) for plan in plans),
            "mux_event_count": sum(
                event.get("event") == "mux_command_published" for event in events
            ),
            "webrtc_send_count": len(send_events),
            "send_is_robot_execution_ack": False,
            "robot_execution_ack_observed": any(
                event.get("robot_execution_ack") is True for event in send_events
            ),
            "closed_loop_response_lag": asdict(response_lag),
            "event_loop_heartbeat": heartbeat,
            "cross_worker_matching": control_chain,
        },
        "integrity": {
            "complete": (
                not trace.parse_errors
                and not producer_health["missing_producers"]
                and not producer_health["files_without_footer"]
                and not drops
            ),
            **producer_health,
            "parse_errors": list(trace.parse_errors),
            "drops": drops,
            "producer_effective_levels": levels,
            "mixed_trace_levels": len(set(levels.values())) > 1,
            "data_gaps": _data_gaps(
                producer_health=producer_health,
                parse_errors=trace.parse_errors,
                drops=drops,
                plans=plans,
            ),
        },
        "map_hashes": map_hashes,
        "evidence_contract": {
            "levels": ["OBSERVED", "CORRELATED", "INFERRED", "UNKNOWN"],
            "cross_worker_matching": "monotonic_time_twist_value_and_order_only",
            "identical_twist_ambiguity": True,
            "unitree_source_clock_domain_verified": False,
        },
    }
    return summary, plans


def _write_session_report(
    trace: ParsedTrace,
    session: SessionWindow,
    *,
    create_rerun: bool,
) -> Path:
    summary, plans = build_session_summary(trace, session)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%f")[:-3]
    report_dir = trace.navigation_dir / "reports" / f"{session.session_id}-{timestamp}"
    plots_dir = report_dir / "plots"
    plots_dir.mkdir(parents=True, exist_ok=False)
    (report_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    (report_dir / "report.md").write_text(
        _markdown_report(summary),
        encoding="utf-8",
    )
    _write_plots(trace, session, plans, plots_dir)
    if create_rerun:
        from dimos.navigation.diagnostics.rerun_export import export_rerun

        export_rerun(
            report_dir / "trace.rrd",
            plans,
            trace=trace,
            session=session,
        )
    return report_dir


def _analyze_plans(
    trace: ParsedTrace,
    session: SessionWindow,
    events: Sequence[dict[str, Any]],
) -> tuple[PlanAnalysis, ...]:
    plan_events = sorted(
        (
            event
            for event in events
            if event.get("event") == "plan_started" and isinstance(event.get("plan_version"), int)
        ),
        key=lambda event: (int(event["plan_version"]), _monotonic_ns(event)),
    )
    output: list[PlanAnalysis] = []
    for index, plan_event in enumerate(plan_events):
        version = int(plan_event["plan_version"])
        start_ns = _monotonic_ns(plan_event)
        end_ns = (
            _monotonic_ns(plan_events[index + 1])
            if index + 1 < len(plan_events)
            else session.end_monotonic_ns
        )
        raw = _load_path(trace.navigation_dir, session.session_id, version, "raw")
        smoothed = _load_path(trace.navigation_dir, session.session_id, version, "smoothed")
        plan_window = tuple(
            event
            for event in events
            if start_ns <= _monotonic_ns(event) <= end_ns
            and (
                event.get("navigation_session_id") in (None, session.session_id)
                or event.get("event") in {"trace_header", "trace_footer"}
            )
        )
        odom_events = tuple(event for event in plan_window if event.get("event") == "planner_odom")
        control_events = tuple(
            event for event in plan_window if event.get("event") == "planner_control_published"
        )
        odom_xy, odom_ts, odom_yaw = _odom_arrays(odom_events)
        command_ts, command_angular_z = _command_arrays(control_events)
        metrics: TrajectoryMetrics | None = None
        if smoothed is not None and len(smoothed) >= 2:
            metrics = calculate_trajectory_metrics(
                smoothed,
                odom_xy,
                timestamps=odom_ts,
                angular_z=_commands_at_odom(
                    odom_ts,
                    command_ts,
                    command_angular_z,
                ),
            )
        output.append(
            PlanAnalysis(
                plan_version=version,
                plan_reason=str(plan_event.get("plan_reason", "UNKNOWN")),
                raw_path=raw,
                smoothed_path=smoothed,
                odom_xy=odom_xy,
                odom_ts=odom_ts,
                odom_yaw=odom_yaw,
                command_ts=command_ts,
                command_angular_z=command_angular_z,
                metrics=metrics,
            )
        )
    return tuple(output)


def _load_path(
    navigation_dir: Path,
    session_id: str,
    plan_version: int,
    kind: str,
) -> NDArray[np.float64] | None:
    path = navigation_dir / "plans" / f"{session_id}-plan-{plan_version:04d}-{kind}.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        poses = payload["poses"]
        points = [[float(pose["position"]["x"]), float(pose["position"]["y"])] for pose in poses]
        return np.asarray(points, dtype=np.float64).reshape((-1, 2))
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
        return None


def _load_path_frame(
    navigation_dir: Path,
    *,
    session_id: str,
    plan_version: int,
    kind: str,
) -> str | None:
    """Load the frame recorded alongside a raw/smoothed path artifact."""
    path = navigation_dir / "plans" / f"{session_id}-plan-{plan_version:04d}-{kind}.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, TypeError, json.JSONDecodeError):
        return None
    frame_id = payload.get("frame_id") if isinstance(payload, Mapping) else None
    return str(frame_id) if frame_id is not None else None


def _odom_arrays(
    events: Sequence[dict[str, Any]],
) -> tuple[NDArray[np.float64], NDArray[np.float64], NDArray[np.float64]]:
    xy: list[list[float]] = []
    timestamps: list[float] = []
    yaw: list[float] = []
    for event in events:
        pose = event.get("pose")
        if not isinstance(pose, Mapping):
            continue
        try:
            xy.append([float(pose["x"]), float(pose["y"])])
            timestamps.append(_monotonic_ns(event) / 1_000_000_000)
            yaw.append(float(pose["yaw"]))
        except (KeyError, TypeError, ValueError):
            continue
    return (
        np.asarray(xy, dtype=np.float64).reshape((-1, 2)),
        np.asarray(timestamps, dtype=np.float64),
        np.asarray(yaw, dtype=np.float64),
    )


def _command_arrays(
    events: Sequence[dict[str, Any]],
) -> tuple[NDArray[np.float64], NDArray[np.float64]]:
    timestamps: list[float] = []
    angular_z: list[float] = []
    for event in events:
        twist = event.get("twist")
        if not isinstance(twist, Mapping):
            continue
        try:
            timestamp = event.get("command_generated_monotonic_ns")
            timestamp_ns = int(timestamp) if timestamp is not None else _monotonic_ns(event)
            timestamps.append(timestamp_ns / 1_000_000_000)
            angular_z.append(float(twist["angular_z"]))
        except (KeyError, TypeError, ValueError):
            continue
    return (
        np.asarray(timestamps, dtype=np.float64),
        np.asarray(angular_z, dtype=np.float64),
    )


def _commands_at_odom(
    odom_ts: NDArray[np.float64],
    command_ts: NDArray[np.float64],
    command_values: NDArray[np.float64],
) -> NDArray[np.float64]:
    if len(odom_ts) == 0:
        return np.array([], dtype=np.float64)
    if len(command_ts) == 0:
        return np.zeros(len(odom_ts), dtype=np.float64)
    indices = np.searchsorted(command_ts, odom_ts, side="right") - 1
    np.clip(indices, 0, len(command_values) - 1, out=indices)
    return command_values[indices]


def _response_lag(plans: Sequence[PlanAnalysis]) -> ResponseLagResult:
    all_lags: list[float] = []
    for plan in plans:
        if len(plan.odom_ts) < 2 or len(plan.command_ts) < 2:
            continue
        delta_ts = np.diff(plan.odom_ts)
        valid = delta_ts > 0
        if not bool(np.any(valid)):
            continue
        unwrapped = np.unwrap(plan.odom_yaw)
        yaw_rate = np.diff(unwrapped)[valid] / delta_ts[valid]
        yaw_rate_ts = plan.odom_ts[1:][valid]
        result = estimate_closed_loop_response_lag(
            plan.command_ts,
            plan.command_angular_z,
            yaw_rate_ts,
            yaw_rate,
        )
        all_lags.extend(result.lags_sec)
    if not all_lags:
        return ResponseLagResult((), None, None, "UNKNOWN")
    values = np.asarray(all_lags, dtype=np.float64)
    return ResponseLagResult(
        tuple(all_lags),
        float(np.median(values)),
        float(np.percentile(values, 95)),
        "CORRELATED",
    )


def _event_loop_heartbeat_summary(
    events: Sequence[dict[str, Any]],
) -> dict[str, Any]:
    delays_ms: list[float] = []
    intervals_sec: list[float] = []
    for event in events:
        if event.get("event") != "webrtc_loop_heartbeat":
            continue
        try:
            delays_ms.append(max(0.0, float(event["delay_ns"]) / 1_000_000))
            intervals_sec.append(float(event["interval_sec"]))
        except (KeyError, TypeError, ValueError):
            continue
    if not delays_ms:
        return {
            "sample_count": 0,
            "configured_interval_sec": None,
            "delay_ms": {
                "median": None,
                "p95": None,
                "p99": None,
                "max": None,
            },
            "evidence": "UNKNOWN",
        }
    values = np.asarray(delays_ms, dtype=np.float64)
    return {
        "sample_count": len(delays_ms),
        "configured_interval_sec": (
            float(np.median(np.asarray(intervals_sec, dtype=np.float64))) if intervals_sec else None
        ),
        "delay_ms": {
            "median": float(np.median(values)),
            "p95": float(np.percentile(values, 95)),
            "p99": float(np.percentile(values, 99)),
            "max": float(np.max(values)),
        },
        "evidence": "OBSERVED",
    }


def _producer_health(trace: ParsedTrace) -> dict[str, Any]:
    headers = {
        str(event.get("producer")) for event in trace.events if event.get("event") == "trace_header"
    }
    files_with_footer = {
        str(event.get("_trace_file"))
        for event in trace.events
        if event.get("event") == "trace_footer"
    }
    return {
        "observed_producers": sorted(headers),
        "missing_producers": sorted(set(EXPECTED_PRODUCERS) - headers),
        "files_without_footer": sorted(
            path.name for path in trace.files if path.name not in files_with_footer
        ),
        "producer_files": [
            {
                "path": path.name,
                "size_bytes": path.stat().st_size,
            }
            for path in trace.files
        ],
        "online_trace_bytes": sum(
            path.stat().st_size
            for path in trace.navigation_dir.rglob("*")
            if path.is_file() and "reports" not in path.relative_to(trace.navigation_dir).parts
        ),
    }


def _producer_levels(events: Sequence[dict[str, Any]]) -> dict[str, str]:
    levels: dict[str, str] = {}
    for event in events:
        producer = event.get("producer")
        level = event.get("effective_trace_level")
        if isinstance(producer, str) and isinstance(level, str):
            levels[producer] = level
    return levels


def _collect_drops(events: Sequence[dict[str, Any]]) -> dict[str, Any]:
    output: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
    for event in events:
        if event.get("event") != "trace_drop_summary":
            continue
        producer = str(event.get("producer", "unknown"))
        drops = event.get("drops")
        if not isinstance(drops, Mapping):
            continue
        for reason, details in drops.items():
            if isinstance(details, Mapping):
                try:
                    reason_key = str(reason)
                    aggregate = output[producer].setdefault(
                        reason_key,
                        {"count": 0, "windows": []},
                    )
                    aggregate["count"] += int(details.get("count", 0))
                    aggregate["windows"].append(
                        {
                            "first_wall_ts": details.get("first_wall_ts"),
                            "last_wall_ts": details.get("last_wall_ts"),
                            "trace_file": event.get("_trace_file"),
                        }
                    )
                except (TypeError, ValueError):
                    pass
    return {producer: dict(sorted(reasons.items())) for producer, reasons in sorted(output.items())}


def _data_gaps(
    *,
    producer_health: Mapping[str, Any],
    parse_errors: Sequence[Mapping[str, Any]],
    drops: Mapping[str, Any],
    plans: Sequence[PlanAnalysis],
) -> list[str]:
    gaps = [f"missing producer: {producer}" for producer in producer_health["missing_producers"]]
    gaps.extend(
        f"producer file has no footer: {path}" for path in producer_health["files_without_footer"]
    )
    if parse_errors:
        gaps.append(f"{len(parse_errors)} malformed/unreadable JSONL records")
    if drops:
        gaps.append("one or more producers reported dropped diagnostic data")
    for plan in plans:
        if plan.raw_path is None:
            gaps.append(f"plan {plan.plan_version}: raw path missing")
        if plan.smoothed_path is None:
            gaps.append(f"plan {plan.plan_version}: smoothed path missing")
        if len(plan.odom_xy) == 0:
            gaps.append(f"plan {plan.plan_version}: planner odom missing")
        if len(plan.command_ts) == 0:
            gaps.append(f"plan {plan.plan_version}: control cycles missing")
    return gaps


def _path_costmap_analysis(
    trace: ParsedTrace,
    session_id: str,
    plans: Sequence[PlanAnalysis],
    blob_events: Sequence[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Check each raw/smoothed path against the A* costmap actually recorded."""
    output: list[dict[str, Any]] = []
    for plan in plans:
        candidates = [
            event
            for event in blob_events
            if event.get("blob_kind") == "costmap"
            and isinstance(event.get("metadata"), Mapping)
            and event["metadata"].get("snapshot_kind") == "astar_navigation_costmap"
            and event["metadata"].get("plan_version") == plan.plan_version
        ]
        if not candidates:
            output.append(
                {
                    "plan_version": plan.plan_version,
                    "evidence": "UNKNOWN",
                    "reason": "A* costmap snapshot missing",
                }
            )
            continue
        event = max(candidates, key=_monotonic_ns)
        loaded = _load_blob_array(trace, event)
        metadata = event.get("metadata")
        if loaded is None or not isinstance(metadata, Mapping) or loaded.ndim != 2:
            output.append(
                {
                    "plan_version": plan.plan_version,
                    "evidence": "UNKNOWN",
                    "reason": "A* costmap snapshot unreadable",
                }
            )
            continue
        resolution, origin_x, origin_y, origin_yaw = _costmap_geometry(metadata)
        costmap_frame = _costmap_frame(metadata)
        path_frame = _load_path_frame(
            trace.navigation_dir,
            session_id=session_id,
            plan_version=plan.plan_version,
            kind="raw",
        )
        if path_frame is None:
            path_frame = _load_path_frame(
                trace.navigation_dir,
                session_id=session_id,
                plan_version=plan.plan_version,
                kind="smoothed",
            )
        if resolution <= 0:
            output.append(
                {
                    "plan_version": plan.plan_version,
                    "evidence": "UNKNOWN",
                    "reason": "invalid or missing costmap resolution",
                }
            )
            continue
        output.append(
            {
                "plan_version": plan.plan_version,
                "evidence": "OBSERVED",
                "blob_path": event.get("blob_path"),
                "resolution_m": resolution,
                "origin": {
                    "x": origin_x,
                    "y": origin_y,
                    "yaw": origin_yaw,
                },
                "origin_rotation_applied": True,
                "path_frame_id": path_frame,
                "costmap_frame_id": costmap_frame,
                "frame_consistent": (
                    path_frame == costmap_frame
                    if path_frame is not None and costmap_frame is not None
                    else None
                ),
                "raw_path": _sample_path_costmap(
                    loaded,
                    metadata,
                    plan.raw_path,
                ),
                "smoothed_path": _sample_path_costmap(
                    loaded,
                    metadata,
                    plan.smoothed_path,
                ),
            }
        )
    return output


def _sample_path_costmap(
    grid: NDArray[Any],
    metadata: Mapping[str, Any],
    path: NDArray[np.float64] | None,
) -> dict[str, Any]:
    if path is None or len(path) == 0:
        return {"available": False}
    resolution, origin_x, origin_y, origin_yaw = _costmap_geometry(metadata)
    if resolution <= 0:
        return {"available": False}

    samples: list[NDArray[np.float64]] = []
    if len(path) == 1:
        samples.append(path[:1])
    else:
        maximum_step = max(resolution * 0.5, 1e-6)
        for start, end in pairwise(path):
            distance = float(np.linalg.norm(end - start))
            count = max(2, int(np.ceil(distance / maximum_step)) + 1)
            samples.append(np.linspace(start, end, count, endpoint=True, dtype=np.float64))
    points = np.concatenate(samples, axis=0)
    translated = points - np.array([origin_x, origin_y], dtype=np.float64)
    cosine = np.cos(origin_yaw)
    sine = np.sin(origin_yaw)
    local_x = cosine * translated[:, 0] + sine * translated[:, 1]
    local_y = -sine * translated[:, 0] + cosine * translated[:, 1]
    columns = np.floor(local_x / resolution).astype(np.int64)
    rows = np.floor(local_y / resolution).astype(np.int64)
    inside = (rows >= 0) & (rows < grid.shape[0]) & (columns >= 0) & (columns < grid.shape[1])
    cell_pairs = np.unique(
        np.column_stack((rows[inside], columns[inside])),
        axis=0,
    )
    values = (
        np.asarray(grid)[cell_pairs[:, 0], cell_pairs[:, 1]]
        if len(cell_pairs)
        else np.asarray([], dtype=np.int8)
    )
    return {
        "available": True,
        "sample_count": len(points),
        "unique_grid_cells": len(cell_pairs),
        "outside_grid_samples": int(np.count_nonzero(~inside)),
        "free_cells": int(np.count_nonzero(values == 0)),
        "unknown_cells": int(np.count_nonzero(values < 0)),
        "inflated_cells": int(np.count_nonzero((values > 0) & (values < 100))),
        "lethal_cells": int(np.count_nonzero(values >= 100)),
        "minimum_cost": int(np.min(values)) if len(values) else None,
        "maximum_cost": int(np.max(values)) if len(values) else None,
    }


def _costmap_geometry(
    metadata: Mapping[str, Any],
) -> tuple[float, float, float, float]:
    nested = metadata.get("costmap")
    geometry = nested if isinstance(nested, Mapping) else metadata
    resolution = _as_float(geometry.get("resolution"), 0.0)
    origin = geometry.get("origin")
    origin_x = origin_y = origin_yaw = 0.0
    if isinstance(origin, Mapping):
        position = origin.get("position")
        if isinstance(position, Mapping):
            origin_x = _as_float(position.get("x"), 0.0)
            origin_y = _as_float(position.get("y"), 0.0)
        else:
            origin_x = _as_float(origin.get("x"), 0.0)
            origin_y = _as_float(origin.get("y"), 0.0)
        origin_yaw = _as_float(origin.get("yaw"), 0.0)
    return resolution, origin_x, origin_y, origin_yaw


def _costmap_frame(metadata: Mapping[str, Any]) -> str | None:
    nested = metadata.get("costmap")
    geometry = nested if isinstance(nested, Mapping) else metadata
    frame_id = geometry.get("frame_id")
    return str(frame_id) if frame_id is not None else None


def _load_blob_array(
    trace: ParsedTrace,
    event: Mapping[str, Any],
) -> NDArray[Any] | None:
    relative = event.get("blob_path")
    if not isinstance(relative, str):
        return None
    path = (trace.navigation_dir / relative).resolve()
    try:
        path.relative_to(trace.navigation_dir)
        return cast("NDArray[Any]", np.load(path, allow_pickle=False))
    except (OSError, ValueError):
        return None


def _classify_avoidance(
    *,
    events: Sequence[dict[str, Any]],
    obstacle_events: Sequence[dict[str, Any]],
    blob_kinds: set[str],
    send_events: Sequence[dict[str, Any]],
    path_costmap: Sequence[Mapping[str, Any]],
) -> tuple[str, str, str]:
    explicit = _explicit_avoidance_classification(events)
    if explicit is not None:
        return explicit
    for observation in path_costmap:
        if observation.get("frame_consistent") is False:
            return (
                "TF_MISALIGNED",
                "CORRELATED",
                "planned path and its A* costmap use different frame_id values",
            )
    # PathClearance emits a stop even when the map itself is unavailable.  In
    # that case the missing sensor/map is stronger evidence than the generic
    # "no blob" fallback below.
    if any(
        isinstance(event.get("decision"), Mapping)
        and event["decision"].get("reason") == "costmap_missing"
        for event in obstacle_events
    ):
        return (
            "SENSOR_MISSING",
            "OBSERVED",
            "PathClearance reported an obstacle because its costmap was missing",
        )
    for observation in path_costmap:
        raw = observation.get("raw_path")
        smoothed = observation.get("smoothed_path")
        if not isinstance(raw, Mapping) or not isinstance(smoothed, Mapping):
            continue
        raw_lethal = _as_int(raw.get("lethal_cells"))
        smoothed_lethal = _as_int(smoothed.get("lethal_cells"))
        if raw_lethal > 0:
            return (
                "RAW_PATH_COLLISION",
                "OBSERVED",
                f"raw path intersects {raw_lethal} lethal costmap cells",
            )
        if smoothed_lethal > 0:
            return (
                "SMOOTHING_COLLISION",
                "OBSERVED",
                "raw path is clear but the smoothed path intersects "
                f"{smoothed_lethal} lethal costmap cells",
            )
        unknown_cells = max(
            _as_int(raw.get("unknown_cells")),
            _as_int(smoothed.get("unknown_cells")),
        )
        if unknown_cells > 0:
            return (
                "UNKNOWN_SPACE_TRAVERSED",
                "OBSERVED",
                f"planned path intersects {unknown_cells} unknown costmap cells",
            )
    if not obstacle_events:
        return (
            "INSUFFICIENT_EVIDENCE",
            "UNKNOWN",
            "no PathClearance anomaly was observed; absence is not external proof",
        )
    pointcloud_issue = _pointcloud_freshness_issue(events, obstacle_events)
    if pointcloud_issue is not None:
        return pointcloud_issue
    stop_propagation = _stop_propagation_issue(events, obstacle_events)
    if stop_propagation is not None:
        return (
            "STOP_NOT_PROPAGATED",
            "CORRELATED",
            stop_propagation,
        )
    transport_issue = _control_transport_issue(send_events)
    if transport_issue is not None:
        return (
            "CONTROL_TRANSPORT_DELAY",
            "OBSERVED",
            transport_issue,
        )
    required = {"pointcloud", "costmap"}
    missing = sorted(required - blob_kinds)
    if missing:
        return (
            "INSUFFICIENT_EVIDENCE",
            "OBSERVED",
            f"obstacle stop observed, but required evidence is missing: {', '.join(missing)}",
        )
    if not send_events:
        return (
            "STOP_NOT_PROPAGATED",
            "CORRELATED",
            "obstacle stop and map evidence exist, but no WebRTC send was observed",
        )
    return (
        "INSUFFICIENT_EVIDENCE",
        "CORRELATED",
        "the sensor-to-costmap chain is present, but no external obstacle ground truth exists",
    )


def _explicit_avoidance_classification(
    events: Sequence[dict[str, Any]],
) -> tuple[str, str, str] | None:
    """Honor producer-level facts when a layer can prove a matrix outcome.

    This keeps the report conservative: a producer must explicitly publish a
    boolean or class, rather than the offline analyzer guessing from a missing
    artifact.  The aliases support traces written by older experimental
    producers while preserving the public vocabulary.
    """
    aliases = {
        "tf_misaligned": "TF_MISALIGNED",
        "merge_missing_obstacle": "MERGE_MISSING_OBSTACLE",
        "costmap_classification_miss": "COSTMAP_CLASSIFICATION_MISS",
        "inflation_insufficient": "INFLATION_INSUFFICIENT",
        "path_clearance_miss": "PATH_CLEARANCE_MISS",
        "robot_execution_mismatch": "ROBOT_EXECUTION_MISMATCH",
    }
    for event in events:
        candidate = event.get("root_cause_class") or event.get("diagnostic_class")
        if isinstance(candidate, str) and candidate in ROOT_CAUSE_CLASSES:
            evidence = str(event.get("evidence_level", "OBSERVED"))
            reason = str(event.get("root_cause_reason", "producer reported this classification"))
            return candidate, evidence, reason
        for key, candidate in aliases.items():
            if event.get(key) is True:
                evidence = str(event.get("evidence_level", "OBSERVED"))
                return (
                    candidate,
                    evidence,
                    str(event.get("root_cause_reason", f"producer reported {key}")),
                )
    return None


def _pointcloud_freshness_issue(
    events: Sequence[dict[str, Any]],
    obstacle_events: Sequence[dict[str, Any]],
) -> tuple[str, str, str] | None:
    first_obstacle_ns = min(map(_monotonic_ns, obstacle_events))
    connection_levels = {
        str(event.get("effective_trace_level"))
        for event in events
        if event.get("event") == "trace_header" and event.get("producer") == "connection"
    }
    connection_drops = any(
        event.get("event") == "trace_drop_summary" and event.get("producer") == "connection"
        for event in events
    )
    if not connection_levels.intersection({"full", "forensic"}) or connection_drops:
        return None
    lidar_events = [
        event
        for event in events
        if event.get("event") == "connection_raw_lidar"
        and _monotonic_ns(event) <= first_obstacle_ns
    ]
    if not lidar_events:
        return (
            "SENSOR_MISSING",
            "CORRELATED",
            "complete full/forensic connection trace has no raw lidar before the obstacle event",
        )
    latest_ns = max(map(_monotonic_ns, lidar_events))
    age_sec = (first_obstacle_ns - latest_ns) / 1_000_000_000
    if age_sec > 2.0:
        return (
            "POINTCLOUD_STALE",
            "OBSERVED",
            f"latest raw lidar host observation was {age_sec:.3f}s before the obstacle event",
        )
    return None


def _stop_propagation_issue(
    events: Sequence[dict[str, Any]],
    obstacle_events: Sequence[dict[str, Any]],
) -> str | None:
    first_obstacle_ns = min(map(_monotonic_ns, obstacle_events))
    stops = [
        event
        for event in events
        if event.get("event") == "planner_stop_command_published"
        and abs(_monotonic_ns(event) - first_obstacle_ns) <= 1_000_000_000
    ]
    if not stops:
        return "PathClearance requested a stop, but no planner zero-command event was observed"
    stop_ns = min(_monotonic_ns(event) for event in stops)
    continued = [
        event
        for event in events
        if event.get("event") == "mux_command_published"
        and event.get("source") == "navigation"
        and 50_000_000 <= _monotonic_ns(event) - stop_ns <= 1_000_000_000
        and _twist_has_motion(event.get("twist"))
    ]
    if continued:
        return (
            "MovementManager published a non-zero navigation command more than "
            "50 ms after the planner zero command"
        )
    return None


def _control_transport_issue(
    send_events: Sequence[dict[str, Any]],
) -> str | None:
    durations_ms: list[float] = []
    maximum_buffered_amount = 0
    for event in send_events:
        duration = event.get("send_duration_ns")
        if duration is not None:
            durations_ms.append(max(0.0, _as_float(duration, 0.0) / 1_000_000))
        datachannel = event.get("datachannel")
        if isinstance(datachannel, Mapping):
            maximum_buffered_amount = max(
                maximum_buffered_amount,
                _as_int(datachannel.get("buffered_amount")),
            )
    if durations_ms and float(np.percentile(durations_ms, 95)) > 200.0:
        return "WebRTC send-call duration P95 exceeded 200 ms"
    if maximum_buffered_amount > 65_536:
        return (
            "WebRTC DataChannel bufferedAmount exceeded 65536 bytes "
            f"(observed {maximum_buffered_amount})"
        )
    return None


def _twist_has_motion(value: Any) -> bool:
    if not isinstance(value, Mapping):
        return False
    return any(
        abs(_as_float(value.get(component), 0.0)) > 1e-6
        for component in ("linear_x", "linear_y", "linear_z", "angular_x", "angular_y", "angular_z")
    )


def _avoidance_event(event: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: event.get(key)
        for key in (
            "event",
            "switch_name",
            "requested_enabled",
            "request_completed",
            "response_received",
            "ack_observed",
            "acknowledged_value",
            "api_id",
            "error",
        )
        if key in event
    }


def _obstacle_evidence_chain(
    events: Sequence[dict[str, Any]],
    blob_events: Sequence[dict[str, Any]],
) -> list[dict[str, Any]]:
    obstacle = next(
        (event for event in events if event.get("event") == "path_clearance_obstacle"),
        None,
    )
    reference_ns = _monotonic_ns(obstacle)

    def blob_source(source: str) -> dict[str, Any] | None:
        return next(
            (
                event
                for event in reversed(blob_events)
                if isinstance(event.get("metadata"), Mapping)
                and event["metadata"].get("source_kind") == source
            ),
            None,
        )

    def named(name: str) -> dict[str, Any] | None:
        return next(
            (event for event in reversed(events) if event.get("event") == name),
            None,
        )

    def costmap_blob() -> dict[str, Any] | None:
        return next(
            (event for event in reversed(blob_events) if event.get("blob_kind") == "costmap"),
            None,
        )

    stages = (
        ("raw_lidar", blob_source("raw_lidar")),
        ("global_map", blob_source("global_map")),
        ("relocalization_tf", named("relocalization_tf_published")),
        ("merged_map", blob_source("merged_map") or named("merged_map_published")),
        ("costmap", costmap_blob()),
        ("path_clearance", obstacle),
        ("planner_stop", named("planner_stop_command_published")),
        ("movement_mux", named("mux_command_published")),
        ("webrtc_send", named("webrtc_command_send")),
    )
    chain: list[dict[str, Any]] = []
    for stage, event in stages:
        if event is None:
            chain.append(
                {
                    "stage": stage,
                    "status": "MISSING",
                    "evidence": "UNKNOWN",
                    "delta_from_obstacle_ms": None,
                }
            )
            continue
        timestamp = _monotonic_ns(event)
        chain.append(
            {
                "stage": stage,
                "status": "OBSERVED",
                "evidence": "OBSERVED",
                "event": event.get("event"),
                "producer": event.get("producer"),
                "producer_seq": event.get("producer_seq"),
                "delta_from_obstacle_ms": (
                    (timestamp - reference_ns) / 1_000_000 if reference_ns else None
                ),
                "cross_worker_causality": (
                    "CORRELATED" if reference_ns and timestamp != reference_ns else "UNKNOWN"
                ),
            }
        )
    return chain


def _match_control_chain(events: Sequence[dict[str, Any]]) -> dict[str, Any]:
    planner = [event for event in events if event.get("event") == "planner_control_published"]
    mux = [
        event
        for event in events
        if event.get("event") == "mux_command_published" and event.get("source") == "navigation"
    ]
    sends = [event for event in events if event.get("event") == "webrtc_command_send"]
    used_mux: set[int] = set()
    used_sends: set[int] = set()
    planner_to_mux_ms: list[float] = []
    mux_to_send_ms: list[float] = []
    confidence_counts: dict[str, int] = defaultdict(int)
    examples: list[dict[str, Any]] = []

    for planner_event in planner:
        planner_ts = _command_timestamp_ns(
            planner_event,
            "command_generated_monotonic_ns",
        )
        mux_candidates = _command_candidates(
            planner_event,
            planner_ts,
            mux,
            used_mux,
            timestamp_key="command_muxed_monotonic_ns",
        )
        if not mux_candidates:
            confidence_counts["UNKNOWN"] += 1
            continue
        mux_index = mux_candidates[0]
        used_mux.add(mux_index)
        mux_event = mux[mux_index]
        mux_ts = _command_timestamp_ns(mux_event, "command_muxed_monotonic_ns")
        planner_to_mux_ms.append(max(0.0, (mux_ts - planner_ts) / 1_000_000))

        send_candidates = _command_candidates(
            mux_event,
            mux_ts,
            sends,
            used_sends,
            timestamp_key="command_send_monotonic_ns",
        )
        if not send_candidates:
            confidence_counts["MEDIUM"] += 1
            continue
        send_index = send_candidates[0]
        used_sends.add(send_index)
        send_event = sends[send_index]
        send_ts = _command_timestamp_ns(send_event, "command_send_monotonic_ns")
        mux_to_send_ms.append(max(0.0, (send_ts - mux_ts) / 1_000_000))
        confidence = "HIGH" if len(mux_candidates) == 1 and len(send_candidates) == 1 else "MEDIUM"
        confidence_counts[confidence] += 1
        if len(examples) < 20:
            examples.append(
                {
                    "planner_producer_seq": planner_event.get("producer_seq"),
                    "mux_producer_seq": mux_event.get("producer_seq"),
                    "send_producer_seq": send_event.get("producer_seq"),
                    "planner_to_mux_ms": planner_to_mux_ms[-1],
                    "mux_to_send_ms": mux_to_send_ms[-1],
                    "confidence": confidence,
                    "identical_twist_candidate_counts": {
                        "mux": len(mux_candidates),
                        "send": len(send_candidates),
                    },
                }
            )
    return {
        "method": "monotonic_time_twist_value_and_order",
        "evidence": "CORRELATED" if planner and mux and sends else "UNKNOWN",
        "exact_trace_id_available": False,
        "identical_twist_can_be_ambiguous": True,
        "matched_end_to_end": len(mux_to_send_ms),
        "unmatched_planner_to_mux": max(0, len(planner) - len(planner_to_mux_ms)),
        "unmatched_mux_to_send": max(0, len(planner_to_mux_ms) - len(mux_to_send_ms)),
        "confidence_counts": dict(confidence_counts),
        "planner_to_mux_latency_ms": _latency_summary(planner_to_mux_ms),
        "mux_to_send_latency_ms": _latency_summary(mux_to_send_ms),
        "examples": examples,
    }


def _command_candidates(
    source_event: Mapping[str, Any],
    source_ts: int,
    candidates: Sequence[dict[str, Any]],
    used: set[int],
    *,
    timestamp_key: str,
) -> list[int]:
    source_twist = source_event.get("twist")
    if not isinstance(source_twist, Mapping):
        return []
    matches: list[tuple[int, int]] = []
    for index, candidate in enumerate(candidates):
        if index in used or not _twists_match(source_twist, candidate.get("twist")):
            continue
        candidate_ts = _command_timestamp_ns(candidate, timestamp_key)
        delta = candidate_ts - source_ts
        if -50_000_000 <= delta <= 1_000_000_000:
            matches.append((abs(delta), index))
    matches.sort()
    return [index for _delta, index in matches]


def _twists_match(left: Mapping[str, Any], right_value: Any) -> bool:
    if not isinstance(right_value, Mapping):
        return False
    keys = (
        "linear_x",
        "linear_y",
        "linear_z",
        "angular_x",
        "angular_y",
        "angular_z",
    )
    try:
        return all(
            abs(float(left.get(key, 0.0)) - float(right_value.get(key, 0.0))) <= 1e-6
            for key in keys
        )
    except (TypeError, ValueError):
        return False


def _command_timestamp_ns(event: Mapping[str, Any], key: str) -> int:
    try:
        return int(event.get(key, event.get("monotonic_ns", 0)) or 0)
    except (TypeError, ValueError):
        return _monotonic_ns(event)


def _latency_summary(values: Sequence[float]) -> dict[str, float | None]:
    if not values:
        return {"median": None, "p95": None, "max": None}
    array = np.asarray(values, dtype=np.float64)
    return {
        "median": float(np.median(array)),
        "p95": float(np.percentile(array, 95)),
        "max": float(np.max(array)),
    }


def _plan_summary(plan: PlanAnalysis) -> dict[str, Any]:
    output: dict[str, Any] = {
        "plan_version": plan.plan_version,
        "plan_reason": plan.plan_reason,
        "raw_path_available": plan.raw_path is not None,
        "smoothed_path_available": plan.smoothed_path is not None,
        "odom_samples": len(plan.odom_xy),
        "control_samples": len(plan.command_ts),
    }
    if plan.metrics is not None:
        output["metrics"] = asdict(plan.metrics)
    return output


def _aggregate_cte(
    metrics: Sequence[TrajectoryMetrics],
) -> tuple[float | None, float | None, float | None]:
    if not metrics:
        return None, None, None
    # Plan-level aggregation stays explicit in ``plans``. These session values
    # summarize plan metrics without pretending samples are equally weighted.
    return (
        float(np.sqrt(np.mean([item.cross_track_rms_m**2 for item in metrics]))),
        max(item.cross_track_p95_m for item in metrics),
        max(item.cross_track_max_m for item in metrics),
    )


def _events_changed(events: Sequence[dict[str, Any]], key: str) -> bool:
    values = {
        json.dumps(event.get(key), sort_keys=True, default=str)
        for event in events
        if event.get(key) is not None
    }
    return len(values) > 1


def _offline_map_hashes(manifest: Mapping[str, Any]) -> list[dict[str, Any]]:
    hashes: list[dict[str, Any]] = []
    map_inputs = manifest.get("map_inputs")
    if not isinstance(map_inputs, list):
        return hashes
    for item in map_inputs:
        if not isinstance(item, Mapping):
            continue
        resolved = item.get("resolved_path")
        if not isinstance(resolved, str):
            continue
        path = Path(resolved)
        try:
            digest = hashlib.sha256()
            with path.open("rb") as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(chunk)
            hashes.append(
                {
                    "config_path": item.get("config_path"),
                    "path": resolved,
                    "sha256": digest.hexdigest(),
                    "evidence": "OBSERVED",
                }
            )
        except OSError as exc:
            hashes.append(
                {
                    "config_path": item.get("config_path"),
                    "path": resolved,
                    "sha256": None,
                    "evidence": "UNKNOWN",
                    "error": str(exc),
                }
            )
    return hashes


def _markdown_report(summary: Mapping[str, Any]) -> str:
    result = summary["navigation_result"]
    trajectory = summary["trajectory"]
    cte = trajectory["cross_track_error_m"]
    integrity = summary["integrity"]
    avoidance = summary["avoidance"]
    lag = summary["control_chain"]["closed_loop_response_lag"]
    heartbeat = summary["control_chain"]["event_loop_heartbeat"]
    heartbeat_delay = heartbeat["delay_ms"]
    lines = [
        f"# Navigation diagnostic report: {summary['navigation_session_id']}",
        "",
        "## Outcome",
        "",
        f"- Result: `{result['terminal']}` ({result['reason']})",
        f"- Entry source: `{result['entry_source']}`",
        (
            "- Initial/latest planned length: "
            f"{_format_metric(trajectory['initial_planned_path_length_m'], 'm')} / "
            f"{_format_metric(trajectory['latest_planned_path_length_m'], 'm')} "
            "(replan alternatives are not summed)"
        ),
        (
            "- Odom-estimated trajectory length: "
            f"{_format_metric(trajectory['odom_estimated_path_length_m'], 'm')} "
            "(not external ground truth)"
        ),
        (
            "- Cross-track error RMS/P95/max: "
            f"{_format_metric(cte['rms'], 'm')} / "
            f"{_format_metric(cte['p95'], 'm')} / "
            f"{_format_metric(cte['max'], 'm')}"
        ),
        f"- Overshoot: {_format_metric(trajectory['overshoot_m'], 'm')}",
        f"- Angular command flips: {trajectory['angular_flip_count']}",
        (
            "- Snake candidates: "
            f"{len(trajectory['snake_candidates'])}; thresholds are not field-calibrated"
        ),
        f"- Replans: {summary['replanning']['count']} ({summary['replanning']['reasons']})",
        (
            "- Costmap/TF changed: "
            f"{summary['environment_changes']['costmap_changed']} / "
            f"{summary['environment_changes']['tf_changed']}"
        ),
        (
            "- Avoidance classification: "
            f"`{avoidance['classification']}` ({avoidance['evidence']}) — "
            f"{avoidance['reason']}"
        ),
        (
            "- Closed-loop response lag median/P95: "
            f"{_format_metric(lag['median_sec'], 's')} / "
            f"{_format_metric(lag['p95_sec'], 's')} ({lag['evidence']})"
        ),
        (
            "- WebRTC event-loop heartbeat delay median/P95/P99: "
            f"{_format_metric(heartbeat_delay['median'], 'ms')} / "
            f"{_format_metric(heartbeat_delay['p95'], 'ms')} / "
            f"{_format_metric(heartbeat_delay['p99'], 'ms')} "
            f"({heartbeat['evidence']}, n={heartbeat['sample_count']})"
        ),
        f"- Data complete: `{integrity['complete']}`",
        "",
        "## Data integrity",
        "",
    ]
    gaps = integrity["data_gaps"]
    lines.extend(f"- {gap}" for gap in gaps)
    if not gaps:
        lines.append("- No trace gaps reported.")
    lines.extend(
        [
            "",
            "## Evidence limits",
            "",
            "- Odometry is the robot's pose estimate, not external ground truth.",
            "- A successful WebRTC send is not proof that the robot executed the command.",
            "- Cross-worker command association is correlation by time, values, and order.",
            "- Sensor-to-obstacle conclusions remain insufficient when required blobs are absent.",
            "",
            "## Artifacts",
            "",
            "- Machine-readable summary: `summary.json`",
            "- Rerun recording: `trace.rrd`",
            "- Plots: `plots/`",
            "",
        ]
    )
    return "\n".join(lines)


def _write_plots(
    trace: ParsedTrace,
    session: SessionWindow,
    plans: Sequence[PlanAnalysis],
    plots_dir: Path,
) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    def save(name: str, title: str, draw: Any) -> None:
        figure, axis = plt.subplots(figsize=(8, 5))
        axis.set_title(title)
        draw(axis)
        figure.savefig(plots_dir / name, dpi=140, bbox_inches="tight")
        plt.close(figure)

    def planned_actual(axis: Any) -> None:
        has_data = False
        for plan in plans:
            if plan.raw_path is not None:
                axis.plot(
                    plan.raw_path[:, 0],
                    plan.raw_path[:, 1],
                    "--",
                    alpha=0.5,
                    label=f"plan {plan.plan_version} raw",
                )
                has_data = True
            if plan.smoothed_path is not None:
                axis.plot(
                    plan.smoothed_path[:, 0],
                    plan.smoothed_path[:, 1],
                    label=f"plan {plan.plan_version} smoothed",
                )
                has_data = True
            if len(plan.odom_xy):
                axis.plot(
                    plan.odom_xy[:, 0],
                    plan.odom_xy[:, 1],
                    label=f"plan {plan.plan_version} odom estimate",
                )
                has_data = True
        _finish_axis(axis, has_data, equal=True)

    def cte(axis: Any) -> None:
        has_data = False
        for plan in plans:
            if plan.smoothed_path is None or len(plan.odom_xy) == 0:
                continue
            projection = project_to_polyline(plan.odom_xy, plan.smoothed_path)
            relative = plan.odom_ts - plan.odom_ts[0]
            axis.plot(
                relative,
                projection.signed_cross_track_m,
                label=f"plan {plan.plan_version}",
            )
            has_data = True
        axis.axhline(0.0, color="black", linewidth=0.7)
        axis.set_xlabel("time since plan start (s)")
        axis.set_ylabel("signed cross-track error (m)")
        _finish_axis(axis, has_data)

    def heading_command(axis: Any) -> None:
        has_data = False
        second = axis.twinx()
        for plan in plans:
            if len(plan.odom_ts):
                axis.plot(
                    plan.odom_ts - plan.odom_ts[0],
                    plan.odom_yaw,
                    label=f"plan {plan.plan_version} odom yaw",
                )
                has_data = True
            if len(plan.command_ts):
                origin = plan.odom_ts[0] if len(plan.odom_ts) else plan.command_ts[0]
                second.plot(
                    plan.command_ts - origin,
                    plan.command_angular_z,
                    ":",
                    label=f"plan {plan.plan_version} angular cmd",
                )
                has_data = True
        axis.set_xlabel("time since plan start (s)")
        axis.set_ylabel("odom yaw (rad)")
        second.set_ylabel("angular command (rad/s)")
        _finish_axis(axis, has_data)

    def latency(axis: Any) -> None:
        sends = [
            event
            for event in trace.events
            if _in_session(event, session)
            and event.get("event") == "webrtc_command_send"
            and event.get("send_duration_ns") is not None
        ]
        if sends:
            values = np.asarray([float(event["send_duration_ns"]) / 1_000_000 for event in sends])
            axis.plot(values, label="WebRTC send call duration")
            axis.set_ylabel("duration (ms)")
            axis.set_xlabel("send sequence")
        _finish_axis(axis, bool(sends))

    def costmap_overlay(axis: Any) -> None:
        blob = _latest_blob(trace, session, "costmap")
        has_data = False
        if blob is not None:
            array, metadata = blob
            resolution, origin_x, origin_y, origin_yaw = _costmap_geometry(metadata)
            if resolution > 0:
                columns = np.arange(array.shape[1] + 1, dtype=np.float64) * resolution
                rows = np.arange(array.shape[0] + 1, dtype=np.float64) * resolution
                local_x, local_y = np.meshgrid(columns, rows)
                cosine = np.cos(origin_yaw)
                sine = np.sin(origin_yaw)
                world_x = origin_x + cosine * local_x - sine * local_y
                world_y = origin_y + sine * local_x + cosine * local_y
                axis.pcolormesh(
                    world_x,
                    world_y,
                    array,
                    cmap="gray_r",
                    alpha=0.55,
                    shading="flat",
                )
                has_data = True
            for plan in plans:
                if plan.raw_path is not None and len(plan.raw_path):
                    axis.plot(
                        plan.raw_path[:, 0],
                        plan.raw_path[:, 1],
                        ":",
                        linewidth=1.0,
                        label=f"plan {plan.plan_version} raw",
                    )
                    has_data = True
                if plan.smoothed_path is not None and len(plan.smoothed_path):
                    axis.plot(
                        plan.smoothed_path[:, 0],
                        plan.smoothed_path[:, 1],
                        "-",
                        linewidth=1.2,
                        label=f"plan {plan.plan_version} smoothed",
                    )
                    has_data = True
                if len(plan.odom_xy):
                    axis.plot(
                        plan.odom_xy[:, 0],
                        plan.odom_xy[:, 1],
                        "--",
                        linewidth=1.0,
                        label=f"plan {plan.plan_version} odom estimate",
                    )
                    has_data = True
            pointcloud_blob = _latest_blob(trace, session, "pointcloud")
            if pointcloud_blob is not None:
                points, pointcloud_metadata = pointcloud_blob
                costmap_frame = _costmap_frame(metadata)
                pointcloud_frame = pointcloud_metadata.get("frame_id")
                if (
                    points.ndim == 2
                    and points.shape[1] >= 2
                    and isinstance(pointcloud_frame, str)
                    and pointcloud_frame == costmap_frame
                ):
                    axis.scatter(
                        points[:, 0],
                        points[:, 1],
                        s=1,
                        alpha=0.25,
                        label=f"pointcloud ({pointcloud_frame})",
                    )
                    has_data = True
            axis.set_xlabel(f"world x (costmap: {metadata.get('snapshot_kind', 'unknown')})")
            axis.set_ylabel("world y")
            axis.set_aspect("equal", adjustable="box")
        _finish_axis(axis, has_data)

    def obstacle_timeline(axis: Any) -> None:
        names = (
            "path_clearance_obstacle",
            "planner_stop_command_published",
            "mux_command_published",
            "webrtc_command_send",
            "command_watchdog_timer_fired",
        )
        rows = {name: index for index, name in enumerate(names)}
        has_data = False
        for event in trace.events:
            name = event.get("event")
            if name not in rows or not _in_session(event, session):
                continue
            relative = (_monotonic_ns(event) - session.start_monotonic_ns) / 1_000_000_000
            axis.scatter(relative, rows[str(name)], s=18)
            has_data = True
        axis.set_yticks(list(rows.values()), labels=list(rows))
        axis.set_xlabel("time since session start (s)")
        _finish_axis(axis, has_data)

    save("planned-vs-actual.png", "Planned paths vs odom estimate", planned_actual)
    save("cross-track-error.png", "Continuous cross-track error", cte)
    save("heading-and-command.png", "Heading and angular command", heading_command)
    save("latency-and-jitter.png", "Observed send-call latency", latency)
    save("costmap-overlay.png", "Latest observed costmap", costmap_overlay)
    save("obstacle-timeline.png", "Obstacle and command timeline", obstacle_timeline)


def _latest_blob(
    trace: ParsedTrace,
    session: SessionWindow,
    kind: str,
) -> tuple[NDArray[Any], Mapping[str, Any]] | None:
    for event in reversed(trace.events):
        if (
            event.get("event") != "blob_saved"
            or event.get("blob_kind") != kind
            or not _in_session(event, session)
        ):
            continue
        relative = event.get("blob_path")
        if not isinstance(relative, str):
            continue
        path = (trace.navigation_dir / relative).resolve()
        try:
            path.relative_to(trace.navigation_dir)
            array = np.load(path, allow_pickle=False)
        except (OSError, ValueError):
            continue
        metadata = event.get("metadata")
        return array, metadata if isinstance(metadata, Mapping) else {}
    return None


def _finish_axis(axis: Any, has_data: bool, *, equal: bool = False) -> None:
    if has_data:
        handles, labels = axis.get_legend_handles_labels()
        if handles:
            axis.legend(fontsize="small")
        axis.grid(alpha=0.2)
        if equal:
            axis.set_aspect("equal", adjustable="datalim")
    else:
        axis.text(
            0.5,
            0.5,
            "INSUFFICIENT DATA",
            transform=axis.transAxes,
            ha="center",
            va="center",
        )


def _in_session(event: Mapping[str, Any], session: SessionWindow) -> bool:
    event_session = event.get("navigation_session_id")
    if isinstance(event_session, str):
        return event_session == session.session_id
    timestamp = _monotonic_ns(event)
    return session.start_monotonic_ns <= timestamp <= session.end_monotonic_ns


def _event_sort_key(event: Mapping[str, Any]) -> tuple[int, str, int]:
    return (
        _monotonic_ns(event),
        str(event.get("producer", "")),
        int(event.get("producer_seq", 0) or 0),
    )


def _monotonic_ns(event: Mapping[str, Any] | None) -> int:
    if event is None:
        return 0
    try:
        return int(event.get("monotonic_ns", 0) or 0)
    except (TypeError, ValueError):
        return 0


def _optional_string(value: Any) -> str | None:
    return str(value) if value is not None else None


def _as_float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _as_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _format_metric(value: Any, unit: str) -> str:
    if value is None:
        return "unknown"
    return f"{float(value):.3f} {unit}"
