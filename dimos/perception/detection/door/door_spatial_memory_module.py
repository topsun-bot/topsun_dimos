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

"""DIMOS Module wrapper for SpatialLandmarkMemory.

Exposes spatial landmark operations as RPC methods so it can be wired
into blueprints and injected into skill containers via Spec protocols.
"""

from __future__ import annotations

from typing import Any

from dimos.constants import STATE_DIR
from dimos.core.core import rpc
from dimos.core.module import Module, ModuleConfig
from dimos.perception.detection.door.door_spatial_memory import SpatialLandmarkMemory
from dimos.types.spatial_record import SpatialRecord, RecordType
from dimos.utils.logging_config import setup_logger

_LANDMARK_MEMORY_DIR = STATE_DIR / "landmark_memory"

logger = setup_logger()
_LANDMARK_DB_PATH = _LANDMARK_MEMORY_DIR / "landmarks.json"
_SNAPSHOTS_DIR = _LANDMARK_MEMORY_DIR / "snapshots"


class SpatialLandmarkMemoryConfig(ModuleConfig):
    db_path: str = str(_LANDMARK_DB_PATH)
    snapshots_dir: str = str(_SNAPSHOTS_DIR)


class SpatialLandmarkMemoryModule(Module):
    """DIMOS Module wrapping SpatialLandmarkMemory for blueprint registration.

    Exposes all landmark CRUD and query operations as ``@rpc`` methods.
    """

    config: SpatialLandmarkMemoryConfig

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._memory = SpatialLandmarkMemory(
            db_path=self.config.db_path,
            snapshots_dir=self.config.snapshots_dir,
        )

    @rpc
    def start(self) -> None:
        """Start the module and load persisted landmarks."""
        super().start()
        loaded = self._memory.load()
        if loaded:
            logger.info("Loaded %d landmark(s) from %s", self._memory.count(), self.config.db_path)
        else:
            logger.info("Starting with empty landmark memory")

    @rpc
    def stop(self) -> None:
        """Persist landmarks and shut down."""
        self._memory.save()
        super().stop()

    # ------------------------------------------------------------------
    # RPC: CRUD
    # ------------------------------------------------------------------

    @rpc
    def record_landmark_str(
        self,
        name: str,
        pos_x: float,
        pos_y: float,
        pos_z: float,
        rot_roll: float,
        rot_pitch: float,
        rot_yaw: float,
        record_type: str = "landmark",
        state: str = "",
        confidence: float = 0.0,
    ) -> str:
        """Record a spatial landmark with primitive parameters (safe across RPC).

        Returns the ``record_id`` of the recorded or updated record.
        """
        rec = SpatialRecord(
            name=name,
            record_type=RecordType(record_type) if record_type else RecordType.UNKNOWN,
            position=(pos_x, pos_y, pos_z),
            rotation=(rot_roll, rot_pitch, rot_yaw),
            state=state,
            confidence=confidence,
        )
        return self._memory.record(rec)

    @rpc
    def record(self, rec: SpatialRecord) -> str:
        """Record a spatial landmark. Returns record_id."""
        return self._memory.record(rec)

    @rpc
    def get_all(self) -> list[SpatialRecord]:
        return self._memory.get_all()

    @rpc
    def count(self) -> int:
        return self._memory.count()

    # ------------------------------------------------------------------
    # RPC: Query
    # ------------------------------------------------------------------

    @rpc
    def find_by_name(self, name: str) -> SpatialRecord | None:
        return self._memory.find_by_name(name)

    @rpc
    def search_by_name(self, query: str) -> list[SpatialRecord]:
        return self._memory.search_by_name(query)

    @rpc
    def find_nearest(self, x: float, y: float, radius: float = 9999.0) -> SpatialRecord | None:
        return self._memory.find_nearest(x, y, radius)

    @rpc
    def query_by_type(self, record_type: str) -> list[SpatialRecord]:
        return self._memory.query_by_type(RecordType(record_type))

    @rpc
    def query_by_state(self, state: str) -> list[SpatialRecord]:
        return self._memory.query_by_state(state)

    @rpc
    def query_by_text(self, query: str, limit: int = 5) -> list[SpatialRecord]:
        return self._memory.query_by_text(query, limit)

    @rpc
    def resolve_by_query(self, query: str) -> SpatialRecord | None:
        return self._memory.resolve_by_query(query)

    @rpc
    def get_by_id(self, record_id: str) -> SpatialRecord | None:
        return self._memory.get_by_id(record_id)

    # ------------------------------------------------------------------
    # RPC: Mutation
    # ------------------------------------------------------------------

    @rpc
    def update_state(self, record_id: str, new_state: str) -> bool:
        return self._memory.update_state(record_id, new_state)

    # ------------------------------------------------------------------
    # RPC: Persistence
    # ------------------------------------------------------------------

    @rpc
    def save_snapshot(self, record_id: str, image_bytes: bytes) -> str | None:
        return self._memory.save_snapshot(record_id, image_bytes)

    @rpc
    def save(self) -> bool:
        return self._memory.save()

    @rpc
    def load(self) -> bool:
        return self._memory.load()

    @rpc
    def clear_all(self) -> int:
        """Clear all landmarks from memory and disk. Returns number removed."""
        n = self._memory.clear_all()
        logger.info("Landmark memory cleared (%d record(s) removed)", n)
        return n
