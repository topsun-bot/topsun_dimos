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

"""RunSession: yi ci yun xing = yi ge session mu lu.

This module encapsulates the on-disk directory shared by all sinks (Rerun
.rrd, SQLite sensors.db, cmd_vel replay) for one closed-loop run. A single
``RunSession`` corresponds to one teleop recording or one simulation replay.
It auto-allocates a timestamped + robot-named session_id and lays out the
directory per plan contract A:

    <session_id>/
      meta.json    # {"session_id", "started_at", "robot", "operator_mode"?, "notes"}
      rerun.rrd
      sensors.db
      cmd_vel/     # or cmd_vel.pkl, decided by Unit 1

Downstream modules consume only the path accessors (``rrd_path()``,
``sensors_db_path()``, ...) so the underlying layout can change in this
file alone.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
from pathlib import Path
import re

from dimos.core.global_config import global_config

# Default base_dir matches plan contract A: ``~/.local/share/dimos/runs``.
# Note this differs from ``dimos.constants.STATE_DIR``
# (``~/.local/state/dimos``) - state holds runtime metadata (PID, logs),
# share holds long-lived data assets.
_DEFAULT_BASE_DIR = Path.home() / ".local" / "share" / "dimos" / "runs"

# session_id schema: YYYYMMDD_HHMMSS_<robotname>; collision suffix _<n> is also matched.
_SESSION_ID_RE = re.compile(r"^\d{8}_\d{6}_[A-Za-z0-9][A-Za-z0-9_-]*$")
# Robot names allow only alphanumerics, underscore, hyphen to keep paths safe.
_ROBOT_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")


def _sanitize_robot_name(robot: str) -> str:
    """Normalize a robot name into something safe to drop into a directory name.

    Keep alphanumerics, underscore, hyphen; replace other chars with ``_``.
    An empty or all-illegal name falls back to the literal string ``robot``.
    """
    cleaned = re.sub(r"[^A-Za-z0-9_-]", "_", robot).strip("_-") or "robot"
    if not _ROBOT_NAME_RE.match(cleaned):
        cleaned = "robot"
    return cleaned


def _allocate_session_dir(base_dir: Path, robot: str, started_at: datetime) -> tuple[str, Path]:
    """Allocate a fresh empty session directory under ``base_dir``.

    Collision strategy: first try ``YYYYMMDD_HHMMSS_<robot>``; if it exists,
    append ``_2``, ``_3``, ... until an empty slot is found. Concurrent
    session creation will therefore never clobber.
    """
    base_dir.mkdir(parents=True, exist_ok=True)
    base_id = f"{started_at.strftime('%Y%m%d_%H%M%S')}_{robot}"
    candidate = base_id
    counter = 1
    while True:
        path = base_dir / candidate
        try:
            path.mkdir(parents=False, exist_ok=False)
        except FileExistsError:
            counter += 1
            candidate = f"{base_id}_{counter}"
            continue
        return candidate, path


@dataclass
class RunSession:
    """Handle to a single run's on-disk session directory.

    Attributes:
        session_id: ``YYYYMMDD_HHMMSS_<robotname>``-style unique ID.
        root: Absolute path to the session directory (already mkdir'd).
        robot: Sanitized robot name.
        started_at: Start timestamp (timezone-aware, UTC).
        notes: Free-form text persisted into meta.json.
        operator_mode: Optional mode label (e.g. ``"teleop"``, ``"closed-loop"``)
            also persisted to meta.json.
    """

    session_id: str
    root: Path
    robot: str
    started_at: datetime
    notes: str = ""
    operator_mode: str | None = None
    # Filled in by ``close()``; serialized as the ``ended_at`` field of meta.json.
    _ended_at: datetime | None = field(default=None, init=False, repr=False)

    # ---------------------------------------------------------------- factory

    @classmethod
    def create(
        cls,
        base_dir: Path | None = None,
        robot: str = "go2",
        notes: str = "",
        operator_mode: str | None = None,
        *,
        set_global: bool = True,
    ) -> RunSession:
        """Allocate and create a fresh session directory.

        Args:
            base_dir: Parent directory. ``None`` -> ``~/.local/share/dimos/runs``.
            robot: Robot name, normalized (alphanumerics + ``_`` / ``-`` only).
            notes: Free-form description.
            operator_mode: Optional mode label.
            set_global: Whether to also assign ``global_config.run_session_dir``
                to the new absolute path. Downstream sinks (Rerun bridge,
                sensors recorder, ...) read that field to decide where to land
                their output. Defaults to ``True``.

        Returns:
            A constructed ``RunSession`` with the directory created and
            meta.json already written.
        """
        base = (base_dir or _DEFAULT_BASE_DIR).expanduser().resolve()
        sanitized_robot = _sanitize_robot_name(robot)
        started_at = datetime.now(timezone.utc)
        session_id, root = _allocate_session_dir(base, sanitized_robot, started_at)
        if not _SESSION_ID_RE.match(session_id):
            # Defensive check: contract A requires session_id to match this
            # schema. The collision suffix ``_N`` is also covered by the regex.
            raise RuntimeError(f"Allocated session_id {session_id!r} violates schema")

        session = cls(
            session_id=session_id,
            root=root,
            robot=sanitized_robot,
            started_at=started_at,
            notes=notes,
            operator_mode=operator_mode,
        )
        session.write_meta()

        if set_global:
            global_config.run_session_dir = str(root)

        return session

    # ---------------------------------------------------------------- accessors

    def rrd_path(self) -> Path:
        """Path to the Rerun recording file (``<root>/rerun.rrd``)."""
        return self.root / "rerun.rrd"

    def sensors_db_path(self) -> Path:
        """Path to the SQLite sensors database (``<root>/sensors.db``)."""
        return self.root / "sensors.db"

    def cmd_vel_dir(self) -> Path:
        """Path to the cmd_vel recording directory (``<root>/cmd_vel``).

        The on-disk format (pickle-per-frame vs single file) is decided by
        Unit 1's ``CmdVelRecorder``; this accessor only guarantees the path.
        """
        return self.root / "cmd_vel"

    def meta_path(self) -> Path:
        """Path to the meta.json file (``<root>/meta.json``)."""
        return self.root / "meta.json"

    # ---------------------------------------------------------------- meta IO

    def _meta_payload(self) -> dict[str, object]:
        """Build the dict serialized to meta.json.

        Includes every contract-required field. Optional fields
        (operator_mode / ended_at) are serialized as ``null`` when unset so
        downstream consumers can parse uniformly.
        """
        return {
            "session_id": self.session_id,
            "started_at": self.started_at.isoformat(),
            "robot": self.robot,
            "operator_mode": self.operator_mode,
            "notes": self.notes,
            "ended_at": self._ended_at.isoformat() if self._ended_at else None,
        }

    def write_meta(self) -> None:
        """Persist current metadata to meta.json, atomically replacing it."""
        payload = self._meta_payload()
        tmp = self.meta_path().with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(self.meta_path())

    def close(self) -> None:
        """Mark the session ended and persist ``ended_at`` to meta.json.

        Idempotent: repeated calls just refresh ``ended_at`` to the latest time.
        """
        self._ended_at = datetime.now(timezone.utc)
        self.write_meta()
