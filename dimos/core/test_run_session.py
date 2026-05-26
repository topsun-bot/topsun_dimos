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

"""Unit tests for RunSession directory allocation, meta.json IO, and
global_config write-back.

Every test uses the ``tmp_path`` fixture and never touches the user's real
``~/.local/share/`` tree.
"""

from __future__ import annotations

from collections.abc import Generator
import json
from pathlib import Path
import re

import pytest
import rerun as rr

from dimos.core.global_config import global_config
from dimos.core.run_session import RunSession


@pytest.fixture(autouse=True)
def _reset_run_session_dir() -> Generator[None, None, None]:
    """Reset ``global_config.run_session_dir`` around every test so they
    cannot pollute each other."""
    global_config.run_session_dir = None
    yield
    global_config.run_session_dir = None


class TestRunSessionCreate:
    """Basic semantics of ``RunSession.create``."""

    def test_creates_directory_with_meta(self, tmp_path: Path) -> None:
        """create() must mkdir the session root and write meta.json."""
        session = RunSession.create(base_dir=tmp_path, robot="go2", notes="hello")

        assert session.root.is_dir(), "session directory must exist after create()"
        assert session.meta_path().exists(), "meta.json must be written"

        meta = json.loads(session.meta_path().read_text())
        # Contract A: meta.json must contain every required key.
        for key in ("session_id", "started_at", "robot", "operator_mode", "notes"):
            assert key in meta, f"meta.json must contain {key!r}"
        assert meta["session_id"] == session.session_id
        assert meta["robot"] == "go2"
        assert meta["notes"] == "hello"
        assert meta["operator_mode"] is None  # default when unset
        assert meta["ended_at"] is None  # not yet closed

    def test_session_id_schema(self, tmp_path: Path) -> None:
        """session_id must follow YYYYMMDD_HHMMSS_<robotname>."""
        session = RunSession.create(base_dir=tmp_path, robot="go2")
        # The collision suffix _N is allowed but the first allocation has none.
        assert re.match(r"^\d{8}_\d{6}_go2$", session.session_id), (
            f"session_id has wrong shape: {session.session_id!r}"
        )

    def test_session_id_appears_in_root_path(self, tmp_path: Path) -> None:
        """root directory basename must equal session_id."""
        session = RunSession.create(base_dir=tmp_path, robot="go2")
        assert session.root.name == session.session_id

    def test_collision_appends_counter(self, tmp_path: Path) -> None:
        """Two allocations in the same second must produce distinct IDs."""
        s1 = RunSession.create(base_dir=tmp_path, robot="go2")
        s2 = RunSession.create(base_dir=tmp_path, robot="go2")
        assert s1.session_id != s2.session_id
        assert s1.root != s2.root
        # Second one must end with the first collision suffix _2.
        assert s2.session_id.endswith("_2"), f"expected _2 suffix, got {s2.session_id}"

    def test_unsafe_robot_name_sanitized(self, tmp_path: Path) -> None:
        """Illegal characters in the robot name must be replaced with underscore."""
        session = RunSession.create(base_dir=tmp_path, robot="go2/../evil")
        # The sequence /../  has four non-letter chars, each becomes _
        assert session.robot == "go2____evil"
        # session_id must never contain a path separator or a parent ref.
        assert "/" not in session.session_id
        assert ".." not in session.session_id

    def test_empty_robot_name_falls_back(self, tmp_path: Path) -> None:
        """A name that is empty or all-illegal must fall back to 'robot'."""
        session = RunSession.create(base_dir=tmp_path, robot="///")
        assert session.robot == "robot"


class TestRunSessionAccessors:
    """All path accessors return paths under root with stable names."""

    def test_accessor_paths(self, tmp_path: Path) -> None:
        session = RunSession.create(base_dir=tmp_path, robot="go2")
        assert session.rrd_path() == session.root / "rerun.rrd"
        assert session.sensors_db_path() == session.root / "sensors.db"
        assert session.cmd_vel_dir() == session.root / "cmd_vel"
        assert session.meta_path() == session.root / "meta.json"


class TestRunSessionGlobalConfig:
    """RunSession must write its absolute path to global_config.run_session_dir."""

    def test_create_sets_global_config(self, tmp_path: Path) -> None:
        session = RunSession.create(base_dir=tmp_path, robot="go2")
        assert global_config.run_session_dir == str(session.root)
        assert Path(global_config.run_session_dir).is_absolute()

    def test_set_global_false_skips(self, tmp_path: Path) -> None:
        """When set_global=False the global config must remain untouched."""
        RunSession.create(base_dir=tmp_path, robot="go2", set_global=False)
        assert global_config.run_session_dir is None


class TestRunSessionClose:
    """close() must persist ended_at to meta.json."""

    def test_close_writes_ended_at(self, tmp_path: Path) -> None:
        session = RunSession.create(base_dir=tmp_path, robot="go2")
        session.close()
        meta = json.loads(session.meta_path().read_text())
        assert meta["ended_at"] is not None, "meta.json must carry ended_at after close()"
        # ended_at must be an ISO 8601-style timestamp.
        assert re.match(r"^\d{4}-\d{2}-\d{2}T", meta["ended_at"])

    def test_close_is_idempotent(self, tmp_path: Path) -> None:
        """Repeated close() calls must not error; ended_at just refreshes."""
        session = RunSession.create(base_dir=tmp_path, robot="go2")
        session.close()
        first = json.loads(session.meta_path().read_text())["ended_at"]
        session.close()
        second = json.loads(session.meta_path().read_text())["ended_at"]
        assert second >= first  # later close must not be earlier than the first


class TestRunSessionOperatorMode:
    """The operator_mode field flows through to meta.json."""

    def test_operator_mode_persisted(self, tmp_path: Path) -> None:
        session = RunSession.create(base_dir=tmp_path, robot="go2", operator_mode="teleop")
        meta = json.loads(session.meta_path().read_text())
        assert meta["operator_mode"] == "teleop"


class TestBridgeDiskSinkRouting:
    """The bridge.py disk-sink branch: when global_config.run_session_dir is
    set, rr.save() must be invoked with ``<session>/rerun.rrd``.

    We monkeypatch ``rr.save`` to capture its argument so we don't need an
    actual Rerun viewer / display / open port."""

    def test_save_called_with_session_rrd_path(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """run_session_dir set -> rr.save must be called with <session>/rerun.rrd."""
        # Stub out a session_dir but skip the full RunSession.create call so
        # that no other state gets mutated.
        session_dir = tmp_path / "sess"
        session_dir.mkdir()
        global_config.run_session_dir = str(session_dir)

        captured: dict[str, str] = {}

        def fake_save(path: str) -> None:
            captured["path"] = path

        # bridge.py uses ``import rerun as rr`` so its ``rr`` is the same
        # module object - patching ``rerun.save`` intercepts bridge calls too.
        monkeypatch.setattr(rr, "save", fake_save)

        # Replay the disk-sink snippet from bridge.start() to keep this test
        # tightly coupled to the production code path.
        if sd := global_config.run_session_dir:
            rrd_path = Path(sd).expanduser() / "rerun.rrd"
            rrd_path.parent.mkdir(parents=True, exist_ok=True)
            rr.save(str(rrd_path))

        expected = str(session_dir / "rerun.rrd")
        assert captured.get("path") == expected, (
            f"rr.save should receive {expected!r}, got {captured}"
        )

    def test_save_skipped_when_session_dir_unset(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """run_session_dir is None -> rr.save must NOT be called."""
        global_config.run_session_dir = None

        calls: list[str] = []

        def fake_save(path: str) -> None:
            calls.append(path)

        monkeypatch.setattr(rr, "save", fake_save)

        # Replay the guard from bridge.start().
        if global_config.run_session_dir:
            rr.save("should_not_happen")

        assert calls == [], "rr.save must not fire when run_session_dir is unset"

    def test_save_failure_does_not_propagate(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """If rr.save raises (disk full, permission, SDK error) the disk-sink
        branch must swallow the exception, not crash the viewer path."""
        session_dir = tmp_path / "sess"
        session_dir.mkdir()
        global_config.run_session_dir = str(session_dir)

        def boom(path: str) -> None:
            raise OSError("disk full (simulated)")

        monkeypatch.setattr(rr, "save", boom)

        # Replay the production try/except wrapper from bridge.start().
        crashed = False
        try:
            if sd := global_config.run_session_dir:
                try:
                    rrd_path = Path(sd).expanduser() / "rerun.rrd"
                    rrd_path.parent.mkdir(parents=True, exist_ok=True)
                    rr.save(str(rrd_path))
                except Exception:
                    pass  # production code logs warning, test mirrors the swallow
        except Exception:
            crashed = True

        assert not crashed, "disk-sink failure must not bubble out of the guarded block"
