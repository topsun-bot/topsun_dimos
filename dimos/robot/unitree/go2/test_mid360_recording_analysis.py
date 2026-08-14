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

from __future__ import annotations

import math
from pathlib import Path
import sqlite3

from dimos.msgs.geometry_msgs.Quaternion import Quaternion
from dimos.msgs.geometry_msgs.Transform import Transform
from dimos.msgs.geometry_msgs.Vector3 import Vector3
from dimos.msgs.tf2_msgs.TFMessage import TFMessage
from dimos.robot.unitree.go2.go2_mid360_static_transforms import (
    ORIN_NAVIGATION_BASE_TO_IMU,
    ORIN_NAVIGATION_IMU_TO_MID360,
)
from dimos.robot.unitree.go2.mid360_recording_analysis import (
    analyze_recording,
    main,
    motion_gate_status,
)


def _create_recording(path: Path, *, duplicate_odom: bool = False) -> None:
    with sqlite3.connect(path) as connection:
        for stream in ("odom", "lidar", "tf"):
            connection.execute(
                f'CREATE TABLE "{stream}" ('
                "id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL NOT NULL, value NUMERIC, "
                "pose_x REAL, pose_y REAL, pose_z REAL, pose_qx REAL, pose_qy REAL, "
                "pose_qz REAL, pose_qw REAL, tags BLOB)"
            )
        connection.execute('CREATE TABLE "tf_blob" (id INTEGER PRIMARY KEY, data BLOB NOT NULL)')

        odom_timestamps = (1.0, 1.1, 1.1 if duplicate_odom else 1.2, 1.3)
        for index, timestamp in enumerate(odom_timestamps):
            yaw = math.radians(index * 10.0)
            pose = (
                timestamp,
                index * 0.2,
                0.0,
                0.0,
                0.0,
                0.0,
                math.sin(yaw / 2.0),
                math.cos(yaw / 2.0),
            )
            connection.execute(
                f'INSERT INTO "odom" ({"ts, pose_x, pose_y, pose_z, pose_qx, pose_qy, pose_qz, pose_qw"}) '
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                pose,
            )

        for index, timestamp in enumerate((1.0, 1.1, 1.2, 1.3)):
            yaw = math.radians(index * 10.0)
            connection.execute(
                f'INSERT INTO "lidar" ({"ts, pose_x, pose_y, pose_z, pose_qx, pose_qy, pose_qz, pose_qw"}) '
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    timestamp,
                    index * 0.2,
                    0.0,
                    0.0,
                    0.0,
                    0.0,
                    math.sin(yaw / 2.0),
                    math.cos(yaw / 2.0),
                ),
            )
            connection.execute(
                f'INSERT INTO "tf" ({"ts, pose_x, pose_y, pose_z, pose_qx, pose_qy, pose_qz, pose_qw"}) '
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (timestamp, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 1.0),
            )

        edges = (
            ("world", "base_link", (0.0, 0.0, 0.0), (0.0, 0.0, 0.0)),
            (
                "base_link",
                "mid360_imu_link",
                ORIN_NAVIGATION_BASE_TO_IMU[0],
                ORIN_NAVIGATION_BASE_TO_IMU[1],
            ),
            (
                "mid360_imu_link",
                "mid360_link",
                ORIN_NAVIGATION_IMU_TO_MID360[0],
                ORIN_NAVIGATION_IMU_TO_MID360[1],
            ),
            ("base_link", "camera_link", (0.3, 0.0, 0.0), (0.0, 0.0, 0.0)),
            ("camera_link", "camera_optical", (0.0, 0.0, 0.0), (0.0, 0.0, 0.0)),
        )
        for index, (parent, child, xyz, rpy) in enumerate(edges, start=1):
            message = TFMessage(
                Transform(
                    translation=Vector3(*xyz),
                    rotation=Quaternion.from_euler(Vector3(*rpy)),
                    frame_id=parent,
                    child_frame_id=child,
                    ts=1.0,
                )
            )
            connection.execute(
                'INSERT INTO "tf_blob" (id, data) VALUES (?, ?)',
                (index, message.lcm_encode()),
            )


def test_analyze_recording_passes_objective_metadata_gates(tmp_path: Path) -> None:
    db_path = tmp_path / "navigation.db"
    _create_recording(db_path)

    report = analyze_recording(
        db_path,
        expected_min_radius_m=0.5,
        expected_min_yaw_span_deg=20.0,
    )

    assert report["all_checks_passed"] is True
    assert report["odom_pose"]["max_radius_xy_m"] == 0.6000000000000001
    assert report["odom_pose"]["rpy_deg"]["span"][2] == 29.999999999999996
    assert report["cloud_odom"]["timestamp_error_s"]["max"] == 0.0
    assert report["checks"]["tf_single_parent"] is True
    assert report["checks"]["tf_required_edges_present"] is True
    assert report["checks"]["tf_mount_matches_profile"] is True


def test_analyze_recording_rejects_tf_child_with_two_parents(tmp_path: Path) -> None:
    db_path = tmp_path / "navigation.db"
    _create_recording(db_path)
    conflicting = TFMessage(
        Transform(
            translation=Vector3(),
            rotation=Quaternion(),
            frame_id="world",
            child_frame_id="mid360_link",
            ts=1.4,
        )
    )
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            'INSERT INTO "tf_blob" (id, data) VALUES (?, ?)',
            (99, conflicting.lcm_encode()),
        )

    report = analyze_recording(db_path)

    assert report["all_checks_passed"] is False
    assert report["tf_tree"]["multi_parent_children"] == {
        "mid360_link": ["mid360_imu_link", "world"]
    }
    assert report["checks"]["tf_single_parent"] is False


def test_analyze_recording_rejects_duplicate_source_state(tmp_path: Path) -> None:
    db_path = tmp_path / "navigation.db"
    _create_recording(db_path, duplicate_odom=True)

    report = analyze_recording(db_path)

    assert report["all_checks_passed"] is False
    assert report["timestamp"]["odom"]["duplicates"] == 1
    assert report["checks"]["odom_timestamp_monotonic"] is False


def test_cli_writes_json_and_csv(tmp_path: Path) -> None:
    db_path = tmp_path / "navigation.db"
    _create_recording(db_path)

    assert main([str(db_path)]) == 0
    assert (tmp_path / "t2-analysis.json").is_file()
    assert (tmp_path / "t2-odom.csv").read_text(encoding="utf-8").splitlines()[0] == (
        "ts,x,y,z,qx,qy,qz,qw"
    )


def test_motion_gate_requires_motion_rotation_and_final_settle(tmp_path: Path) -> None:
    db_path = tmp_path / "navigation.db"
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            'CREATE TABLE "odom" ('
            "id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL NOT NULL, value NUMERIC, "
            "pose_x REAL, pose_y REAL, pose_z REAL, pose_qx REAL, pose_qy REAL, "
            "pose_qz REAL, pose_qw REAL, tags BLOB)"
        )
        for index in range(81):
            timestamp = index * 0.1
            moving = index <= 40
            x = min(index / 40.0, 1.0) * 0.6
            yaw = math.radians(min(index / 40.0, 1.0) * 25.0)
            if not moving:
                x = 0.6
                yaw = math.radians(25.0)
            connection.execute(
                'INSERT INTO "odom" '
                "(ts, pose_x, pose_y, pose_z, pose_qx, pose_qy, pose_qz, pose_qw) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (timestamp, x, 0.0, 0.0, 0.0, 0.0, math.sin(yaw / 2), math.cos(yaw / 2)),
            )

    status = motion_gate_status(
        db_path,
        expected_min_radius_m=0.5,
        expected_min_yaw_span_deg=20.0,
    )

    assert status["all_checks_passed"] is True
    assert all(status["checks"].values())


def test_motion_gate_rejects_motion_without_final_settle(tmp_path: Path) -> None:
    db_path = tmp_path / "navigation.db"
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            'CREATE TABLE "odom" ('
            "id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL NOT NULL, value NUMERIC, "
            "pose_x REAL, pose_y REAL, pose_z REAL, pose_qx REAL, pose_qy REAL, "
            "pose_qz REAL, pose_qw REAL, tags BLOB)"
        )
        for index in range(41):
            timestamp = index * 0.1
            x = index * 0.02
            yaw = math.radians(index)
            connection.execute(
                'INSERT INTO "odom" '
                "(ts, pose_x, pose_y, pose_z, pose_qx, pose_qy, pose_qz, pose_qw) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (timestamp, x, 0.0, 0.0, 0.0, 0.0, math.sin(yaw / 2), math.cos(yaw / 2)),
            )

    status = motion_gate_status(
        db_path,
        expected_min_radius_m=0.5,
        expected_min_yaw_span_deg=20.0,
    )

    assert status["all_checks_passed"] is False
    assert status["checks"]["expected_translation_observed"] is True
    assert status["checks"]["expected_rotation_observed"] is True
    assert status["checks"]["settled_translation"] is False
    assert status["checks"]["settled_rotation"] is False
