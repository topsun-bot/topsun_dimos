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
Unified spatial record type — replaces the door-only model with a
generalized record that can represent doors, rooms, or generic landmarks.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Any


class RecordType(Enum):
    DOOR = "door"
    ROOM = "room"
    LANDMARK = "landmark"
    UNKNOWN = "unknown"


@dataclass
class SpatialRecord:
    """A spatial location observed by the robot.

    Stores position, type, state, visual snapshot, and semantic
    information about a point of interest in the environment.

    JSON-serializable via :meth:`to_dict` / :meth:`from_dict`.
    """

    name: str = ""
    record_type: RecordType = RecordType.UNKNOWN
    position: tuple[float, float, float] = (0.0, 0.0, 0.0)
    rotation: tuple[float, float, float] = (0.0, 0.0, 0.0)
    description: str = ""

    record_id: str = field(default_factory=lambda: f"rec_{uuid.uuid4().hex[:8]}")
    confidence: float = 0.0
    image_snapshot_path: str = ""
    timestamp: float = field(default_factory=time.time)
    last_seen: float = field(default_factory=time.time)
    observation_count: int = 1
    session_id: str = ""

    # Type-specific state (e.g. "open"/"closed" for doors)
    state: str = ""

    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if len(self.position) == 2:
            self.position = (self.position[0], self.position[1], 0.0)
        else:
            self.position = tuple(float(v) for v in self.position)

        if len(self.rotation) == 1:
            self.rotation = (0.0, 0.0, self.rotation[0])
        else:
            self.rotation = tuple(float(v) for v in self.rotation)

        if isinstance(self.record_type, str):
            self.record_type = RecordType(self.record_type)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["record_type"] = self.record_type.value
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SpatialRecord:
        return cls(**data)

    def distance_to(self, other: SpatialRecord) -> float:
        return (
            (self.position[0] - other.position[0]) ** 2
            + (self.position[1] - other.position[1]) ** 2
            + (self.position[2] - other.position[2]) ** 2
        ) ** 0.5

    # ------------------------------------------------------------------
    # Backward-compatible alias
    # ------------------------------------------------------------------


DoorRecord = SpatialRecord
