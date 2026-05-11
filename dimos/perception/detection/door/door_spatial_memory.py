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

"""
SpatialLandmarkMemory — simplified V1.

In-memory dict for fast queries + JSON file persistence.
No database dependency. Can be upgraded to ChromaDB/SQLite later.
"""

from __future__ import annotations

import json
import os
import shutil
import time
from pathlib import Path
from typing import Any

from dimos.constants import STATE_DIR
from dimos.types.spatial_record import SpatialRecord, RecordType

_DOOR_MEMORY_DIR = STATE_DIR / "door_memory"
_DOOR_DB_PATH = _DOOR_MEMORY_DIR / "doors.json"
_SNAPSHOTS_DIR = _DOOR_MEMORY_DIR / "snapshots"


class DoorSpatialMemory:
    """Simplified door spatial memory with JSON persistence.

    V1 uses an in-memory dict for all queries and a JSON file for
    persistence across robot restarts (same SLAM session).

    Usage::

        memory = DoorSpatialMemory()
        memory.load()                          # restore from disk
        memory.record_door(door)               # store a new door
        door = memory.find_nearest(1.0, 2.0)   # spatial query
        memory.save()                          # persist to disk
    """

    def __init__(
        self,
        db_path: str | Path | None = None,
        snapshots_dir: str | Path | None = None,
    ) -> None:
        self._db_path = Path(db_path or _DOOR_DB_PATH)
        self._snapshots_dir = Path(snapshots_dir or _SNAPSHOTS_DIR)
        self._doors: dict[str, SpatialRecord] = {}
        self._dirty: bool = False

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def record_door(self, door: SpatialRecord) -> str:
        """Record or update a door.

        If a door already exists at a nearby position (within *dedup_radius*),
        the existing record is updated and ``observation_count`` is incremented.

        Returns the ``record_id`` of the stored (possibly existing) record.
        """
        existing = self._find_by_position(door.position[:2], radius=0.5)
        if existing is not None:
            existing.observation_count += 1
            existing.last_seen = time.time()
            existing.state = door.state
            if door.name:
                existing.name = door.name
            if door.confidence > existing.confidence:
                existing.confidence = door.confidence
            if door.image_snapshot_path:
                existing.image_snapshot_path = door.image_snapshot_path
            self._dirty = True
            return existing.record_id

        self._doors[door.record_id] = door
        self._dirty = True
        return door.record_id

    def get_all_doors(self) -> list[SpatialRecord]:
        return list(self._doors.values())

    def count(self) -> int:
        return len(self._doors)

    def find_by_name(self, name: str) -> SpatialRecord | None:
        """Find a door by exact name (case-insensitive)."""
        lower = name.lower()
        for door in self._doors.values():
            if door.name.lower() == lower:
                return door
        return None

    def search_by_name(self, query: str) -> list[SpatialRecord]:
        """Find doors whose name contains the query (case-insensitive)."""
        lower = query.lower()
        return [d for d in self._doors.values() if lower in d.name.lower()]

    def find_nearest(self, x: float, y: float, radius: float = 9999.0) -> SpatialRecord | None:
        """Find the nearest door within *radius* meters of (x, y)."""
        best: SpatialRecord | None = None
        best_dist = radius
        for door in self._doors.values():
            dx = door.position[0] - x
            dy = door.position[1] - y
            dist = (dx * dx + dy * dy) ** 0.5
            if dist < best_dist:
                best_dist = dist
                best = door
        return best

    def query_by_type(self, record_type: RecordType) -> list[SpatialRecord]:
        return [d for d in self._doors.values() if d.record_type == record_type]

    def query_by_state(self, state: str) -> list[SpatialRecord]:
        return [d for d in self._doors.values() if d.state == state]

    def query_by_text(self, query: str, limit: int = 5) -> list[SpatialRecord]:
        """Simple substring search on name, description, and record_id.

        V1 uses basic string matching. A later version can use CLIP
        embeddings for semantic search.
        """
        lower = query.lower()
        results: list[SpatialRecord] = []
        for door in self._doors.values():
            if lower in door.name.lower() or lower in door.description.lower() or lower in door.record_id:
                results.append(door)
        return results[:limit]

    def update_state(self, record_id: str, new_state: str) -> bool:
        if record_id not in self._doors:
            return False
        self._doors[record_id].state = new_state
        self._doors[record_id].last_seen = time.time()
        self._dirty = True
        return True

    def get_by_id(self, record_id: str) -> SpatialRecord | None:
        return self._doors.get(record_id)

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def save(self) -> bool:
        """Persist all door records to a JSON file."""
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        data = [door.to_dict() for door in self._doors.values()]
        tmp = self._db_path.with_suffix(".tmp")
        try:
            tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False))
            shutil.move(str(tmp), str(self._db_path))
            self._dirty = False
            return True
        except (OSError, TypeError) as e:
            return False

    def load(self) -> bool:
        """Load door records from the JSON file."""
        if not self._db_path.exists():
            return False
        try:
            data = json.loads(self._db_path.read_text())
            self._doors = {}
            for item in data:
                door = SpatialRecord.from_dict(item)
                self._doors[door.record_id] = door
            self._dirty = False
            return True
        except (OSError, json.JSONDecodeError) as e:
            return False

    # ------------------------------------------------------------------
    # Snapshot image helpers
    # ------------------------------------------------------------------

    def save_snapshot(self, record_id: str, image_bytes: bytes) -> str | None:
        """Save a cropped door image to the snapshots directory."""
        self._snapshots_dir.mkdir(parents=True, exist_ok=True)
        path = self._snapshots_dir / f"{record_id}.jpg"
        try:
            path.write_bytes(image_bytes)
            return str(path)
        except OSError:
            return None

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _find_by_position(self, xy: tuple[float, float], radius: float) -> SpatialRecord | None:
        """Check if any existing door is within *radius* of xy."""
        x, y = xy
        for door in self._doors.values():
            dx = door.position[0] - x
            dy = door.position[1] - y
            if dx * dx + dy * dy <= radius * radius:
                return door
        return None
