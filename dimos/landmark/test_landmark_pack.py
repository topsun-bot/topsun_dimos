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

"""Unit tests for landmark pack import/export."""

from __future__ import annotations

import json
from pathlib import Path
from unittest import mock

import pytest
import yaml

from dimos.landmark.landmark_pack import (
    LANDMARK_PACKS_DIR,
    ImportResult,
    _default_pack_dir,
    _landmark_summary,
    export_pack,
    import_pack,
    list_packs,
    load_manifest,
    status,
)

# ── Fixtures ──────────────────────────────────────────────────────


@pytest.fixture
def tmp_state_dir(tmp_path: Path):
    """Redirect LANDMARK_MEMORY_DIR to a temp dir."""
    with mock.patch(
        "dimos.landmark.landmark_pack.LANDMARK_MEMORY_DIR", tmp_path / "landmark_memory"
    ):
        yield tmp_path / "landmark_memory"


@pytest.fixture
def sample_pack_dir(tmp_path: Path):
    """Create a minimal valid pack directory."""
    pack = tmp_path / "test_pack"
    pack.mkdir()
    (pack / "manifest.yaml").write_text(
        yaml.dump(
            {
                "name": "test_pack",
                "version": 1,
                "description": "Test pack",
                "scene": {"robot": "unitree-go2", "mode": "replay", "replay_db": "go2_short"},
                "demo_queries": ["去厨房"],
            }
        )
    )
    (pack / "landmarks.json").write_text(
        json.dumps(
            [
                {
                    "name": "厨房",
                    "record_type": "room",
                    "position": [1.5, 2.0, 0.0],
                    "rotation": [0.0, 0.0, 0.0],
                    "description": "厨房",
                    "record_id": "rec_test_01",
                    "confidence": 0.95,
                    "image_snapshot_path": "",
                    "timestamp": 1748217600.0,
                    "last_seen": 1748217600.0,
                    "observation_count": 1,
                    "session_id": "old_session_123",
                    "state": "",
                    "metadata": {},
                },
                {
                    "name": "电脑",
                    "record_type": "landmark",
                    "position": [4.5, 1.2, 0.0],
                    "rotation": [0.0, 0.0, 0.0],
                    "description": "台式电脑",
                    "record_id": "rec_test_02",
                    "confidence": 0.88,
                    "image_snapshot_path": "",
                    "timestamp": 1748217620.0,
                    "last_seen": 1748217620.0,
                    "observation_count": 1,
                    "session_id": "old_session_123",
                    "state": "",
                    "metadata": {},
                },
            ]
        )
    )
    (pack / "snapshots").mkdir()
    (pack / "snapshots" / "rec_test_01.jpg").write_text("fake jpeg")
    return pack


@pytest.fixture
def mock_dimos_not_running():
    """Ensure _dimos_is_running returns False."""
    with mock.patch("dimos.landmark.landmark_pack._dimos_is_running", return_value=False):
        yield


# ── list_packs ────────────────────────────────────────────────────


def test_list_packs_empty(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setattr("dimos.landmark.landmark_pack.LANDMARK_PACKS_DIR", tmp_path)
    assert list_packs() == []


def test_list_packs_with_pack(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    pack = tmp_path / "demo_office"
    pack.mkdir()
    (pack / "manifest.yaml").write_text("name: demo_office")
    monkeypatch.setattr("dimos.landmark.landmark_pack.LANDMARK_PACKS_DIR", tmp_path)
    assert list_packs() == ["demo_office"]


def test_list_packs_skips_non_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    (tmp_path / "not_a_pack").write_text("just a file")
    monkeypatch.setattr("dimos.landmark.landmark_pack.LANDMARK_PACKS_DIR", tmp_path)
    assert list_packs() == []


# ── load_manifest ─────────────────────────────────────────────────


def test_load_manifest(sample_pack_dir: Path):
    manifest = load_manifest("test_pack", pack_dir=sample_pack_dir)
    assert manifest["name"] == "test_pack"
    assert manifest["demo_queries"] == ["去厨房"]


def test_load_manifest_missing():
    with pytest.raises(FileNotFoundError):
        load_manifest("nonexistent")


# ── _landmark_summary ─────────────────────────────────────────────


def test_landmark_summary():
    records = [
        {"record_type": "room", "name": "厨房"},
        {"record_type": "room", "name": "走廊"},
        {"record_type": "landmark", "name": "电脑"},
    ]
    s = _landmark_summary(records)
    assert s == {"room": 2, "landmark": 1}


# ── import_pack ───────────────────────────────────────────────────


def test_import_pack_basic(sample_pack_dir: Path, tmp_state_dir: Path, mock_dimos_not_running):
    result = import_pack("test_pack", force=True, pack_dir=sample_pack_dir)
    assert result.record_count == 2
    assert result.summary == {"room": 1, "landmark": 1}
    assert (tmp_state_dir / "landmarks.json").exists()
    assert (tmp_state_dir / "snapshots" / "rec_test_01.jpg").exists()


def test_import_pack_clears_session_id(
    sample_pack_dir: Path, tmp_state_dir: Path, mock_dimos_not_running
):
    import_pack("test_pack", force=True, pack_dir=sample_pack_dir)
    data = json.loads((tmp_state_dir / "landmarks.json").read_text())
    for rec in data:
        assert rec["session_id"] == ""


def test_import_pack_keep_session_id(
    sample_pack_dir: Path, tmp_state_dir: Path, mock_dimos_not_running
):
    import_pack("test_pack", force=True, clear_session_id=False, pack_dir=sample_pack_dir)
    data = json.loads((tmp_state_dir / "landmarks.json").read_text())
    assert data[0]["session_id"] == "old_session_123"


def test_import_pack_dry_run(sample_pack_dir: Path, tmp_state_dir: Path, mock_dimos_not_running):
    result = import_pack("test_pack", dry_run=True, pack_dir=sample_pack_dir)
    assert result.record_count == 2
    assert not (tmp_state_dir / "landmarks.json").exists()


def test_import_pack_dimos_running(sample_pack_dir: Path):
    with mock.patch("dimos.landmark.landmark_pack._dimos_is_running", return_value=True):
        with pytest.raises(RuntimeError, match="DimOS is currently running"):
            import_pack("test_pack", force=True, pack_dir=sample_pack_dir)


def test_import_pack_exists_no_force(
    sample_pack_dir: Path, tmp_state_dir: Path, mock_dimos_not_running
):
    tmp_state_dir.mkdir(parents=True)
    (tmp_state_dir / "landmarks.json").write_text("[]")
    with pytest.raises(FileExistsError, match="already exists"):
        import_pack("test_pack", pack_dir=sample_pack_dir)


def test_import_pack_exists_with_force(
    sample_pack_dir: Path, tmp_state_dir: Path, mock_dimos_not_running
):
    tmp_state_dir.mkdir(parents=True)
    (tmp_state_dir / "landmarks.json").write_text("[]")
    result = import_pack("test_pack", force=True, pack_dir=sample_pack_dir)
    assert result.record_count == 2


def test_import_pack_missing_manifest(tmp_path: Path, mock_dimos_not_running):
    pack = tmp_path / "bad_pack"
    pack.mkdir()
    with pytest.raises(FileNotFoundError):
        import_pack("bad_pack", force=True, pack_dir=pack)


# ── export_pack ───────────────────────────────────────────────────


def test_export_pack(tmp_state_dir: Path, tmp_path: Path):
    tmp_state_dir.mkdir(parents=True)
    records = [
        {
            "name": "厨房",
            "record_type": "room",
            "position": [1.0, 2.0, 0.0],
            "rotation": [0.0, 0.0, 0.0],
            "description": "",
            "record_id": "rec_x01",
            "confidence": 1.0,
            "image_snapshot_path": "",
            "timestamp": 0.0,
            "last_seen": 0.0,
            "observation_count": 1,
            "session_id": "",
            "state": "",
            "metadata": {},
        },
    ]
    (tmp_state_dir / "landmarks.json").write_text(json.dumps(records))
    snapshots = tmp_state_dir / "snapshots"
    snapshots.mkdir()
    (snapshots / "rec_x01.jpg").write_text("fake jpeg")

    output = tmp_path / "exported"
    dest = export_pack("exported", output_dir=output)
    assert dest == output
    assert (output / "landmarks.json").exists()
    assert (output / "snapshots" / "rec_x01.jpg").exists()
    assert (output / "manifest.yaml").exists()


def test_export_pack_empty(tmp_path: Path, tmp_state_dir: Path):
    tmp_state_dir.mkdir(parents=True)
    (tmp_state_dir / "landmarks.json").write_text("[]")
    with pytest.raises(ValueError, match="empty"):
        export_pack("empty_pack", output_dir=tmp_path / "empty_pack")


def test_export_pack_no_memory():
    with mock.patch("dimos.landmark.landmark_pack.LANDMARK_MEMORY_DIR", Path("/nonexistent/path")):
        with pytest.raises(FileNotFoundError):
            export_pack("no_memory")


def test_round_trip(
    sample_pack_dir: Path, tmp_state_dir: Path, tmp_path: Path, mock_dimos_not_running
):
    """import → export → re-import gives same record count."""
    # Import
    result1 = import_pack("test_pack", force=True, pack_dir=sample_pack_dir)
    assert result1.record_count == 2

    # Export
    export_dir = tmp_path / "roundtrip"
    export_pack("roundtrip", output_dir=export_dir)

    # Clear state
    import shutil

    shutil.rmtree(tmp_state_dir)

    # Re-import from exported pack
    result2 = import_pack("roundtrip", force=True, pack_dir=export_dir)
    assert result2.record_count == 2


# ── status ────────────────────────────────────────────────────────


def test_status_empty():
    with mock.patch("dimos.landmark.landmark_pack.LANDMARK_MEMORY_DIR", Path("/nonexistent/path")):
        s = status()
        assert not s.exists
        assert s.record_count == 0


def test_status_with_data(tmp_state_dir: Path):
    tmp_state_dir.mkdir(parents=True)
    (tmp_state_dir / "landmarks.json").write_text(
        json.dumps(
            [
                {"record_type": "room", "name": "厨房"},
                {"record_type": "landmark", "name": "电脑"},
            ]
        )
    )
    s = status()
    assert s.exists
    assert s.record_count == 2
    assert s.summary == {"room": 1, "landmark": 1}


# ── _default_pack_dir ─────────────────────────────────────────────


def test_default_pack_dir():
    d = _default_pack_dir("demo_office")
    assert d == LANDMARK_PACKS_DIR / "demo_office"


# ── ImportResult ──────────────────────────────────────────────────


def test_import_result_fields():
    r = ImportResult(
        pack_name="test",
        dest_dir=Path("/tmp/test"),
        backup_dir=None,
        record_count=3,
        summary={"room": 2, "landmark": 1},
        demo_queries=["去厨房"],
    )
    assert r.pack_name == "test"
    assert r.record_count == 3
    assert r.demo_queries == ["去厨房"]
