# Copyright 2025-2026 Dimensional Inc.
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

"""Landmark pack import/export for pre-built navigation data.

Pack format:
    fixtures/landmark_packs/<name>/
        manifest.yaml       # metadata
        landmarks.json      # SpatialRecord list
        snapshots/          # optional JPEGs keyed by record_id
"""

from __future__ import annotations

from dataclasses import dataclass, field
import json
from pathlib import Path
import shutil
import time
from typing import Any
import uuid

import yaml

from dimos.constants import DIMOS_PROJECT_ROOT, STATE_DIR
from dimos.utils.logging_config import setup_logger

logger = setup_logger()

LANDMARK_PACKS_DIR = DIMOS_PROJECT_ROOT / "fixtures" / "landmark_packs"
LANDMARK_MEMORY_DIR = STATE_DIR / "landmark_memory"


def _dimos_is_running() -> bool:
    from dimos.core.run_registry import get_most_recent

    return get_most_recent(alive_only=True) is not None


def _default_pack_dir(pack_name: str) -> Path:
    return LANDMARK_PACKS_DIR / pack_name


def list_packs() -> list[str]:
    """List available pack names under fixtures/landmark_packs/."""
    if not LANDMARK_PACKS_DIR.exists():
        return []
    packs: list[str] = []
    for d in sorted(LANDMARK_PACKS_DIR.iterdir()):
        if d.is_dir() and (d / "manifest.yaml").exists():
            packs.append(d.name)
    return packs


def load_manifest(pack_name: str, *, pack_dir: Path | None = None) -> dict[str, Any]:
    """Load and return the manifest.yaml dict for *pack_name*.

    Raises FileNotFoundError if the pack or manifest is missing.
    """
    pdir = pack_dir or _default_pack_dir(pack_name)
    manifest_path = pdir / "manifest.yaml"
    if not manifest_path.exists():
        raise FileNotFoundError(f"Manifest not found: {manifest_path}")
    try:
        manifest = yaml.safe_load(manifest_path.read_text())
    except yaml.YAMLError as e:
        raise ValueError(f"Invalid YAML in manifest: {manifest_path}") from e
    if manifest is None:
        return {}
    if not isinstance(manifest, dict):
        raise ValueError(
            f"Manifest must be a YAML mapping, got {type(manifest).__name__}: {manifest_path}"
        )
    return manifest


def _load_landmarks_json(pack_dir: Path) -> list[dict[str, Any]]:
    path = pack_dir / "landmarks.json"
    if not path.exists():
        raise FileNotFoundError(f"landmarks.json not found: {path}")
    data = json.loads(path.read_text())
    if not isinstance(data, list):
        raise ValueError(f"landmarks.json must be a JSON array, got {type(data).__name__}")
    return data


def _landmark_summary(records: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for rec in records:
        t = rec.get("record_type", "unknown")
        counts[t] = counts.get(t, 0) + 1
    return counts


def _backup_memory_dir() -> Path | None:
    if not LANDMARK_MEMORY_DIR.exists():
        return None
    ts = time.strftime("%Y%m%d_%H%M%S")
    suffix = uuid.uuid4().hex[:6]
    backup = LANDMARK_MEMORY_DIR.with_name(f"landmark_memory.backup.{ts}_{suffix}")
    shutil.copytree(LANDMARK_MEMORY_DIR, backup, symlinks=False)
    logger.info(f"Backed up existing landmark memory → {backup}")
    return backup


@dataclass
class ImportResult:
    pack_name: str
    dest_dir: Path
    backup_dir: Path | None
    record_count: int
    summary: dict[str, int]
    demo_queries: list[str] = field(default_factory=list)


def _normalize_record_types(records: list[dict[str, Any]]) -> None:
    """Lowercase record_type values so they match RecordType enum values."""
    for i, rec in enumerate(records):
        if not isinstance(rec, dict):
            raise ValueError(
                f"landmarks.json element {i} must be a JSON object, got {type(rec).__name__}"
            )
        rt = rec.get("record_type")
        if isinstance(rt, str):
            rec["record_type"] = rt.lower()


def import_pack(
    pack_name: str,
    *,
    force: bool = False,
    dry_run: bool = False,
    clear_session_id: bool = True,
    no_backup: bool = False,
    pack_dir: Path | None = None,
) -> ImportResult:
    """Import *pack_name* into STATE_DIR/landmark_memory/.

    Parameters
    ----------
    pack_name: Name of the pack subdirectory under fixtures/landmark_packs/.
    force: If True, skip the overwrite confirmation prompt.
    dry_run: Only print what would be copied, don't actually write.
    clear_session_id: Set every record's session_id to "" on import.
    no_backup: Skip backing up the existing landmark_memory directory.
    pack_dir: Custom pack directory (overrides the default lookup).

    Raises
    ------
    RuntimeError: If dimos is currently running.
    FileNotFoundError: If the pack is missing required files.
    """
    if _dimos_is_running():
        raise RuntimeError(
            "DimOS is currently running. Stop it first (`dimos stop`) before importing a pack."
        )

    pdir = pack_dir or _default_pack_dir(pack_name)
    if not pdir.is_dir():
        raise FileNotFoundError(f"Pack directory not found: {pdir}")

    manifest = load_manifest(pack_name, pack_dir=pdir)
    records = _load_landmarks_json(pdir)
    _normalize_record_types(records)

    if dry_run:
        logger.info("[dry-run] Would copy from %s → %s", pdir, LANDMARK_MEMORY_DIR)
        logger.info("[dry-run] %d records in landmarks.json", len(records))
        for dest in _files_to_copy(pdir):
            logger.info("[dry-run]   %s", dest)
        return ImportResult(
            pack_name=pack_name,
            dest_dir=LANDMARK_MEMORY_DIR,
            backup_dir=None,
            record_count=len(records),
            summary=_landmark_summary(records),
            demo_queries=manifest.get("demo_queries", []),
        )

    if LANDMARK_MEMORY_DIR.exists() and not force:
        logger.warning(
            "landmark_memory/ already exists at %s. Use --force to overwrite.",
            LANDMARK_MEMORY_DIR,
        )
        raise FileExistsError(
            f"landmark_memory/ already exists. Use --force to overwrite, "
            f"or manually remove: {LANDMARK_MEMORY_DIR}"
        )

    backup_dir: Path | None = None
    if not no_backup:
        backup_dir = _backup_memory_dir()

    # Clear existing
    if LANDMARK_MEMORY_DIR.exists():
        shutil.rmtree(LANDMARK_MEMORY_DIR)

    LANDMARK_MEMORY_DIR.mkdir(parents=True, exist_ok=True)

    # Process and write landmarks.json with session_id cleared
    if clear_session_id:
        for rec in records:
            rec["session_id"] = ""

    dest_json = LANDMARK_MEMORY_DIR / "landmarks.json"
    dest_json.write_text(json.dumps(records, indent=2, ensure_ascii=False))

    # Copy snapshots if present
    src_snapshots = pdir / "snapshots"
    if src_snapshots.is_dir():
        dest_snapshots = LANDMARK_MEMORY_DIR / "snapshots"
        shutil.copytree(src_snapshots, dest_snapshots, symlinks=False)

    summary = _landmark_summary(records)
    logger.info(
        "Imported pack '%s': %d records (%s) → %s",
        pack_name,
        len(records),
        summary,
        LANDMARK_MEMORY_DIR,
    )

    return ImportResult(
        pack_name=pack_name,
        dest_dir=LANDMARK_MEMORY_DIR,
        backup_dir=backup_dir,
        record_count=len(records),
        summary=summary,
        demo_queries=manifest.get("demo_queries", []),
    )


def _files_to_copy(pack_dir: Path) -> list[str]:
    files: list[str] = ["landmarks.json"]
    snapshots = pack_dir / "snapshots"
    if snapshots.is_dir():
        for f in sorted(snapshots.iterdir()):
            if f.is_file():
                files.append(f"snapshots/{f.name}")
    return files


def export_pack(
    pack_name: str,
    *,
    output_dir: Path | None = None,
) -> Path:
    """Export current landmark_memory/ to a pack directory.

    Parameters
    ----------
    pack_name: Name for the new pack.
    output_dir: Custom output directory (default: fixtures/landmark_packs/<pack_name>).

    Returns the path to the created pack directory.
    """
    if not LANDMARK_MEMORY_DIR.exists():
        raise FileNotFoundError(f"No landmark memory to export: {LANDMARK_MEMORY_DIR}")

    json_path = LANDMARK_MEMORY_DIR / "landmarks.json"
    if not json_path.exists():
        raise FileNotFoundError(f"No landmarks.json found in {LANDMARK_MEMORY_DIR}")

    records = json.loads(json_path.read_text())
    if not isinstance(records, list):
        raise ValueError(
            f"landmarks.json must be a JSON array, got {type(records).__name__}"
        )
    if not records:
        raise ValueError("landmarks.json is empty — nothing to export")

    dest = output_dir or _default_pack_dir(pack_name)
    dest.mkdir(parents=True, exist_ok=True)

    # Write landmarks.json
    shutil.copy2(json_path, dest / "landmarks.json")

    # Copy snapshots
    src_snapshots = LANDMARK_MEMORY_DIR / "snapshots"
    if src_snapshots.is_dir():
        dest_snapshots = dest / "snapshots"
        if dest_snapshots.exists():
            shutil.rmtree(dest_snapshots)
        shutil.copytree(src_snapshots, dest_snapshots, symlinks=False)

    # Generate manifest template if missing
    manifest_path = dest / "manifest.yaml"
    if not manifest_path.exists():
        summary = _landmark_summary(records)
        template: dict[str, Any] = {
            "name": pack_name,
            "version": 1,
            "description": "",
            "created_at": time.strftime("%Y-%m-%d"),
            "author": "",
            "scene": {
                "robot": "unitree-go2",
                "mode": "replay",
                "replay_db": "go2_short",
                "simulation": "",
            },
            "origin_note": "Replay 起始位姿需与录包时一致",
            "counts": {
                "rooms": summary.get("room", 0),
                "landmarks": sum(v for k, v in summary.items() if k not in ("room", "unknown")),
            },
            "demo_queries": [],
        }
        manifest_path.write_text(yaml.dump(template, allow_unicode=True, sort_keys=False))

    logger.info("Exported pack '%s' → %s", pack_name, dest)
    return dest


@dataclass
class LandmarkStatus:
    path: Path
    exists: bool
    record_count: int
    summary: dict[str, int]


def status() -> LandmarkStatus:
    """Report the current STATE_DIR landmark_memory status."""
    json_path = LANDMARK_MEMORY_DIR / "landmarks.json"
    if not json_path.exists():
        return LandmarkStatus(path=LANDMARK_MEMORY_DIR, exists=False, record_count=0, summary={})

    try:
        records = json.loads(json_path.read_text())
    except (json.JSONDecodeError, ValueError) as e:
        return LandmarkStatus(
            path=LANDMARK_MEMORY_DIR, exists=True, record_count=0, summary={"error": str(e)}
        )
    if not isinstance(records, list):
        return LandmarkStatus(
            path=LANDMARK_MEMORY_DIR,
            exists=True,
            record_count=0,
            summary={"error": f"landmarks.json must be a JSON array, got {type(records).__name__}"},
        )
    for i, rec in enumerate(records):
        if not isinstance(rec, dict):
            return LandmarkStatus(
                path=LANDMARK_MEMORY_DIR,
                exists=True,
                record_count=len(records),
                summary={"error": f"Element {i} is not a JSON object"},
            )
    return LandmarkStatus(
        path=LANDMARK_MEMORY_DIR,
        exists=True,
        record_count=len(records),
        summary=_landmark_summary(records),
    )
