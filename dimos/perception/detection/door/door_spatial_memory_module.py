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

"""DIMOS Module wrapper for DoorSpatialMemory.

Exposes door memory operations as RPC methods so it can be wired
into blueprints and injected into skill containers via Spec protocols.
"""

from __future__ import annotations

from typing import Any

from dimos.constants import STATE_DIR
from dimos.core.core import rpc
from dimos.core.module import Module, ModuleConfig
from dimos.perception.detection.door.door_spatial_memory import DoorSpatialMemory
from dimos.types.spatial_record import SpatialRecord, RecordType
from dimos.utils.logging_config import setup_logger

_DOOR_MEMORY_DIR = STATE_DIR / "door_memory"

logger = setup_logger()
_DOOR_DB_PATH = _DOOR_MEMORY_DIR / "doors.json"
_SNAPSHOTS_DIR = _DOOR_MEMORY_DIR / "snapshots"


class DoorSpatialMemoryConfig(ModuleConfig):
    db_path: str = str(_DOOR_DB_PATH)
    snapshots_dir: str = str(_SNAPSHOTS_DIR)


class DoorSpatialMemoryModule(Module):
    """DIMOS Module wrapping DoorSpatialMemory for blueprint registration.

    Exposes all door CRUD and query operations as ``@rpc`` methods.
    """

    config: DoorSpatialMemoryConfig

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._memory = DoorSpatialMemory(
            db_path=self.config.db_path,
            snapshots_dir=self.config.snapshots_dir,
        )

    @rpc
    def start(self) -> None:
        """Start the module and load persisted doors."""
        super().start()
        loaded = self._memory.load()
        if loaded:
            logger.info("Loaded %d door(s) from %s", self._memory.count(), self.config.db_path)
        else:
            logger.info("Starting with empty door memory")

    @rpc
    def stop(self) -> None:
        """Persist doors and shut down."""
        self._memory.save()
        super().stop()

    # ------------------------------------------------------------------
    # RPC: CRUD
    # ------------------------------------------------------------------

    @rpc
    def record_door_str(
        self,
        name: str,
        pos_x: float,
        pos_y: float,
        pos_z: float,
        rot_roll: float,
        rot_pitch: float,
        rot_yaw: float,
        record_type: str = "door",
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
        self._memory.record_door(rec)
        return rec.record_id

    @rpc
    def record_door(self, door: SpatialRecord) -> str:
        """Record a spatial record. Returns record_id."""
        self._memory.record_door(door)
        return door.record_id

    @rpc
    def get_all_doors(self) -> list[SpatialRecord]:
        return self._memory.get_all_doors()

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
    def save(self) -> bool:
        return self._memory.save()

    @rpc
    def load(self) -> bool:
        return self._memory.load()
