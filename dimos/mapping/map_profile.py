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

"""Metadata identifying the sensor and preprocessing used to build a map."""

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from typing import Any

MAP_PROFILE_SCHEMA_VERSION = 1
MAP_PROFILE_SUFFIX = ".meta.json"
MID360_POINTLIO_SENSOR_PROFILE = "mid360_pointlio_v1"


def map_profile_path(map_path: Path) -> Path:
    """Return the companion metadata path for a ``.pc2.lcm`` map."""
    return Path(f"{map_path}{MAP_PROFILE_SUFFIX}")


def preprocess_config_hash(config: dict[str, Any]) -> str:
    """Hash a preprocessing configuration using stable canonical JSON."""
    encoded = json.dumps(
        config,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def build_map_profile(
    *,
    map_id: str,
    sensor_profile: str,
    voxel_size: float,
    extrinsic_version: str,
    preprocessing: dict[str, Any],
    source_dataset: str,
) -> dict[str, Any]:
    """Build a validated, reproducible map-profile payload."""
    if not map_id.strip():
        raise ValueError("map_id must not be empty")
    if not sensor_profile.strip():
        raise ValueError("sensor_profile must not be empty")
    if voxel_size <= 0:
        raise ValueError("voxel_size must be positive")
    if not extrinsic_version.strip():
        raise ValueError("extrinsic_version must not be empty")
    return {
        "schema_version": MAP_PROFILE_SCHEMA_VERSION,
        "map_id": map_id,
        "sensor_profile": sensor_profile,
        "preprocess_config_hash": preprocess_config_hash(preprocessing),
        "voxel_size": float(voxel_size),
        "extrinsic_version": extrinsic_version,
        "source_dataset": source_dataset,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "preprocessing": preprocessing,
    }


def validate_map_profile(payload: Any) -> dict[str, Any]:
    """Validate profile fields before they are trusted by navigation."""
    if not isinstance(payload, dict):
        raise ValueError("map profile root must be a JSON object")
    if payload.get("schema_version") != MAP_PROFILE_SCHEMA_VERSION:
        raise ValueError("unsupported map profile schema_version")
    for field in (
        "map_id",
        "sensor_profile",
        "preprocess_config_hash",
        "extrinsic_version",
    ):
        if not isinstance(payload.get(field), str) or not payload[field].strip():
            raise ValueError(f"map profile {field} must be a non-empty string")
    voxel_size = payload.get("voxel_size")
    if not isinstance(voxel_size, (int, float)) or voxel_size <= 0:
        raise ValueError("map profile voxel_size must be positive")
    preprocessing = payload.get("preprocessing")
    if not isinstance(preprocessing, dict):
        raise ValueError("map profile preprocessing must be a JSON object")
    expected_hash = preprocess_config_hash(preprocessing)
    if payload["preprocess_config_hash"] != expected_hash:
        raise ValueError("map profile preprocess_config_hash does not match preprocessing")
    return dict(payload)


def load_map_profile(map_path: Path) -> dict[str, Any] | None:
    """Load a companion map profile, returning ``None`` for legacy maps."""
    profile_path = map_profile_path(map_path)
    if not profile_path.exists():
        return None
    payload = json.loads(profile_path.read_text(encoding="utf-8"))
    return validate_map_profile(payload)


def write_map_profile(map_path: Path, profile: dict[str, Any]) -> Path:
    """Validate and atomically write a map profile beside the map."""
    payload = validate_map_profile(profile)
    profile_path = map_profile_path(map_path)
    temporary = profile_path.with_suffix(f"{profile_path.suffix}.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(profile_path)
    return profile_path
