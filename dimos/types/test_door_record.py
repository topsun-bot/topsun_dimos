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

from dimos.types.door_record import DoorRecord, DoorState
from dimos.types.spatial_record import RecordType, SpatialRecord


class TestSpatialRecord:
    def test_create_door(self) -> None:
        r = SpatialRecord(name="front_door", record_type=RecordType.DOOR, position=(1.0, 2.0, 0.0))
        assert r.name == "front_door"
        assert r.record_type == RecordType.DOOR
        assert r.position == (1.0, 2.0, 0.0)
        assert r.record_id.startswith("rec_")

    def test_create_room(self) -> None:
        r = SpatialRecord(name="kitchen", record_type=RecordType.ROOM, position=(3.0, 4.0, 0.0))
        assert r.record_type == RecordType.ROOM

    def test_position_normalization_2d(self) -> None:
        r = SpatialRecord(position=(3.0, 4.0))
        assert r.position == (3.0, 4.0, 0.0)

    def test_rotation_normalization_single(self) -> None:
        r = SpatialRecord(rotation=(1.5,))
        assert r.rotation == (0.0, 0.0, 1.5)

    def test_json_roundtrip(self) -> None:
        r = SpatialRecord(
            name="kitchen_door",
            record_type=RecordType.DOOR,
            position=(1.0, 2.0, 0.5),
            rotation=(0.0, 0.0, 1.57),
            state="open",
            description="Door to the kitchen",
            confidence=0.87,
            observation_count=3,
        )
        data = r.to_dict()
        restored = SpatialRecord.from_dict(data)
        assert restored.name == r.name
        assert restored.position == r.position
        assert restored.record_type == r.record_type
        assert restored.state == r.state
        assert restored.confidence == r.confidence
        assert restored.observation_count == r.observation_count

    def test_distance_to(self) -> None:
        a = SpatialRecord(position=(0.0, 0.0, 0.0))
        b = SpatialRecord(position=(3.0, 4.0, 0.0))
        assert abs(a.distance_to(b) - 5.0) < 1e-6

    def test_enum_string_conversion(self) -> None:
        data = {
            "name": "test",
            "position": (1.0, 2.0, 0.0),
            "rotation": (0.0, 0.0, 0.0),
            "record_type": "door",
            "state": "open",
        }
        r = SpatialRecord.from_dict(data)
        assert r.record_type == RecordType.DOOR
        assert r.state == "open"

    def test_door_record_backward_compat(self) -> None:
        """DoorRecord alias still works."""
        door = DoorRecord(name="test", position=(1.0, 2.0, 0.0))
        assert isinstance(door, SpatialRecord)
        assert door.record_id.startswith("rec_")

    def test_door_state_enum(self) -> None:
        assert DoorState.OPEN.value == "open"
        assert DoorState.CLOSED.value == "closed"
