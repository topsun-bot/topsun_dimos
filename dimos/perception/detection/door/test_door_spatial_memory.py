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

import tempfile
from pathlib import Path

import pytest

from dimos.perception.detection.door.door_spatial_memory import DoorSpatialMemory
from dimos.types.spatial_record import SpatialRecord, RecordType


@pytest.fixture
def memory(tmp_path: Path) -> DoorSpatialMemory:
    return DoorSpatialMemory(db_path=tmp_path / "doors.json", snapshots_dir=tmp_path / "snapshots")


class TestDoorSpatialMemory:
    def test_record_and_get_all(self, memory: DoorSpatialMemory) -> None:
        rec = SpatialRecord(name="front_door", record_type=RecordType.DOOR, position=(0.0, 0.0, 0.0))
        memory.record_door(rec)
        assert memory.count() == 1
        assert memory.get_all_doors()[0].name == "front_door"

    def test_record_room(self, memory: DoorSpatialMemory) -> None:
        rec = SpatialRecord(name="kitchen", record_type=RecordType.ROOM, position=(1.0, 1.0, 0.0))
        memory.record_door(rec)
        assert memory.count() == 1
        assert memory.get_all_doors()[0].record_type == RecordType.ROOM

    def test_dedup_same_position(self, memory: DoorSpatialMemory) -> None:
        r1 = SpatialRecord(name="door_a", position=(0.0, 0.0, 0.0))
        r2 = SpatialRecord(name="door_b", position=(0.3, 0.0, 0.0))

        memory.record_door(r1)
        memory.record_door(r2)

        assert memory.count() == 1
        merged = memory.get_all_doors()[0]
        assert merged.observation_count == 2
        assert merged.name == "door_b"

    def test_no_dedup_different_position(self, memory: DoorSpatialMemory) -> None:
        memory.record_door(SpatialRecord(name="a", position=(0.0, 0.0, 0.0)))
        memory.record_door(SpatialRecord(name="b", position=(5.0, 0.0, 0.0)))
        assert memory.count() == 2

    def test_find_by_name(self, memory: DoorSpatialMemory) -> None:
        memory.record_door(SpatialRecord(name="kitchen_door", record_type=RecordType.DOOR, position=(1.0, 1.0, 0.0)))
        memory.record_door(SpatialRecord(name="kitchen", record_type=RecordType.ROOM, position=(5.0, 0.0, 0.0)))

        assert memory.find_by_name("kitchen_door") is not None
        assert memory.find_by_name("kitchen") is not None
        assert memory.find_by_name("nonexistent") is None

    def test_search_by_name(self, memory: DoorSpatialMemory) -> None:
        memory.record_door(SpatialRecord(name="kitchen_door", position=(1.0, 1.0, 0.0)))
        memory.record_door(SpatialRecord(name="kitchen_sink", position=(2.0, 1.0, 0.0)))
        memory.record_door(SpatialRecord(name="front_door", position=(5.0, 0.0, 0.0)))

        assert len(memory.search_by_name("kitchen")) == 2

    def test_find_nearest(self, memory: DoorSpatialMemory) -> None:
        memory.record_door(SpatialRecord(name="far", position=(10.0, 10.0, 0.0)))
        memory.record_door(SpatialRecord(name="near", position=(1.0, 0.0, 0.0)))

        nearest = memory.find_nearest(0.0, 0.0, radius=5.0)
        assert nearest is not None
        assert nearest.name == "near"
        assert memory.find_nearest(0.0, 0.0, radius=0.5) is None

    def test_query_by_type(self, memory: DoorSpatialMemory) -> None:
        memory.record_door(SpatialRecord(name="kitchen", record_type=RecordType.ROOM, position=(1.0, 1.0, 0.0)))
        memory.record_door(SpatialRecord(name="front_door", record_type=RecordType.DOOR, position=(2.0, 2.0, 0.0)))

        assert len(memory.query_by_type(RecordType.ROOM)) == 1
        assert len(memory.query_by_type(RecordType.DOOR)) == 1

    def test_query_by_state(self, memory: DoorSpatialMemory) -> None:
        memory.record_door(SpatialRecord(name="open_door", state="open", position=(0.0, 0.0, 0.0)))
        memory.record_door(SpatialRecord(name="closed_door", state="closed", position=(1.0, 0.0, 0.0)))

        assert len(memory.query_by_state("open")) == 1
        assert len(memory.query_by_state("closed")) == 1

    def test_update_state(self, memory: DoorSpatialMemory) -> None:
        rec = SpatialRecord(name="test_door", state="unknown", position=(0.0, 0.0, 0.0))
        memory.record_door(rec)

        assert memory.update_state(rec.record_id, "open")
        assert memory.get_by_id(rec.record_id).state == "open"
        assert not memory.update_state("nonexistent", "open")

    def test_get_by_id(self, memory: DoorSpatialMemory) -> None:
        rec = SpatialRecord(name="test", position=(0.0, 0.0, 0.0))
        memory.record_door(rec)
        assert memory.get_by_id(rec.record_id) is not None
        assert memory.get_by_id("bad_id") is None

    def test_json_persistence(self, tmp_path: Path) -> None:
        db_path = tmp_path / "doors.json"
        mem = DoorSpatialMemory(db_path=db_path)
        mem.record_door(SpatialRecord(name="saved", record_type=RecordType.DOOR, position=(1.0, 2.0, 0.0)))
        assert mem.save()

        mem2 = DoorSpatialMemory(db_path=db_path)
        assert mem2.load()
        assert mem2.count() == 1
        assert mem2.get_all_doors()[0].name == "saved"

    def test_save_snapshot(self, memory: DoorSpatialMemory) -> None:
        path = memory.save_snapshot("test_rec", b"fake_jpeg_bytes")
        assert path is not None
        assert Path(path).read_bytes() == b"fake_jpeg_bytes"

    def test_load_nonexistent(self, memory: DoorSpatialMemory) -> None:
        assert not memory.load()

    def test_clear_doors_and_rooms_preserves_landmarks(self, memory: DoorSpatialMemory) -> None:
        """Test that clear_doors_and_rooms only removes DOOR and ROOM records."""
        # Add different types of records
        memory.record_door(SpatialRecord(name="front_door", record_type=RecordType.DOOR, position=(1.0, 0.0, 0.0)))
        memory.record_door(SpatialRecord(name="kitchen", record_type=RecordType.ROOM, position=(2.0, 0.0, 0.0)))
        memory.record_door(SpatialRecord(name="landmark_a", record_type=RecordType.LANDMARK, position=(3.0, 0.0, 0.0)))
        memory.record_door(SpatialRecord(name="landmark_b", record_type=RecordType.LANDMARK, position=(4.0, 0.0, 0.0)))

        assert memory.count() == 4

        # Clear only doors and rooms
        removed = memory.clear_doors_and_rooms()
        assert removed == 2
        assert memory.count() == 2

        # Verify landmarks are preserved
        remaining = memory.get_all_doors()
        assert len(remaining) == 2
        assert all(r.record_type == RecordType.LANDMARK for r in remaining)
        assert {r.name for r in remaining} == {"landmark_a", "landmark_b"}

    def test_clear_doors_and_rooms_empty_memory(self, memory: DoorSpatialMemory) -> None:
        """Test clearing when memory is empty."""
        removed = memory.clear_doors_and_rooms()
        assert removed == 0
        assert memory.count() == 0

    def test_clear_doors_and_rooms_only_landmarks(self, memory: DoorSpatialMemory) -> None:
        """Test clearing when only landmarks exist."""
        memory.record_door(SpatialRecord(name="landmark", record_type=RecordType.LANDMARK, position=(1.0, 0.0, 0.0)))
        removed = memory.clear_doors_and_rooms()
        assert removed == 0
        assert memory.count() == 1

    def test_clear_persistent_doors_and_rooms(self, memory: DoorSpatialMemory) -> None:
        """Test that snapshot images for doors/rooms are deleted."""
        # Create records
        door = SpatialRecord(name="door", record_type=RecordType.DOOR, position=(1.0, 0.0, 0.0))
        room = SpatialRecord(name="room", record_type=RecordType.ROOM, position=(2.0, 0.0, 0.0))
        landmark = SpatialRecord(name="landmark", record_type=RecordType.LANDMARK, position=(3.0, 0.0, 0.0))

        memory.record_door(door)
        memory.record_door(room)
        memory.record_door(landmark)

        # Save snapshots
        memory.save_snapshot(door.record_id, b"door_image")
        memory.save_snapshot(room.record_id, b"room_image")
        memory.save_snapshot(landmark.record_id, b"landmark_image")

        # Verify all snapshots exist
        snapshots_dir = Path(memory._snapshots_dir)
        assert (snapshots_dir / f"{door.record_id}.jpg").exists()
        assert (snapshots_dir / f"{room.record_id}.jpg").exists()
        assert (snapshots_dir / f"{landmark.record_id}.jpg").exists()

        # Clear door/room snapshots
        deleted = memory.clear_persistent_doors_and_rooms()
        assert deleted == 2

        # Verify only door/room snapshots are deleted
        assert not (snapshots_dir / f"{door.record_id}.jpg").exists()
        assert not (snapshots_dir / f"{room.record_id}.jpg").exists()
        assert (snapshots_dir / f"{landmark.record_id}.jpg").exists()

    def test_clear_persistent_no_snapshots_dir(self, tmp_path: Path) -> None:
        """Test clearing when snapshots directory doesn't exist."""
        mem = DoorSpatialMemory(db_path=tmp_path / "doors.json", snapshots_dir=tmp_path / "nonexistent")
        deleted = mem.clear_persistent_doors_and_rooms()
        assert deleted == 0
