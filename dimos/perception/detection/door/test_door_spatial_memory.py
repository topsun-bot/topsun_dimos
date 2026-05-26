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

from pathlib import Path

import pytest

from dimos.perception.detection.door.door_spatial_memory import DoorSpatialMemory
from dimos.types.spatial_record import RecordType, SpatialRecord


@pytest.fixture
def memory(tmp_path: Path) -> DoorSpatialMemory:
    return DoorSpatialMemory(db_path=tmp_path / "doors.json", snapshots_dir=tmp_path / "snapshots")


class TestDoorSpatialMemory:
    def test_record_and_get_all(self, memory: DoorSpatialMemory) -> None:
        rec = SpatialRecord(
            name="front_door", record_type=RecordType.DOOR, position=(0.0, 0.0, 0.0)
        )
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
        memory.record_door(
            SpatialRecord(
                name="kitchen_door", record_type=RecordType.DOOR, position=(1.0, 1.0, 0.0)
            )
        )
        memory.record_door(
            SpatialRecord(name="kitchen", record_type=RecordType.ROOM, position=(5.0, 0.0, 0.0))
        )

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
        memory.record_door(
            SpatialRecord(name="kitchen", record_type=RecordType.ROOM, position=(1.0, 1.0, 0.0))
        )
        memory.record_door(
            SpatialRecord(name="front_door", record_type=RecordType.DOOR, position=(2.0, 2.0, 0.0))
        )

        assert len(memory.query_by_type(RecordType.ROOM)) == 1
        assert len(memory.query_by_type(RecordType.DOOR)) == 1

    def test_query_by_state(self, memory: DoorSpatialMemory) -> None:
        memory.record_door(SpatialRecord(name="open_door", state="open", position=(0.0, 0.0, 0.0)))
        memory.record_door(
            SpatialRecord(name="closed_door", state="closed", position=(1.0, 0.0, 0.0))
        )

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
        mem.record_door(
            SpatialRecord(name="saved", record_type=RecordType.DOOR, position=(1.0, 2.0, 0.0))
        )
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
