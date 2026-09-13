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
import math
from pathlib import Path
import sqlite3
from unittest.mock import MagicMock

import pytest
from typer.testing import CliRunner

from dimos.robot.unitree.go2.cli import (
    truth_capture as capture_module,
    truth_capture_gui as gui_module,
)
from dimos.robot.unitree.go2.cli.truth_capture import (
    app as truth_app,
    build_capture_command,
    build_capture_manifest,
    inspect_truth_db,
)
from dimos.robot.unitree.go2.cli.truth_capture_gui import TruthCaptureGui


def _create_stream(
    conn: sqlite3.Connection, name: str, count: int, start_ts: float = 1_000.0
) -> None:
    conn.execute("INSERT INTO _streams (name, config) VALUES (?, '{}')", (name,))
    conn.execute(
        f'CREATE TABLE "{name}" ('
        "id INTEGER PRIMARY KEY AUTOINCREMENT,"
        "ts REAL NOT NULL UNIQUE,"
        "pose_x REAL, pose_y REAL, pose_z REAL,"
        "pose_qx REAL, pose_qy REAL, pose_qz REAL, pose_qw REAL"
        ")"
    )
    for i in range(count):
        conn.execute(
            f'INSERT INTO "{name}" '
            "(ts, pose_x, pose_y, pose_z, pose_qx, pose_qy, pose_qz, pose_qw) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (start_ts + i, float(i), 0.0, 0.3, 0.0, 0.0, 0.0, 1.0),
        )


def test_build_capture_command_orders_global_flags_before_run(tmp_path: Path) -> None:
    db_path = tmp_path / "truth.db"

    cmd = build_capture_command(
        robot_ip="192.168.123.161",
        db_path=db_path,
        viewer="rerun",
        overwrite=False,
        save_rerun=True,
        rrd_dir=tmp_path / "rrd",
    )

    run_index = cmd.index("run")
    assert cmd[run_index + 1] == "unitree-go2-memory"
    assert cmd.index("--robot-ip") < run_index
    assert cmd.index("--viewer") < run_index
    assert "--rerun-save" in cmd[:run_index]
    assert f"go2memory.db_path={db_path}" in cmd
    assert "go2memory.overwrite=false" in cmd


def test_inspect_truth_db_passes_with_required_streams(tmp_path: Path) -> None:
    db_path = tmp_path / "truth.db"
    conn = sqlite3.connect(db_path)
    conn.execute("CREATE TABLE _streams (name TEXT PRIMARY KEY, config TEXT NOT NULL)")
    _create_stream(conn, "odom", count=5)
    _create_stream(conn, "lidar", count=3)
    _create_stream(conn, "color_image", count=3)
    conn.commit()
    conn.close()

    summary = inspect_truth_db(
        db_path,
        min_duration_s=4.0,
        min_counts={"odom": 5, "lidar": 3, "color_image": 3},
    )

    assert summary["ok"] is True
    assert summary["streams"]["odom"]["count"] == 5
    assert summary["duration_s"] == 4.0


def test_inspect_truth_db_fails_when_required_stream_missing(tmp_path: Path) -> None:
    db_path = tmp_path / "truth.db"
    conn = sqlite3.connect(db_path)
    conn.execute("CREATE TABLE _streams (name TEXT PRIMARY KEY, config TEXT NOT NULL)")
    _create_stream(conn, "odom", count=5)
    conn.commit()
    conn.close()

    summary = inspect_truth_db(
        db_path,
        min_duration_s=1.0,
        min_counts={"odom": 1, "lidar": 1, "color_image": 1},
    )

    assert summary["ok"] is False
    assert "lidar" in summary["missing_streams"]
    assert "color_image" in summary["missing_streams"]


def test_inspect_truth_db_reports_malformed_stream_without_crashing(tmp_path: Path) -> None:
    db_path = tmp_path / "truth.db"
    conn = sqlite3.connect(db_path)
    conn.execute("CREATE TABLE _streams (name TEXT PRIMARY KEY, config TEXT NOT NULL)")
    conn.execute("INSERT INTO _streams (name, config) VALUES ('odom', '{}')")
    conn.execute("CREATE TABLE odom (ts REAL NOT NULL)")
    conn.execute("INSERT INTO odom (ts) VALUES (1000.0)")
    conn.commit()
    conn.close()

    summary = inspect_truth_db(
        db_path,
        min_duration_s=0.0,
        min_counts={"odom": 1},
        required_streams=("odom",),
    )

    assert summary["ok"] is False
    assert "error" in summary["streams"]["odom"]
    assert any("Could not inspect stream odom" in item for item in summary["failed_checks"])


def test_inspect_truth_db_exports_start_yaw(tmp_path: Path) -> None:
    db_path = tmp_path / "truth.db"
    conn = sqlite3.connect(db_path)
    conn.execute("CREATE TABLE _streams (name TEXT PRIMARY KEY, config TEXT NOT NULL)")
    conn.execute("INSERT INTO _streams (name, config) VALUES ('odom', '{}')")
    conn.execute(
        "CREATE TABLE odom ("
        "ts REAL NOT NULL UNIQUE, pose_x REAL, pose_y REAL, pose_z REAL, "
        "pose_qx REAL, pose_qy REAL, pose_qz REAL, pose_qw REAL)"
    )
    half_angle = math.radians(90.0) / 2.0
    conn.execute(
        "INSERT INTO odom VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (1000.0, 1.0, 2.0, 0.3, 0.0, 0.0, math.sin(half_angle), math.cos(half_angle)),
    )
    conn.commit()
    conn.close()

    summary = inspect_truth_db(
        db_path,
        min_duration_s=0.0,
        min_counts={"odom": 1},
        required_streams=("odom",),
    )

    assert summary["ok"] is True
    assert summary["start_pose"]["yaw_deg"] == pytest.approx(90.0)


def test_gui_launch_failure_does_not_leave_process(monkeypatch: pytest.MonkeyPatch) -> None:
    gui = object.__new__(TruthCaptureGui)
    gui._process = None

    def fail_to_start(*args: object, **kwargs: object) -> None:
        raise OSError("missing executable")

    monkeypatch.setattr(gui_module.subprocess, "Popen", fail_to_start)

    error = gui._launch_capture_process(["missing-command"])

    assert error == "missing executable"
    assert gui._process is None


def test_capture_manifest_has_codegen_contract(tmp_path: Path) -> None:
    db_path = tmp_path / "truth.db"
    manifest_path = tmp_path / "truth.manifest.json"

    manifest = build_capture_manifest(
        route="office_loop",
        robot_ip="192.168.123.161",
        db_path=db_path,
        manifest_path=manifest_path,
        viewer="none",
        save_rerun=False,
        rrd_dir=None,
        scene="shanghai_office",
        task_text="inspect doors",
        teleop_user="zhang",
        map_ref="office_map.pickle",
        command=["dimos", "run"],
        robot_variant="go2-w",
    )

    assert manifest["robot"] == "go2"
    assert manifest["robot_variant"] == "go2-w"
    assert manifest["scene"] == "shanghai_office"
    assert manifest["task_text"] == "inspect doors"
    assert manifest["teleop_user"] == "zhang"
    assert manifest["map_ref"] == "office_map.pickle"
    assert manifest["db_path"] == str(db_path)


def test_gui_db_check_uses_start_time_threshold_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    gui = object.__new__(TruthCaptureGui)
    gui._db_path = tmp_path / "truth.db"
    gui._check_thresholds = (12.0, 100, 30, 20)
    gui._write_json = MagicMock()
    gui._append_log = MagicMock()
    captured: dict[str, object] = {}

    def fake_inspect(
        db_path: Path, *, min_duration_s: float, min_counts: dict[str, int]
    ) -> dict[str, object]:
        captured.update(
            db_path=db_path,
            min_duration_s=min_duration_s,
            min_counts=min_counts,
        )
        return {"ok": True}

    monkeypatch.setattr(gui_module, "inspect_truth_db", fake_inspect)

    assert gui._check_db() == {"ok": True}
    assert captured == {
        "db_path": gui._db_path,
        "min_duration_s": 12.0,
        "min_counts": {"odom": 100, "lidar": 30, "color_image": 20},
    }


def test_gui_missing_threshold_snapshot_still_writes_failed_check(tmp_path: Path) -> None:
    gui = object.__new__(TruthCaptureGui)
    gui._db_path = tmp_path / "truth.db"
    gui._check_thresholds = None
    gui._write_json = MagicMock()
    gui._append_log = MagicMock()

    summary = gui._check_db()

    assert summary["ok"] is False
    gui._write_json.assert_called_once_with(
        gui._db_path.with_suffix(".check.json"),
        summary,
    )


def test_gui_close_terminates_and_finalizes_process(monkeypatch: pytest.MonkeyPatch) -> None:
    gui = object.__new__(TruthCaptureGui)
    process = MagicMock()
    process.poll.return_value = None
    process.returncode = None
    gui._process = process
    gui._stop_requested = False
    gui.status = MagicMock()
    gui.root = MagicMock()
    gui._on_process_exit = MagicMock()

    def terminate(proc: object) -> None:
        assert proc is process
        process.returncode = -15

    monkeypatch.setattr(gui_module.messagebox, "askyesno", lambda *args: True)
    monkeypatch.setattr(gui_module, "_terminate_process", terminate)

    gui._on_close()

    assert gui._stop_requested is True
    gui._on_process_exit.assert_called_once_with(-15, notify=False)
    gui.root.destroy.assert_called_once_with()


def test_cli_start_failure_finalizes_manifest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path = tmp_path / "truth.db"

    def fail_to_start(*args: object, **kwargs: object) -> None:
        raise OSError("missing executable")

    monkeypatch.setattr(capture_module.subprocess, "Popen", fail_to_start)

    result = CliRunner().invoke(
        truth_app,
        [
            "capture",
            "--robot-ip",
            "192.0.2.1",
            "--viewer",
            "none",
            "--db",
            str(db_path),
        ],
    )

    manifest = json.loads(db_path.with_suffix(".manifest.json").read_text(encoding="utf-8"))
    assert result.exit_code == 1
    assert manifest["finished_at"]
    assert manifest["return_code"] is None
    assert manifest["start_error"] == "missing executable"
    assert manifest["check_ok"] is False


def test_inspect_truth_db_rejects_negative_thresholds(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="min_duration_s"):
        inspect_truth_db(tmp_path / "missing.db", min_duration_s=-1.0)

    with pytest.raises(ValueError, match="minimum stream counts"):
        inspect_truth_db(
            tmp_path / "missing.db",
            min_duration_s=0.0,
            min_counts={"odom": -1},
        )


def test_inspect_truth_db_reports_open_failure(tmp_path: Path) -> None:
    summary = inspect_truth_db(tmp_path, min_duration_s=0.0)

    assert summary["ok"] is False
    assert any("Could not open DB" in item for item in summary["failed_checks"])
