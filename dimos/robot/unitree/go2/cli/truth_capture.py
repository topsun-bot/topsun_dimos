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

"""Go2 truth-data capture helpers.

The capture command wraps the existing ``unitree-go2-memory`` blueprint so field
operators do not need to remember the long ``dimos run`` command. The resulting
SQLite DB is intended to be the benchmark truth source for replay/trajectory
comparison; Rerun recordings are useful evidence, but not the ground truth.
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
import math
from pathlib import Path
import re
import shlex
import signal
import sqlite3
import subprocess
import sys
import time
from typing import Any, Literal

import typer

ViewerBackend = Literal["rerun", "foxglove", "none"]

REQUIRED_STREAMS = ("odom", "lidar", "color_image")
DEFAULT_MIN_COUNTS = {
    "odom": 100,
    "lidar": 30,
    "color_image": 30,
}

app = typer.Typer(
    help="Capture and validate Go2 teleop truth DBs for replay benchmarks.",
    no_args_is_help=True,
)


def _slugify(value: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9_.-]+", "-", value.strip()).strip("-")
    return slug or "office-loop"


def _quote_identifier(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _yaw_from_quaternion(qx: float, qy: float, qz: float, qw: float) -> float:
    """Return planar yaw in radians for an ``(x, y, z, w)`` quaternion."""
    norm = math.sqrt(qx * qx + qy * qy + qz * qz + qw * qw)
    if norm == 0:
        raise ValueError("zero-norm quaternion")
    qx, qy, qz, qw = qx / norm, qy / norm, qz / norm, qw / norm
    sin_yaw = 2.0 * (qw * qz + qx * qy)
    cos_yaw = 1.0 - 2.0 * (qy * qy + qz * qz)
    return math.atan2(sin_yaw, cos_yaw)


def _utc_timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")


def default_db_path(route: str, out_dir: Path) -> Path:
    return out_dir / f"go2_{_slugify(route)}_{_utc_timestamp()}.db"


def build_capture_command(
    *,
    robot_ip: str,
    db_path: Path,
    viewer: ViewerBackend,
    overwrite: bool,
    save_rerun: bool,
    rrd_dir: Path | None,
) -> list[str]:
    cmd = [
        sys.executable,
        "-m",
        "dimos.robot.cli.dimos",
        "--robot-ip",
        robot_ip,
        "--viewer",
        viewer,
    ]

    if save_rerun:
        cmd.append("--rerun-save")
        if rrd_dir is not None:
            cmd.extend(["--rerun-save-dir", str(rrd_dir)])

    cmd.extend(
        [
            "run",
            "unitree-go2-memory",
            "--option",
            f"go2memory.db_path={db_path}",
            "--option",
            f"go2memory.overwrite={str(overwrite).lower()}",
        ]
    )
    return cmd


def build_capture_manifest(
    *,
    route: str,
    robot_ip: str,
    db_path: Path,
    manifest_path: Path,
    viewer: ViewerBackend,
    save_rerun: bool,
    rrd_dir: Path | None,
    scene: str,
    task_text: str,
    teleop_user: str,
    map_ref: str,
    command: list[str],
    robot_variant: str | None = None,
) -> dict[str, Any]:
    """Build the shared CLI/GUI capture metadata contract."""
    manifest: dict[str, Any] = {
        "route": route,
        "robot": "go2",
        "operator_mode": "manual_remote",
        "robot_ip": robot_ip,
        "db_path": str(db_path),
        "manifest_path": str(manifest_path),
        "viewer": viewer,
        "save_rerun": save_rerun,
        "rrd_dir": str(rrd_dir) if rrd_dir is not None else None,
        "scene": scene,
        "task_text": task_text,
        "teleop_user": teleop_user,
        "map_ref": map_ref,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "command": command,
        "notes": [
            "Drive one full route lap with the handheld remote.",
            "Stop after the lap; the tool will stop DimOS and inspect the DB.",
            "The DB is the benchmark truth source. Rerun output is visual evidence only.",
        ],
    }
    if robot_variant:
        manifest["robot_variant"] = robot_variant
    return manifest


def inspect_truth_db(
    db_path: Path,
    *,
    min_duration_s: float,
    min_counts: dict[str, int] | None = None,
    required_streams: tuple[str, ...] = REQUIRED_STREAMS,
) -> dict[str, Any]:
    min_counts = DEFAULT_MIN_COUNTS if min_counts is None else min_counts
    if min_duration_s < 0:
        raise ValueError("min_duration_s must be non-negative")
    negative_streams = [name for name, count in min_counts.items() if count < 0]
    if negative_streams:
        raise ValueError(
            "minimum stream counts must be non-negative: " + ", ".join(negative_streams)
        )
    summary: dict[str, Any] = {
        "db_path": str(db_path),
        "ok": False,
        "missing_streams": [],
        "failed_checks": [],
        "streams": {},
    }

    if not db_path.exists():
        summary["failed_checks"].append(f"DB does not exist: {db_path}")
        return summary

    try:
        conn = sqlite3.connect(str(db_path))
    except sqlite3.Error as exc:
        summary["failed_checks"].append(f"Could not open DB: {exc}")
        return summary
    try:
        stream_rows = conn.execute("SELECT name FROM _streams ORDER BY name").fetchall()
        streams = [row[0] for row in stream_rows]
        summary["available_streams"] = streams

        for stream_name in streams:
            quoted = _quote_identifier(stream_name)
            try:
                row = conn.execute(
                    f"SELECT COUNT(*), MIN(ts), MAX(ts), "
                    f"SUM(CASE WHEN pose_x IS NOT NULL THEN 1 ELSE 0 END) FROM {quoted}"
                ).fetchone()
            except sqlite3.Error as exc:
                summary["streams"][stream_name] = {"error": str(exc)}
                summary["failed_checks"].append(f"Could not inspect stream {stream_name}: {exc}")
                continue

            count = int(row[0] or 0)
            first_ts = row[1]
            last_ts = row[2]
            duration_s = (
                float(last_ts - first_ts) if first_ts is not None and last_ts is not None else 0.0
            )
            summary["streams"][stream_name] = {
                "count": count,
                "first_ts": first_ts,
                "last_ts": last_ts,
                "duration_s": duration_s,
                "pose_count": int(row[3] or 0),
            }

        missing_streams = [name for name in required_streams if name not in streams]
        summary["missing_streams"] = missing_streams
        for stream_name in missing_streams:
            summary["failed_checks"].append(f"Missing required stream: {stream_name}")

        for stream_name, minimum in min_counts.items():
            if stream_name not in streams:
                continue
            stream_summary = summary["streams"][stream_name]
            if "error" in stream_summary:
                continue
            count = int(stream_summary["count"])
            if count < minimum:
                summary["failed_checks"].append(
                    f"{stream_name} count {count} is below minimum {minimum}"
                )

        odom_summary = summary["streams"].get("odom", {})
        if "odom" in streams and "error" not in odom_summary:
            odom_duration = float(odom_summary["duration_s"])
            summary["duration_s"] = odom_duration
            if odom_duration < min_duration_s:
                summary["failed_checks"].append(
                    f"odom duration {odom_duration:.1f}s is below minimum {min_duration_s:.1f}s"
                )
            if int(odom_summary["pose_count"]) == 0:
                summary["failed_checks"].append("odom contains no positioned observations")

            try:
                first_pose = conn.execute(
                    "SELECT pose_x, pose_y, pose_z, pose_qx, pose_qy, pose_qz, pose_qw "
                    'FROM "odom" '
                    "WHERE pose_x IS NOT NULL ORDER BY ts ASC LIMIT 1"
                ).fetchone()
            except sqlite3.Error as exc:
                summary["failed_checks"].append(f"Could not read odom start pose: {exc}")
                first_pose = None
            if first_pose is not None:
                position = first_pose[:3]
                if any(value is None for value in position):
                    summary["failed_checks"].append("odom start pose is missing position values")
                    start_pose = {}
                else:
                    start_pose = {
                        "x": float(first_pose[0]),
                        "y": float(first_pose[1]),
                        "z": float(first_pose[2]),
                    }
                quaternion = first_pose[3:7]
                if all(value is not None for value in quaternion):
                    qx, qy, qz, qw = (float(value) for value in quaternion)
                    start_pose.update({"qx": qx, "qy": qy, "qz": qz, "qw": qw})
                    try:
                        yaw_rad = _yaw_from_quaternion(qx, qy, qz, qw)
                    except ValueError as exc:
                        summary["failed_checks"].append(f"Invalid odom start quaternion: {exc}")
                    else:
                        start_pose["yaw_rad"] = yaw_rad
                        start_pose["yaw_deg"] = math.degrees(yaw_rad)
                else:
                    summary["failed_checks"].append("odom start pose is missing quaternion values")
                summary["start_pose"] = start_pose
        else:
            summary["duration_s"] = 0.0

        summary["ok"] = not summary["failed_checks"]
        return summary
    except sqlite3.Error as exc:
        summary["failed_checks"].append(f"Could not inspect DB: {exc}")
        return summary
    finally:
        conn.close()


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _print_check_summary(summary: dict[str, Any]) -> None:
    status = "PASS" if summary.get("ok") else "FAIL"
    typer.echo(f"Truth DB check: {status}")
    typer.echo(f"  DB: {summary['db_path']}")
    typer.echo(f"  Duration: {float(summary.get('duration_s', 0.0)):.1f}s")

    streams = summary.get("streams", {})
    if streams:
        typer.echo("  Streams:")
        for name, info in sorted(streams.items()):
            if "error" in info:
                typer.echo(f"    {name}: error={info['error']}")
                continue
            typer.echo(
                f"    {name}: count={info['count']}, "
                f"duration={float(info['duration_s']):.1f}s, pose_count={info['pose_count']}"
            )

    failed = summary.get("failed_checks", [])
    if failed:
        typer.echo("  Failed checks:", err=True)
        for item in failed:
            typer.echo(f"    - {item}", err=True)


def _terminate_process(proc: subprocess.Popen[Any]) -> None:
    if proc.poll() is not None:
        return

    proc.send_signal(signal.SIGTERM)
    try:
        proc.wait(timeout=15)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=5)


@app.command("capture")
def capture(
    robot_ip: str = typer.Option(..., "--robot-ip", help="Go2 robot IP address"),
    route: str = typer.Option(
        "office_loop",
        "--route",
        "-r",
        help="Route label used in output filenames, e.g. office_loop_north_gate",
    ),
    out_dir: Path = typer.Option(
        Path("data/.lfs/extracted"),
        "--out-dir",
        help="Directory for truth DB and manifest files (default aligns with existing LFS dbs).",
    ),
    db: Path | None = typer.Option(
        None,
        "--db",
        help="Exact DB output path. Defaults to <out-dir>/go2_<route>_<timestamp>.db",
    ),
    viewer: ViewerBackend = typer.Option(
        "rerun",
        "--viewer",
        help="Viewer backend to run while capturing",
    ),
    duration_s: float | None = typer.Option(
        None,
        "--duration-s",
        min=0.1,
        help="Stop automatically after this many seconds. Omit to stop with Ctrl+C.",
    ),
    force: bool = typer.Option(False, "--force", help="Overwrite an existing DB path"),
    save_rerun: bool = typer.Option(
        False,
        "--save-rerun/--no-save-rerun",
        help="Also save a Rerun .rrd recording for visual evidence",
    ),
    rrd_dir: Path | None = typer.Option(
        None,
        "--rrd-dir",
        help="Directory for .rrd files when --save-rerun is set",
    ),
    scene: str = typer.Option(
        "",
        "--scene",
        help="Physical location label, e.g. shanghai_office, bigoffice (PROJECT_PLAN §5).",
    ),
    task: str = typer.Option(
        "",
        "--task",
        help="Natural-language task description, e.g. '绕楼一圈检查门是否关着'.",
    ),
    user: str = typer.Option(
        "",
        "--user",
        help="Operator running the teleop (free-form identifier, e.g. zhang).",
    ),
    map_ref: str = typer.Option(
        "",
        "--map-ref",
        help="Reference map artifact filename if a prior map is loaded (e.g. unitree_go2_bigoffice_map.pickle).",
    ),
    min_duration_s: float = typer.Option(
        30.0,
        "--min-duration-s",
        min=0.0,
        help="Minimum odom duration required by the post-capture check",
    ),
    min_odom: int = typer.Option(100, "--min-odom", min=0, help="Minimum odom messages"),
    min_lidar: int = typer.Option(30, "--min-lidar", min=0, help="Minimum lidar messages"),
    min_images: int = typer.Option(30, "--min-images", min=0, help="Minimum color_image messages"),
    skip_check: bool = typer.Option(False, "--skip-check", help="Do not inspect the DB after stop"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Print the underlying command only"),
) -> None:
    """Capture a human-teleoperated Go2 office-loop truth DB.

    Start this command, drive the robot around the route with the handheld
    remote, then press Ctrl+C after the lap is complete. The DB is written by
    the existing ``Go2Memory`` recorder inside ``unitree-go2-memory``.
    """

    db_path = (db or default_db_path(route, out_dir)).expanduser().resolve()
    db_path.parent.mkdir(parents=True, exist_ok=True)

    if db_path.exists() and not force:
        typer.echo(f"DB already exists: {db_path}. Use --force or choose --db.", err=True)
        raise typer.Exit(1)

    resolved_rrd_dir = rrd_dir.expanduser().resolve() if rrd_dir is not None else None
    if resolved_rrd_dir is not None:
        resolved_rrd_dir.mkdir(parents=True, exist_ok=True)

    cmd = build_capture_command(
        robot_ip=robot_ip,
        db_path=db_path,
        viewer=viewer,
        overwrite=force,
        save_rerun=save_rerun,
        rrd_dir=resolved_rrd_dir,
    )

    typer.echo("Underlying command:")
    typer.echo("  " + shlex.join(cmd))
    if dry_run:
        return

    manifest_path = db_path.with_suffix(".manifest.json")
    manifest = build_capture_manifest(
        route=route,
        robot_ip=robot_ip,
        db_path=db_path,
        manifest_path=manifest_path,
        viewer=viewer,
        save_rerun=save_rerun,
        rrd_dir=resolved_rrd_dir,
        scene=scene,
        task_text=task,
        teleop_user=user,
        map_ref=map_ref,
        command=cmd,
    )
    _write_json(manifest_path, manifest)

    typer.echo("")
    typer.echo("Capture is running.")
    typer.echo("  1. Wait until Go2 is standing and streams are live.")
    typer.echo("  2. Use the handheld remote to drive one complete office-loop route.")
    typer.echo("  3. Press Ctrl+C here after the lap is finished.")
    typer.echo("")

    try:
        proc = subprocess.Popen(cmd)
    except OSError as exc:
        manifest["finished_at"] = datetime.now(timezone.utc).isoformat()
        manifest["return_code"] = None
        manifest["start_error"] = str(exc)
        manifest["check_ok"] = False
        manifest["failed_checks"] = [f"Capture process failed to start: {exc}"]
        _write_json(manifest_path, manifest)
        typer.echo(f"Could not start capture process: {exc}", err=True)
        raise typer.Exit(1) from exc
    try:
        if duration_s is None:
            return_code = proc.wait()
        else:
            deadline = time.monotonic() + duration_s
            while proc.poll() is None and time.monotonic() < deadline:
                time.sleep(0.5)
            _terminate_process(proc)
            return_code = proc.returncode
    except KeyboardInterrupt:
        typer.echo("\nStopping capture...")
        _terminate_process(proc)
        return_code = proc.returncode

    manifest["finished_at"] = datetime.now(timezone.utc).isoformat()
    manifest["return_code"] = return_code
    _write_json(manifest_path, manifest)

    typer.echo(f"Capture stopped. DB: {db_path}")
    typer.echo(f"Manifest: {manifest_path}")

    if skip_check:
        return

    summary = inspect_truth_db(
        db_path,
        min_duration_s=min_duration_s,
        min_counts={
            "odom": min_odom,
            "lidar": min_lidar,
            "color_image": min_images,
        },
    )
    _print_check_summary(summary)
    check_path = db_path.with_suffix(".check.json")
    _write_json(check_path, summary)
    manifest["check_path"] = str(check_path)
    manifest["check_ok"] = bool(summary["ok"])
    manifest["duration_s"] = float(summary.get("duration_s", 0.0))
    manifest["start_pose"] = summary.get("start_pose")
    manifest["failed_checks"] = summary.get("failed_checks", [])
    _write_json(manifest_path, manifest)

    if not summary["ok"]:
        raise typer.Exit(2)


@app.command("check")
def check(
    db: Path = typer.Argument(..., help="Truth DB to inspect"),
    min_duration_s: float = typer.Option(
        30.0,
        "--min-duration-s",
        min=0.0,
        help="Minimum odom duration required",
    ),
    min_odom: int = typer.Option(100, "--min-odom", min=0, help="Minimum odom messages"),
    min_lidar: int = typer.Option(30, "--min-lidar", min=0, help="Minimum lidar messages"),
    min_images: int = typer.Option(30, "--min-images", min=0, help="Minimum color_image messages"),
    json_output: bool = typer.Option(False, "--json", help="Print machine-readable JSON"),
) -> None:
    """Inspect whether a captured Go2 truth DB has enough benchmark data."""

    summary = inspect_truth_db(
        db.expanduser().resolve(),
        min_duration_s=min_duration_s,
        min_counts={
            "odom": min_odom,
            "lidar": min_lidar,
            "color_image": min_images,
        },
    )

    if json_output:
        typer.echo(json.dumps(summary, indent=2, ensure_ascii=False))
    else:
        _print_check_summary(summary)

    if not summary["ok"]:
        raise typer.Exit(2)
