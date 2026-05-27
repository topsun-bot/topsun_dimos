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

"""Export SpatialMemory ChromaDB metadata and VisualMemory images for inspection."""

from __future__ import annotations

import csv
from dataclasses import asdict, dataclass, field
import json
from pathlib import Path
import re
from typing import Any

import cv2

from dimos.agents_deprecated.memory.visual_memory import VisualMemory
from dimos.constants import DIMOS_PROJECT_ROOT

_DEFAULT_MEMORY_DIR = DIMOS_PROJECT_ROOT / "assets" / "output" / "memory"
_DEFAULT_SPATIAL_DIR = _DEFAULT_MEMORY_DIR / "spatial_memory"
DEFAULT_DB_PATH = _DEFAULT_SPATIAL_DIR / "chromadb_data"
DEFAULT_VISUAL_MEMORY_PATH = _DEFAULT_SPATIAL_DIR / "visual_memory.pkl"
DEFAULT_EXPORT_DIR = _DEFAULT_MEMORY_DIR / "spatial_memory_export"

_SAFE_FILENAME = re.compile(r"[^\w.\-]+", re.UNICODE)


@dataclass
class CollectionExportStats:
    name: str
    row_count: int
    csv_path: str


@dataclass
class SpatialMemoryExportResult:
    output_dir: str
    db_path: str
    visual_memory_path: str
    collections: list[CollectionExportStats] = field(default_factory=list)
    images_exported: int = 0
    images_missing_pkl: bool = False
    combined_csv_path: str = ""


def _safe_image_filename(record_id: str) -> str:
    cleaned = _SAFE_FILENAME.sub("_", record_id.strip())
    return cleaned or "unknown"


def _flatten_metadata(meta: dict[str, Any] | None) -> dict[str, str]:
    if not meta:
        return {}
    out: dict[str, str] = {}
    for key, value in meta.items():
        if value is None:
            out[key] = ""
        elif isinstance(value, (str, int, float, bool)):
            out[key] = str(value)
        else:
            out[key] = json.dumps(value, ensure_ascii=False)
    return out


def _write_csv(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fieldnames: list[str] = []
    seen: set[str] = set()
    for row in rows:
        for key in row:
            if key not in seen:
                seen.add(key)
                fieldnames.append(key)
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _open_chroma_client(db_path: Path) -> Any:
    import chromadb
    from chromadb.config import Settings

    if not db_path.is_dir():
        raise FileNotFoundError(f"ChromaDB directory not found: {db_path}")
    return chromadb.PersistentClient(
        path=str(db_path),
        settings=Settings(anonymized_telemetry=False),
    )


def export_chroma_metadata(
    db_path: Path, csv_dir: Path
) -> tuple[list[CollectionExportStats], str, set[str]]:
    """Export each Chroma collection to CSV; also write combined ``all_collections.csv``."""
    client = _open_chroma_client(db_path)
    csv_dir.mkdir(parents=True, exist_ok=True)

    stats: list[CollectionExportStats] = []
    combined_rows: list[dict[str, str]] = []
    all_ids: set[str] = set()

    for col_ref in client.list_collections():
        name = col_ref.name if hasattr(col_ref, "name") else str(col_ref)
        collection = client.get_collection(name)
        data = collection.get(include=["metadatas"])
        ids: list[str] = data.get("ids") or []
        all_ids.update(ids)
        metas: list[dict[str, Any] | None] = data.get("metadatas") or []

        rows: list[dict[str, str]] = []
        for record_id, meta in zip(ids, metas, strict=False):
            flat = _flatten_metadata(meta)
            row = {"collection": name, "id": record_id, **flat}
            rows.append(row)
            combined_rows.append(row)

        csv_path = csv_dir / f"{name}.csv"
        _write_csv(csv_path, rows)
        stats.append(CollectionExportStats(name=name, row_count=len(rows), csv_path=str(csv_path)))

    combined_path = csv_dir / "all_collections.csv"
    _write_csv(combined_path, combined_rows)
    return stats, str(combined_path), all_ids


def export_visual_memory_images(
    visual_memory_path: Path,
    images_dir: Path,
    *,
    id_filter: set[str] | None = None,
) -> int:
    """Decode ``visual_memory.pkl`` entries to ``images_dir/{id}.jpg``."""
    if not visual_memory_path.is_file():
        return 0

    vm = VisualMemory.load(str(visual_memory_path))
    images_dir.mkdir(parents=True, exist_ok=True)
    exported = 0

    for record_id in vm.images:
        if id_filter is not None and record_id not in id_filter:
            continue
        image = vm.get(record_id)
        if image is None:
            continue
        out_path = images_dir / f"{_safe_image_filename(record_id)}.jpg"
        if cv2.imwrite(str(out_path), image):
            exported += 1

    return exported


def export_spatial_memory(
    *,
    db_path: Path | None = None,
    visual_memory_path: Path | None = None,
    output_dir: Path | None = None,
    export_images: bool = True,
    images_only_in_chroma: bool = False,
) -> SpatialMemoryExportResult:
    """Export SpatialMemory DB to CSV + JPEG under *output_dir*.

    Layout::

        output_dir/
          csv/{collection}.csv
          csv/all_collections.csv
          images/{id}.jpg
          export_summary.json
    """
    resolved_db = Path(db_path or DEFAULT_DB_PATH)
    resolved_pkl = Path(visual_memory_path or DEFAULT_VISUAL_MEMORY_PATH)
    resolved_out = Path(output_dir or DEFAULT_EXPORT_DIR)

    resolved_out.mkdir(parents=True, exist_ok=True)
    csv_dir = resolved_out / "csv"
    images_dir = resolved_out / "images"

    collection_stats, combined_csv, chroma_ids = export_chroma_metadata(resolved_db, csv_dir)

    images_exported = 0
    missing_pkl = not resolved_pkl.is_file()
    if export_images and not missing_pkl:
        id_filter = chroma_ids if images_only_in_chroma else None
        images_exported = export_visual_memory_images(resolved_pkl, images_dir, id_filter=id_filter)

    result = SpatialMemoryExportResult(
        output_dir=str(resolved_out),
        db_path=str(resolved_db),
        visual_memory_path=str(resolved_pkl),
        collections=collection_stats,
        images_exported=images_exported,
        images_missing_pkl=missing_pkl,
        combined_csv_path=combined_csv,
    )
    summary_path = resolved_out / "export_summary.json"
    summary_path.write_text(
        json.dumps(asdict(result), indent=2, ensure_ascii=False), encoding="utf-8"
    )
    return result
