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

import csv
from pathlib import Path

import chromadb
from chromadb.config import Settings
import numpy as np
import pytest

from dimos.agents_deprecated.memory.visual_memory import VisualMemory
from dimos.utils.cli.spatial_memory_export import (
    _flatten_metadata,
    _write_csv,
    export_chroma_metadata,
    export_spatial_memory,
    export_visual_memory_images,
)


def test_flatten_metadata_and_write_csv(tmp_path: Path) -> None:
    rows = [
        {"collection": "spatial_memory", "id": "f1", **_flatten_metadata({"pos_x": 1.0})},
        {"collection": "spatial_memory", "id": "f2", **_flatten_metadata({"tags": ["a", "b"]})},
    ]
    path = tmp_path / "out.csv"
    _write_csv(path, rows)

    with path.open(encoding="utf-8") as f:
        loaded = list(csv.DictReader(f))
    assert len(loaded) == 2
    assert loaded[0]["pos_x"] == "1.0"
    assert loaded[1]["tags"] == '["a", "b"]'


def test_export_visual_memory_images(tmp_path: Path) -> None:
    pkl_path = tmp_path / "visual_memory.pkl"
    images_dir = tmp_path / "images"

    vm = VisualMemory(output_dir=str(tmp_path / "vm_cache"))
    image = np.zeros((32, 48, 3), dtype=np.uint8)
    image[:, :, 1] = 255
    vm.add("frame_test_001", image)
    vm.save(str(pkl_path))

    count = export_visual_memory_images(pkl_path, images_dir)
    assert count == 1
    assert (images_dir / "frame_test_001.jpg").is_file()


@pytest.mark.self_hosted
def test_export_spatial_memory_integration(tmp_path: Path) -> None:
    """Full Chroma + pkl export (ChromaDB leaves a background thread)."""
    db_path = tmp_path / "chromadb_data"
    client = chromadb.PersistentClient(
        path=str(db_path),
        settings=Settings(anonymized_telemetry=False),
    )
    frames = client.get_or_create_collection("spatial_memory")
    frames.add(
        ids=["frame_test_001"],
        embeddings=[[0.1] * 8],
        metadatas=[{"frame_id": "frame_test_001", "pos_x": 1.0, "pos_y": 2.0, "pos_z": 0.0}],
    )
    rooms = client.get_or_create_collection("spatial_memory_room_images")
    rooms.add(
        ids=["loc_test_001"],
        embeddings=[[0.2] * 8],
        metadatas=[{"location_name": "office", "location_id": "loc_test_001"}],
    )
    pkl_path = tmp_path / "visual_memory.pkl"
    vm = VisualMemory(output_dir=str(tmp_path / "vm_cache"))
    image = np.zeros((48, 64, 3), dtype=np.uint8)
    vm.add("frame_test_001", image)
    vm.add("loc_test_001", image)
    vm.save(str(pkl_path))

    out_dir = tmp_path / "export"
    result = export_spatial_memory(
        db_path=db_path,
        visual_memory_path=pkl_path,
        output_dir=out_dir,
    )

    assert result.images_exported == 2
    assert len(result.collections) == 2

    combined = Path(result.combined_csv_path)
    with combined.open(encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    assert len(rows) == 2
    assert {r["collection"] for r in rows} == {"spatial_memory", "spatial_memory_room_images"}
    assert (out_dir / "images" / "frame_test_001.jpg").is_file()
    assert (out_dir / "export_summary.json").is_file()


@pytest.mark.self_hosted
def test_export_chroma_metadata_only(tmp_path: Path) -> None:
    db_path = tmp_path / "chromadb_data"
    client = chromadb.PersistentClient(
        path=str(db_path),
        settings=Settings(anonymized_telemetry=False),
    )
    col = client.get_or_create_collection("spatial_memory")
    col.add(ids=["frame_only"], embeddings=[[0.0] * 4], metadatas=[{"pos_x": 0.0}])

    stats, combined_csv, ids = export_chroma_metadata(db_path, tmp_path / "csv")

    assert ids == {"frame_only"}
    assert len(stats) == 1
    assert stats[0].row_count == 1
    assert Path(combined_csv).is_file()
