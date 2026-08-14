# Copyright 2026 Dimensional Inc.
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

"""Offline checks for a recorded Mid360 navigation-source session.

The analyzer reads only sqlite metadata columns. It never joins the live DimOS
graph and therefore cannot add latency to Point-LIO or robot control.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
from pathlib import Path
import sqlite3
import time
from typing import Any

import numpy as np
from numpy.typing import NDArray

from dimos.msgs.tf2_msgs.TFMessage import TFMessage
from dimos.robot.unitree.go2.go2_mid360_static_transforms import (
    ORIN_NAVIGATION_BASE_TO_IMU,
    ORIN_NAVIGATION_IMU_TO_MID360,
)

_POSE_COLUMNS = "ts, pose_x, pose_y, pose_z, pose_qx, pose_qy, pose_qz, pose_qw"
_REQUIRED_TF_EDGES = {
    ("world", "base_link"),
    ("base_link", "mid360_imu_link"),
    ("mid360_imu_link", "mid360_link"),
    ("base_link", "camera_link"),
    ("camera_link", "camera_optical"),
}


def motion_gate_status(
    db_path: Path,
    *,
    expected_min_radius_m: float,
    expected_min_yaw_span_deg: float,
    settle_window_s: float = 3.0,
    settle_translation_tolerance_m: float = 0.03,
    settle_yaw_tolerance_deg: float = 2.0,
) -> dict[str, Any]:
    """Return lightweight T2 motion and final-settle checks from recorded odom metadata."""
    if not db_path.is_file():
        return {
            "ready": False,
            "reason": "recording database does not exist yet",
            "checks": {},
        }

    try:
        with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=0.2) as connection:
            rows = _load_pose_rows(connection, "odom")
    except sqlite3.Error as error:
        return {"ready": False, "reason": str(error), "checks": {}}

    if rows.shape[0] < 2:
        return {
            "ready": False,
            "reason": "waiting for odom samples",
            "sample_count": int(rows.shape[0]),
            "checks": {},
        }

    pose = _pose_metrics(rows)
    latest_ts = float(rows[-1, 0])
    recent = rows[rows[:, 0] >= latest_ts - settle_window_s]
    recent_pose = _pose_metrics(recent)
    recent_duration_s = float(recent[-1, 0] - recent[0, 0])
    # At 10 Hz the first sample inside a 3 s window may be almost one interval
    # newer than the cutoff. Requiring 90% coverage tolerates that quantization
    # while still preventing a brief pause from being called settled.
    settle_window_covered = recent_duration_s >= settle_window_s * 0.9
    radius = float(pose.get("max_radius_xy_m", 0.0))
    yaw_span = float(pose.get("rpy_deg", {}).get("span", [0.0, 0.0, 0.0])[2])
    settle_radius = float(recent_pose.get("max_radius_xy_m", math.inf))
    settle_yaw_span = float(
        recent_pose.get("rpy_deg", {}).get("span", [math.inf, math.inf, math.inf])[2]
    )
    checks = {
        "expected_translation_observed": radius >= expected_min_radius_m,
        "expected_rotation_observed": yaw_span >= expected_min_yaw_span_deg,
        "settle_window_covered": settle_window_covered,
        "settled_translation": settle_radius <= settle_translation_tolerance_m,
        "settled_rotation": settle_yaw_span <= settle_yaw_tolerance_deg,
    }
    return {
        "ready": True,
        "reason": None,
        "sample_count": int(rows.shape[0]),
        "latest_source_ts": latest_ts,
        "max_radius_xy_m": radius,
        "yaw_span_deg": yaw_span,
        "settle_window_s": settle_window_s,
        "settle_observed_duration_s": recent_duration_s,
        "settle_radius_xy_m": settle_radius,
        "settle_yaw_span_deg": settle_yaw_span,
        "checks": checks,
        "all_checks_passed": all(checks.values()),
    }


def wait_for_motion_gate(
    db_path: Path,
    *,
    expected_min_radius_m: float,
    expected_min_yaw_span_deg: float,
    timeout_s: float,
    poll_interval_s: float = 1.0,
    settle_window_s: float = 3.0,
) -> dict[str, Any]:
    """Wait for T2 motion and settling while requiring a newly committed odom sample."""
    deadline = time.monotonic() + timeout_s
    previous_source_ts: float | None = None
    status: dict[str, Any] = {"ready": False, "reason": "not polled", "checks": {}}
    while True:
        status = motion_gate_status(
            db_path,
            expected_min_radius_m=expected_min_radius_m,
            expected_min_yaw_span_deg=expected_min_yaw_span_deg,
            settle_window_s=settle_window_s,
        )
        current_source_ts = status.get("latest_source_ts")
        source_advanced = (
            current_source_ts is not None
            and previous_source_ts is not None
            and current_source_ts > previous_source_ts
        )
        status["source_advanced_since_previous_poll"] = source_advanced
        if status.get("all_checks_passed") and source_advanced:
            return status
        if current_source_ts is not None:
            previous_source_ts = float(current_source_ts)

        now = time.monotonic()
        if now >= deadline:
            status["timed_out"] = True
            return status
        time.sleep(min(poll_interval_s, deadline - now))


def _percentile(values: NDArray[np.float64], percentile: float) -> float | None:
    if values.size == 0:
        return None
    return float(np.percentile(values, percentile))


def _timestamp_metrics(timestamps: NDArray[np.float64]) -> dict[str, Any]:
    if timestamps.size == 0:
        return {
            "count": 0,
            "duration_s": 0.0,
            "average_hz": None,
            "regressions": 0,
            "duplicates": 0,
            "dt_s": {"p50": None, "p95": None, "max": None},
        }

    intervals = np.diff(timestamps)
    positive = intervals[intervals > 0.0]
    duration = float(timestamps[-1] - timestamps[0])
    return {
        "count": int(timestamps.size),
        "duration_s": duration,
        "average_hz": float((timestamps.size - 1) / duration) if duration > 0.0 else None,
        "regressions": int(np.count_nonzero(intervals < 0.0)),
        "duplicates": int(np.count_nonzero(intervals == 0.0)),
        "dt_s": {
            "p50": _percentile(positive, 50.0),
            "p95": _percentile(positive, 95.0),
            "max": float(np.max(positive)) if positive.size else None,
        },
    }


def _quaternion_to_rpy(quaternions: NDArray[np.float64]) -> NDArray[np.float64]:
    """Convert xyzw quaternions to roll, pitch, yaw without scipy."""
    x = quaternions[:, 0]
    y = quaternions[:, 1]
    z = quaternions[:, 2]
    w = quaternions[:, 3]

    roll = np.arctan2(2.0 * (w * x + y * z), 1.0 - 2.0 * (x * x + y * y))
    pitch_term = np.clip(2.0 * (w * y - z * x), -1.0, 1.0)
    pitch = np.arcsin(pitch_term)
    yaw = np.arctan2(2.0 * (w * z + x * y), 1.0 - 2.0 * (y * y + z * z))
    return np.column_stack((roll, pitch, yaw))


def _pose_metrics(rows: NDArray[np.float64]) -> dict[str, Any]:
    if rows.size == 0:
        return {"pose_count": 0}

    timestamps = rows[:, 0]
    positions = rows[:, 1:4]
    quaternions = rows[:, 4:8]
    position_steps = np.linalg.norm(np.diff(positions, axis=0), axis=1)
    xy_steps = np.linalg.norm(np.diff(positions[:, :2], axis=0), axis=1)
    relative = positions - positions[0]
    q_norm = np.linalg.norm(quaternions, axis=1)
    rpy_deg = np.rad2deg(np.unwrap(_quaternion_to_rpy(quaternions), axis=0))

    intervals = np.diff(timestamps)
    valid_dt = intervals > 0.0
    speed_xy = xy_steps[valid_dt] / intervals[valid_dt]

    return {
        "pose_count": int(rows.shape[0]),
        "start_position_m": positions[0].tolist(),
        "end_position_m": positions[-1].tolist(),
        "net_displacement_m": float(np.linalg.norm(relative[-1])),
        "net_displacement_xy_m": float(np.linalg.norm(relative[-1, :2])),
        "max_radius_m": float(np.max(np.linalg.norm(relative, axis=1))),
        "max_radius_xy_m": float(np.max(np.linalg.norm(relative[:, :2], axis=1))),
        "path_length_m": float(np.sum(position_steps)),
        "path_length_xy_m": float(np.sum(xy_steps)),
        "max_step_m": float(np.max(position_steps)) if position_steps.size else 0.0,
        "speed_xy_mps": {
            "p50": _percentile(speed_xy, 50.0),
            "p95": _percentile(speed_xy, 95.0),
            "max": float(np.max(speed_xy)) if speed_xy.size else None,
        },
        "quaternion_norm": {
            "min": float(np.min(q_norm)),
            "max": float(np.max(q_norm)),
            "outside_1e_3": int(np.count_nonzero(np.abs(q_norm - 1.0) > 1e-3)),
        },
        "rpy_deg": {
            "start": rpy_deg[0].tolist(),
            "end": rpy_deg[-1].tolist(),
            "net": (rpy_deg[-1] - rpy_deg[0]).tolist(),
            "span": (np.max(rpy_deg, axis=0) - np.min(rpy_deg, axis=0)).tolist(),
        },
    }


def _load_pose_rows(connection: sqlite3.Connection, stream: str) -> NDArray[np.float64]:
    tables = {
        str(row[0])
        for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
    }
    if stream not in tables:
        return np.empty((0, 8), dtype=np.float64)
    rows = connection.execute(f'SELECT {_POSE_COLUMNS} FROM "{stream}" ORDER BY id').fetchall()
    if not rows:
        return np.empty((0, 8), dtype=np.float64)
    return np.asarray(rows, dtype=np.float64)


def _nearest_indices(
    source_ts: NDArray[np.float64], target_ts: NDArray[np.float64]
) -> NDArray[np.int64]:
    insertion = np.searchsorted(target_ts, source_ts)
    right = np.clip(insertion, 0, target_ts.size - 1)
    left = np.clip(insertion - 1, 0, target_ts.size - 1)
    use_left = np.abs(source_ts - target_ts[left]) <= np.abs(source_ts - target_ts[right])
    return np.where(use_left, left, right).astype(np.int64)


def _cloud_odom_metrics(
    lidar_rows: NDArray[np.float64], odom_rows: NDArray[np.float64]
) -> dict[str, Any]:
    if lidar_rows.size == 0 or odom_rows.size == 0:
        return {"matched_count": 0}

    indices = _nearest_indices(lidar_rows[:, 0], odom_rows[:, 0])
    matched = odom_rows[indices]
    delta_ts = np.abs(lidar_rows[:, 0] - matched[:, 0])
    position_error = np.linalg.norm(lidar_rows[:, 1:4] - matched[:, 1:4], axis=1)

    lidar_yaw = np.unwrap(_quaternion_to_rpy(lidar_rows[:, 4:8])[:, 2])
    odom_yaw = np.unwrap(_quaternion_to_rpy(matched[:, 4:8])[:, 2])
    yaw_error_deg = np.abs(
        np.rad2deg(np.arctan2(np.sin(lidar_yaw - odom_yaw), np.cos(lidar_yaw - odom_yaw)))
    )

    return {
        "matched_count": int(delta_ts.size),
        "timestamp_error_s": {
            "p50": _percentile(delta_ts, 50.0),
            "p95": _percentile(delta_ts, 95.0),
            "max": float(np.max(delta_ts)),
        },
        "attached_pose_error_m": {
            "p95": _percentile(position_error, 95.0),
            "max": float(np.max(position_error)),
        },
        "attached_yaw_error_deg": {
            "p95": _percentile(yaw_error_deg, 95.0),
            "max": float(np.max(yaw_error_deg)),
        },
    }


def _transform_values(transform: Any) -> tuple[NDArray[np.float64], NDArray[np.float64]]:
    translation = np.asarray(
        [transform.translation.x, transform.translation.y, transform.translation.z],
        dtype=np.float64,
    )
    rotation = np.asarray(
        [transform.rotation.x, transform.rotation.y, transform.rotation.z, transform.rotation.w],
        dtype=np.float64,
    )
    return translation, rotation


def _expected_quaternion(rpy: tuple[float, float, float]) -> NDArray[np.float64]:
    roll, pitch, yaw = rpy
    cr, sr = math.cos(roll / 2.0), math.sin(roll / 2.0)
    cp, sp = math.cos(pitch / 2.0), math.sin(pitch / 2.0)
    cy, sy = math.cos(yaw / 2.0), math.sin(yaw / 2.0)
    return np.asarray(
        [
            sr * cp * cy - cr * sp * sy,
            cr * sp * cy + sr * cp * sy,
            cr * cp * sy - sr * sp * cy,
            cr * cp * cy + sr * sp * sy,
        ],
        dtype=np.float64,
    )


def _tf_tree_metrics(connection: sqlite3.Connection) -> dict[str, Any]:
    tables = {
        str(row[0])
        for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
    }
    if "tf_blob" not in tables:
        return {
            "message_count": 0,
            "edges": [],
            "parents_by_child": {},
            "multi_parent_children": {},
            "missing_required_edges": sorted(f"{p}->{c}" for p, c in _REQUIRED_TF_EDGES),
            "mount_profile": {"matches": False, "reason": "tf_blob is missing"},
        }

    edge_transforms: dict[tuple[str, str], Any] = {}
    parents_by_child: dict[str, set[str]] = {}
    message_count = 0
    for (payload,) in connection.execute('SELECT data FROM "tf_blob" ORDER BY id'):
        message_count += 1
        message = TFMessage.lcm_decode(bytes(payload))
        for transform in message.transforms:
            edge = (transform.frame_id, transform.child_frame_id)
            edge_transforms.setdefault(edge, transform)
            parents_by_child.setdefault(transform.child_frame_id, set()).add(transform.frame_id)

    edges = set(edge_transforms)
    multi_parent = {
        child: sorted(parents) for child, parents in parents_by_child.items() if len(parents) > 1
    }
    expected_mounts = {
        ("base_link", "mid360_imu_link"): ORIN_NAVIGATION_BASE_TO_IMU,
        ("mid360_imu_link", "mid360_link"): ORIN_NAVIGATION_IMU_TO_MID360,
    }
    mount_errors: dict[str, dict[str, float]] = {}
    for edge, (expected_translation, expected_rpy) in expected_mounts.items():
        recorded_transform = edge_transforms.get(edge)
        edge_name = f"{edge[0]}->{edge[1]}"
        if recorded_transform is None:
            mount_errors[edge_name] = {
                "translation_max_abs_m": math.inf,
                "quaternion_max_abs": math.inf,
            }
            continue
        translation, quaternion = _transform_values(recorded_transform)
        expected_q = _expected_quaternion(expected_rpy)
        quaternion_error = min(
            float(np.max(np.abs(quaternion - expected_q))),
            float(np.max(np.abs(quaternion + expected_q))),
        )
        mount_errors[edge_name] = {
            "translation_max_abs_m": float(
                np.max(np.abs(translation - np.asarray(expected_translation)))
            ),
            "quaternion_max_abs": quaternion_error,
        }

    mount_matches = all(
        values["translation_max_abs_m"] <= 1e-6 and values["quaternion_max_abs"] <= 1e-6
        for values in mount_errors.values()
    )
    return {
        "message_count": message_count,
        "edges": sorted(f"{parent}->{child}" for parent, child in edges),
        "parents_by_child": {
            child: sorted(parents) for child, parents in sorted(parents_by_child.items())
        },
        "multi_parent_children": multi_parent,
        "missing_required_edges": sorted(
            f"{parent}->{child}" for parent, child in _REQUIRED_TF_EDGES - edges
        ),
        "mount_profile": {
            "matches": mount_matches,
            "tolerance": 1e-6,
            "errors": mount_errors,
        },
    }


def analyze_recording(
    db_path: Path,
    *,
    max_cloud_odom_dt_s: float = 0.10,
    expected_min_radius_m: float = 0.0,
    expected_min_yaw_span_deg: float = 0.0,
) -> dict[str, Any]:
    """Analyze one navigation recording and return metrics plus objective gates."""
    if not db_path.is_file():
        raise FileNotFoundError(db_path)

    with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True) as connection:
        odom_rows = _load_pose_rows(connection, "odom")
        lidar_rows = _load_pose_rows(connection, "lidar")
        tf_rows = _load_pose_rows(connection, "tf")
        tf_tree = _tf_tree_metrics(connection)

    timestamp = {
        "odom": _timestamp_metrics(odom_rows[:, 0]),
        "lidar": _timestamp_metrics(lidar_rows[:, 0]),
        "tf": _timestamp_metrics(tf_rows[:, 0]),
    }
    odom_pose = _pose_metrics(odom_rows)
    cloud_odom = _cloud_odom_metrics(lidar_rows, odom_rows)

    cloud_dt_max = cloud_odom.get("timestamp_error_s", {}).get("max")
    checks = {
        "odom_present": bool(odom_rows.size),
        "lidar_present": bool(lidar_rows.size),
        "odom_timestamp_monotonic": timestamp["odom"]["regressions"] == 0
        and timestamp["odom"]["duplicates"] == 0,
        "lidar_timestamp_monotonic": timestamp["lidar"]["regressions"] == 0
        and timestamp["lidar"]["duplicates"] == 0,
        "odom_quaternion_valid": odom_pose.get("quaternion_norm", {}).get("outside_1e_3", 1) == 0,
        "cloud_odom_time_aligned": cloud_dt_max is not None and cloud_dt_max <= max_cloud_odom_dt_s,
        "tf_present": tf_tree["message_count"] > 0,
        "tf_single_parent": not tf_tree["multi_parent_children"],
        "tf_required_edges_present": not tf_tree["missing_required_edges"],
        "tf_mount_matches_profile": tf_tree["mount_profile"]["matches"],
        "expected_translation_observed": odom_pose.get("max_radius_xy_m", 0.0)
        >= expected_min_radius_m,
        "expected_rotation_observed": odom_pose.get("rpy_deg", {}).get("span", [0.0, 0.0, 0.0])[2]
        >= expected_min_yaw_span_deg,
    }

    return {
        "schema_version": 1,
        "recording": str(db_path.resolve()),
        "thresholds": {
            "max_cloud_odom_dt_s": max_cloud_odom_dt_s,
            "expected_min_radius_m": expected_min_radius_m,
            "expected_min_yaw_span_deg": expected_min_yaw_span_deg,
        },
        "timestamp": timestamp,
        "odom_pose": odom_pose,
        "cloud_odom": cloud_odom,
        "tf_tree": tf_tree,
        "checks": checks,
        "all_checks_passed": all(checks.values()),
        "manual_checks_required": [
            "Observed push direction agrees with the odom axes shown in Rerun.",
            "World cloud remains stationary while the robot is translated and rotated.",
            "Floor is level and straight walls do not split into double surfaces.",
        ],
    }


def _write_odom_csv(db_path: Path, output_path: Path) -> None:
    with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True) as connection:
        rows = _load_pose_rows(connection, "odom")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", newline="", encoding="utf-8") as file:
        writer = csv.writer(file)
        writer.writerow(("ts", "x", "y", "z", "qx", "qy", "qz", "qw"))
        writer.writerows(rows.tolist())


def _format_optional(value: Any, digits: int = 4) -> str:
    return "n/a" if value is None else f"{float(value):.{digits}f}"


def _print_summary(report: dict[str, Any]) -> None:
    odom = report["timestamp"]["odom"]
    lidar = report["timestamp"]["lidar"]
    pose = report["odom_pose"]
    alignment = report["cloud_odom"].get("timestamp_error_s", {})
    print(f"recording: {report['recording']}")
    print(
        "odom: "
        f"count={odom['count']} hz={_format_optional(odom['average_hz'], 2)} "
        f"regressions={odom['regressions']} duplicates={odom['duplicates']}"
    )
    print(
        "lidar: "
        f"count={lidar['count']} hz={_format_optional(lidar['average_hz'], 2)} "
        f"regressions={lidar['regressions']} duplicates={lidar['duplicates']}"
    )
    print(
        "trajectory: "
        f"max_radius_xy={_format_optional(pose.get('max_radius_xy_m'))}m "
        f"path_xy={_format_optional(pose.get('path_length_xy_m'))}m "
        f"yaw_span={_format_optional(pose.get('rpy_deg', {}).get('span', [None] * 3)[2], 2)}deg"
    )
    print(
        "cloud/odom timestamp error: "
        f"p95={_format_optional(alignment.get('p95'), 6)}s "
        f"max={_format_optional(alignment.get('max'), 6)}s"
    )
    tf_tree = report["tf_tree"]
    print(
        "tf tree: "
        f"messages={tf_tree['message_count']} edges={len(tf_tree['edges'])} "
        f"multi_parent={len(tf_tree['multi_parent_children'])} "
        f"missing={len(tf_tree['missing_required_edges'])}"
    )
    for name, passed in report["checks"].items():
        print(f"[{'PASS' if passed else 'FAIL'}] {name}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("db", type=Path, help="Recorded navigation.db")
    parser.add_argument("--out", type=Path, help="JSON report path")
    parser.add_argument("--csv", type=Path, help="Odom CSV path")
    parser.add_argument("--max-cloud-odom-dt-s", type=float, default=0.10)
    parser.add_argument("--expected-min-radius-m", type=float, default=0.0)
    parser.add_argument("--expected-min-yaw-span-deg", type=float, default=0.0)
    parser.add_argument(
        "--wait-for-motion",
        action="store_true",
        help="Wait for motion thresholds and a stable final pose without decoding point clouds or TF",
    )
    parser.add_argument("--motion-timeout-s", type=float, default=180.0)
    parser.add_argument("--motion-poll-interval-s", type=float, default=1.0)
    parser.add_argument("--motion-settle-window-s", type=float, default=3.0)
    args = parser.parse_args(argv)

    if args.wait_for_motion:
        status = wait_for_motion_gate(
            args.db,
            expected_min_radius_m=args.expected_min_radius_m,
            expected_min_yaw_span_deg=args.expected_min_yaw_span_deg,
            timeout_s=args.motion_timeout_s,
            poll_interval_s=args.motion_poll_interval_s,
            settle_window_s=args.motion_settle_window_s,
        )
        print(json.dumps(status, ensure_ascii=False))
        return 0 if status.get("all_checks_passed") else 3

    output = args.out or args.db.with_name("t2-analysis.json")
    csv_output = args.csv or args.db.with_name("t2-odom.csv")
    report = analyze_recording(
        args.db,
        max_cloud_odom_dt_s=args.max_cloud_odom_dt_s,
        expected_min_radius_m=args.expected_min_radius_m,
        expected_min_yaw_span_deg=args.expected_min_yaw_span_deg,
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    _write_odom_csv(args.db, csv_output)
    _print_summary(report)
    print(f"json: {output}")
    print(f"csv:  {csv_output}")
    return 0 if report["all_checks_passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
