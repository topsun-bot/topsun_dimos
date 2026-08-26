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

from dimos.perception.detection.door.door_spatial_memory import SpatialLandmarkMemory
from dimos.types.spatial_record import RecordType, SpatialRecord


@pytest.fixture
def memory(tmp_path: Path) -> SpatialLandmarkMemory:
    return SpatialLandmarkMemory(
        db_path=tmp_path / "landmarks.json", snapshots_dir=tmp_path / "snapshots"
    )


def test_landmark_objects_same_position_different_names(memory: SpatialLandmarkMemory) -> None:
    pos = (1.0, 0.0, 0.0)
    for name in ("cabinet", "television", "tv stand", "person"):
        memory.record(SpatialRecord(name=name, record_type=RecordType.LANDMARK, position=pos))
    assert memory.count() == 4
    assert memory.find_by_name("cabinet") is not None
    assert memory.find_by_name("person") is not None


def test_resolve_by_query_chinese_matches_english_name(memory: SpatialLandmarkMemory) -> None:
    memory.record(
        SpatialRecord(
            name="computer",
            record_type=RecordType.LANDMARK,
            position=(1.0, 2.0, 0.0),
            state="桌上的显示器",
        )
    )
    hit = memory.resolve_by_query("电脑")
    assert hit is not None
    assert hit.name == "computer"


def test_resolve_by_query_computer_monitor_alias(memory: SpatialLandmarkMemory) -> None:
    memory.record(
        SpatialRecord(
            name="computer monitor",
            record_type=RecordType.LANDMARK,
            position=(7.0, 6.0, 0.0),
        )
    )
    memory.record(
        SpatialRecord(
            name="computer",
            record_type=RecordType.LANDMARK,
            position=(1.0, 1.0, 0.0),
        )
    )
    hit = memory.resolve_by_query("电脑")
    assert hit is not None
    assert hit.name in ("computer", "computer monitor")


def test_resolve_by_query_chinese_stored_name(memory: SpatialLandmarkMemory) -> None:
    memory.record(
        SpatialRecord(
            name="电脑",
            record_type=RecordType.LANDMARK,
            position=(1.0, 2.0, 0.0),
            state="显示器",
        )
    )
    assert memory.resolve_by_query("电脑") is not None
    assert memory.resolve_by_query("computer") is not None


def test_landmark_object_same_name_updates_count(memory: SpatialLandmarkMemory) -> None:
    memory.record(
        SpatialRecord(name="person", record_type=RecordType.LANDMARK, position=(1.0, 0.0, 0.0))
    )
    memory.record(
        SpatialRecord(name="person", record_type=RecordType.LANDMARK, position=(2.0, 0.0, 0.0))
    )
    assert memory.count() == 1
    assert memory.find_by_name("person").observation_count == 2  # type: ignore[union-attr]


def test_module_start_new_memory_clears_persisted_json(tmp_path: Path) -> None:
    """new_memory=True 时 start 应清空 landmarks.json, 避免跨 session 残留."""
    from dimos.perception.detection.door.door_spatial_memory_module import (
        SpatialLandmarkMemoryModule,
    )

    db_path = tmp_path / "landmarks.json"
    snapshots_dir = tmp_path / "snapshots"
    seed = SpatialLandmarkMemory(db_path=db_path, snapshots_dir=snapshots_dir)
    seed.record(SpatialRecord(name="办公室", record_type=RecordType.ROOM, position=(1.0, 2.0, 0.0)))
    assert db_path.exists()
    assert seed.count() == 1

    module = SpatialLandmarkMemoryModule(
        db_path=str(db_path),
        snapshots_dir=str(snapshots_dir),
        new_memory=True,
    )
    module.start()
    try:
        assert module.count() == 0
        assert not db_path.exists()
    finally:
        module.stop()


def test_module_start_keeps_memory_when_new_memory_false(tmp_path: Path) -> None:
    from dimos.perception.detection.door.door_spatial_memory_module import (
        SpatialLandmarkMemoryModule,
    )

    db_path = tmp_path / "landmarks.json"
    snapshots_dir = tmp_path / "snapshots"
    seed = SpatialLandmarkMemory(db_path=db_path, snapshots_dir=snapshots_dir)
    seed.record(SpatialRecord(name="会议室", record_type=RecordType.ROOM, position=(3.0, 4.0, 0.0)))

    module = SpatialLandmarkMemoryModule(
        db_path=str(db_path),
        snapshots_dir=str(snapshots_dir),
        new_memory=False,
    )
    module.start()
    try:
        assert module.count() == 1
        assert module.find_by_name("会议室") is not None
    finally:
        module.stop()


def test_module_stop_before_start_does_not_overwrite_persisted_memory(tmp_path: Path) -> None:
    """A sibling deployment failure must not erase landmarks before load()."""
    from dimos.perception.detection.door.door_spatial_memory_module import (
        SpatialLandmarkMemoryModule,
    )

    db_path = tmp_path / "landmarks.json"
    snapshots_dir = tmp_path / "snapshots"
    seed = SpatialLandmarkMemory(db_path=db_path, snapshots_dir=snapshots_dir)
    seed.record(SpatialRecord(name="跨重启记忆", record_type=RecordType.ROOM))
    original = db_path.read_bytes()

    module = SpatialLandmarkMemoryModule(
        db_path=str(db_path),
        snapshots_dir=str(snapshots_dir),
        new_memory=False,
    )
    module.stop()

    assert db_path.read_bytes() == original
    restored = SpatialLandmarkMemory(db_path=db_path, snapshots_dir=snapshots_dir)
    assert restored.load()
    assert restored.find_by_name("跨重启记忆") is not None
