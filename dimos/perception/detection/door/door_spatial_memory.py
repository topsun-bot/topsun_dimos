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
from pathlib import Path
import shutil
import time

from dimos.constants import STATE_DIR
from dimos.types.spatial_record import RecordType, SpatialRecord
from dimos.utils.logging_config import setup_logger

logger = setup_logger()

_LANDMARK_MEMORY_DIR = STATE_DIR / "landmark_memory"
_LANDMARK_DB_PATH = _LANDMARK_MEMORY_DIR / "landmarks.json"
_SNAPSHOTS_DIR = _LANDMARK_MEMORY_DIR / "snapshots"

# Expand user queries (incl. Chinese) to match stored names / legacy English tags.
_LANDMARK_QUERY_EXPANSIONS: dict[str, tuple[str, ...]] = {
    "电脑": ("电脑", "computer", "computer monitor", "显示器", "monitor", "pc", "屏幕", "台式机"),
    "computer": ("电脑", "computer", "computer monitor", "显示器", "monitor"),
    "computer monitor": ("电脑", "computer monitor", "computer", "显示器", "monitor"),
    "显示器": ("显示器", "电脑", "computer", "monitor", "computer monitor"),
    "椅子": ("椅子", "chair", "office chair"),
    "chair": ("椅子", "chair", "office chair"),
    "桌子": ("桌子", "desk", "table", "会议桌"),
    "desk": ("桌子", "desk", "table"),
    "灭火器": ("灭火器", "fire extinguisher", "extinguisher"),
    "fire extinguisher": ("灭火器", "fire extinguisher", "extinguisher"),
}


def _landmark_query_terms(query: str) -> tuple[str, ...]:
    q = query.strip()
    if not q:
        return ()
    key = q.lower()
    extra = _LANDMARK_QUERY_EXPANSIONS.get(key) or _LANDMARK_QUERY_EXPANSIONS.get(q)
    if extra:
        return tuple(dict.fromkeys([q, *extra]))
    return (q,)


def _landmark_text_overlap(query: str, record: SpatialRecord) -> bool:
    q = query.lower().strip()
    if not q:
        return False
    for field in (record.name, record.description, record.state):
        text = (field or "").lower()
        if not text:
            continue
        if q == text or q in text or text in q:
            return True
        if not q.replace(" ", "").isascii() or not text.replace(" ", "").isascii():
            if set(q) & set(text):
                return True
        elif set(q.split()) & set(text.split()):
            return True
    return False


class SpatialLandmarkMemory:
    """Spatial landmark memory with JSON persistence.

    V1 uses an in-memory dict for all queries and a JSON file for
    persistence across robot restarts (same SLAM session).

    Usage::

        memory = SpatialLandmarkMemory()
        memory.load()                           # restore from disk
        memory.record(landmark)                 # store a new landmark
        nearest = memory.find_nearest(1.0, 2.0) # spatial query
        memory.save()                           # persist to disk
    """

    def __init__(
        self,
        db_path: str | Path | None = None,
        snapshots_dir: str | Path | None = None,
    ) -> None:
        self._db_path = Path(db_path or _LANDMARK_DB_PATH)
        self._snapshots_dir = Path(snapshots_dir or _SNAPSHOTS_DIR)
        self._records: dict[str, SpatialRecord] = {}
        self._dirty: bool = False

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def record(self, rec: SpatialRecord) -> str:
        """Record or update a landmark.

        If a landmark already exists at a nearby position (within *dedup_radius*),
        the existing record is updated and ``observation_count`` is incremented.

        Returns the ``record_id`` of the stored (possibly existing) record.
        """
        if rec.record_type == RecordType.ROOM and rec.name:
            by_name = self.find_by_name(rec.name)
            if by_name is not None:
                by_name.observation_count += 1
                by_name.last_seen = time.time()
                by_name.position = rec.position
                by_name.rotation = rec.rotation
                if rec.state:
                    by_name.state = rec.state
                if rec.image_snapshot_path:
                    by_name.image_snapshot_path = rec.image_snapshot_path
                self._dirty = True
                self._auto_save()
                return by_name.record_id

        # Objects (LANDMARK): merge by name; allow many different names at same pose.
        if rec.record_type == RecordType.LANDMARK and rec.name:
            by_name = self.find_by_name(rec.name)
            if by_name is not None:
                by_name.observation_count += 1
                by_name.last_seen = time.time()
                by_name.position = rec.position
                by_name.rotation = rec.rotation
                if rec.state:
                    by_name.state = rec.state
                if rec.confidence > by_name.confidence:
                    by_name.confidence = rec.confidence
                if rec.image_snapshot_path:
                    by_name.image_snapshot_path = rec.image_snapshot_path
                self._dirty = True
                self._auto_save()
                return by_name.record_id
            self._records[rec.record_id] = rec
            self._dirty = True
            self._auto_save()
            return rec.record_id

        existing = self._find_by_position(rec.position[:2], radius=0.5, record_type=rec.record_type)
        if existing is not None:
            existing.observation_count += 1
            existing.last_seen = time.time()
            existing.state = rec.state
            if rec.name:
                existing.name = rec.name
            if rec.confidence > existing.confidence:
                existing.confidence = rec.confidence
            if rec.image_snapshot_path:
                existing.image_snapshot_path = rec.image_snapshot_path
            self._dirty = True
            self._auto_save()
            return existing.record_id

        self._records[rec.record_id] = rec
        self._dirty = True
        self._auto_save()
        return rec.record_id

    def get_all(self) -> list[SpatialRecord]:
        return list(self._records.values())

    def count(self) -> int:
        return len(self._records)

    def find_by_name(self, name: str) -> SpatialRecord | None:
        """Find a landmark by exact name (case-insensitive)."""
        lower = name.lower()
        for rec in self._records.values():
            if rec.name.lower() == lower:
                return rec
        return None

    def search_by_name(self, query: str) -> list[SpatialRecord]:
        """Find landmarks whose name contains the query (case-insensitive)."""
        lower = query.lower()
        return [r for r in self._records.values() if lower in r.name.lower()]

    def find_nearest(self, x: float, y: float, radius: float = 9999.0) -> SpatialRecord | None:
        """Find the nearest landmark within *radius* meters of (x, y)."""
        best: SpatialRecord | None = None
        best_dist = radius
        for rec in self._records.values():
            dx = rec.position[0] - x
            dy = rec.position[1] - y
            dist = (dx * dx + dy * dy) ** 0.5
            if dist < best_dist:
                best_dist = dist
                best = rec
        return best

    def query_by_type(self, record_type: RecordType) -> list[SpatialRecord]:
        return [r for r in self._records.values() if r.record_type == record_type]

    def query_by_state(self, state: str) -> list[SpatialRecord]:
        return [r for r in self._records.values() if r.state == state]

    def query_by_text(self, query: str, limit: int = 5) -> list[SpatialRecord]:
        """Substring search on name, description, state, and record_id."""
        lower = query.lower()
        results: list[SpatialRecord] = []
        for rec in self._records.values():
            haystack = " ".join((rec.name, rec.description, rec.state, rec.record_id)).lower()
            if lower in haystack:
                results.append(rec)
        return results[:limit]

    def resolve_by_query(
        self,
        query: str,
        *,
        prefer_type: RecordType | None = RecordType.LANDMARK,
    ) -> SpatialRecord | None:
        """Resolve a user query to a landmark (exact, alias, substring, description)."""
        terms = _landmark_query_terms(query)
        if not terms:
            return None

        def _pick(candidates: list[SpatialRecord]) -> SpatialRecord | None:
            if not candidates:
                return None
            if prefer_type is not None:
                typed = [c for c in candidates if c.record_type == prefer_type]
                if typed:
                    candidates = typed
            q = query.strip()
            q_lower = q.lower()
            scored: list[tuple[int, SpatialRecord]] = []
            for rec in candidates:
                score = 0
                if rec.name.lower() == q_lower:
                    score += 100
                if _landmark_text_overlap(q, rec):
                    score += 50
                if q_lower in rec.name.lower():
                    score += 30
                score += min(rec.observation_count, 10)
                scored.append((score, rec))
            if scored:
                scored.sort(key=lambda t: -t[0])
                return scored[0][1]
            return candidates[0]

        for term in terms:
            hit = self.find_by_name(term)
            if hit is not None:
                return hit

        for term in terms:
            picked = _pick(self.search_by_name(term))
            if picked is not None:
                return picked

        for term in terms:
            picked = _pick(self.query_by_text(term, limit=10))
            if picked is not None:
                return picked

        return None

    def update_state(self, record_id: str, new_state: str) -> bool:
        if record_id not in self._records:
            return False
        self._records[record_id].state = new_state
        self._records[record_id].last_seen = time.time()
        self._dirty = True
        self._auto_save()
        return True

    def get_by_id(self, record_id: str) -> SpatialRecord | None:
        return self._records.get(record_id)

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def save(self) -> bool:
        """Persist all landmark records to a JSON file."""
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        data = [rec.to_dict() for rec in self._records.values()]
        tmp = self._db_path.with_suffix(".tmp")
        try:
            tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False))
            shutil.move(str(tmp), str(self._db_path))
            self._dirty = False
            return True
        except (OSError, TypeError):
            return False

    def clear_all(self) -> int:
        """Remove all in-memory landmarks, JSON DB, and snapshot images.

        Returns the number of records that were cleared.
        """
        n = len(self._records)
        self._records.clear()
        self._dirty = False
        if self._db_path.exists():
            try:
                self._db_path.unlink()
            except OSError:
                logger.warning("Failed to delete %s", self._db_path, exc_info=True)
        if self._snapshots_dir.exists():
            try:
                shutil.rmtree(self._snapshots_dir)
            except OSError:
                logger.warning(
                    "Failed to clear snapshots dir %s", self._snapshots_dir, exc_info=True
                )
        self._snapshots_dir.mkdir(parents=True, exist_ok=True)
        logger.info("Cleared %d landmark record(s) from %s", n, self._db_path.parent)
        return n

    def load(self) -> bool:
        """Load landmark records from the JSON file."""
        if not self._db_path.exists():
            return False
        try:
            data = json.loads(self._db_path.read_text())
            new_records: dict[str, SpatialRecord] = {}
            for item in data:
                rec = SpatialRecord.from_dict(item)
                new_records[rec.record_id] = rec
            self._records = new_records
            self._dirty = False
            return True
        except (OSError, json.JSONDecodeError, TypeError, KeyError, ValueError) as e:
            logger.warning("Failed to load landmark memory from %s: %s", self._db_path, e)
            return False

    # ------------------------------------------------------------------
    # Snapshot image helpers
    # ------------------------------------------------------------------

    def save_snapshot(self, record_id: str, image_bytes: bytes) -> str | None:
        """Save a landmark snapshot image to the snapshots directory."""
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

    def _auto_save(self) -> None:
        """Persist after each mutation so data survives crashes."""
        try:
            self.save()
        except Exception:
            logger.warning("Auto-save failed", exc_info=True)

    def _find_by_position(
        self, xy: tuple[float, float], radius: float, record_type: RecordType | None = None
    ) -> SpatialRecord | None:
        """Check if any existing record is within *radius* of xy.

        If *record_type* is given, only match records of the same type.
        """
        x, y = xy
        for rec in self._records.values():
            if record_type is not None and rec.record_type != record_type:
                continue
            dx = rec.position[0] - x
            dy = rec.position[1] - y
            if dx * dx + dy * dy <= radius * radius:
                return rec
        return None


class DoorSpatialMemory(SpatialLandmarkMemory):
    """Backward-compatible door memory wrapper around SpatialLandmarkMemory."""

    def record_door(self, rec: SpatialRecord) -> str:
        return self.record(rec)

    def get_all_doors(self) -> list[SpatialRecord]:
        return self.get_all()
