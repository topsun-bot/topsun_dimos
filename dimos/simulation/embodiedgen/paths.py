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

"""Resolve EmbodiedGen install / export / catalog paths without importing EmbodiedGen."""

from __future__ import annotations

import os
from pathlib import Path

from dimos.core.global_config import GlobalConfig, global_config


def _optional_path(value: str | Path | None) -> Path | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    return Path(text).expanduser()


def embodiedgen_root(config: GlobalConfig | None = None) -> Path | None:
    """EmbodiedGen checkout or install prefix, if configured.

    Resolution order: ``config.embodiedgen_root``, ``EMBODIEDGEN_ROOT``,
    ``DIMOS_EMBODIEDGEN_ROOT``. Never required at import time.
    """
    cfg = config or global_config
    return _optional_path(
        cfg.embodiedgen_root
        or os.environ.get("EMBODIEDGEN_ROOT")
        or os.environ.get("DIMOS_EMBODIEDGEN_ROOT")
    )


def embodiedgen_export_dir(config: GlobalConfig | None = None) -> Path | None:
    """Directory where EmbodiedGen writes URDF/MJCF/USD exports."""
    cfg = config or global_config
    return _optional_path(
        cfg.embodiedgen_export_dir
        or os.environ.get("EMBODIEDGEN_EXPORT_DIR")
        or os.environ.get("DIMOS_EMBODIEDGEN_EXPORT_DIR")
    )


def default_catalog_dir() -> Path:
    """DimOS-managed catalog for registered EmbodiedGen exports."""
    xdg = os.environ.get("XDG_DATA_HOME")
    base = Path(xdg) / "dimos" if xdg else Path.home() / ".local" / "share" / "dimos"
    return base / "embodiedgen"


def catalog_dir(config: GlobalConfig | None = None) -> Path:
    """Catalog root. ``EMBODIEDGEN_CATALOG_DIR`` overrides the default."""
    override = os.environ.get("EMBODIEDGEN_CATALOG_DIR") or os.environ.get(
        "DIMOS_EMBODIEDGEN_CATALOG_DIR"
    )
    if override:
        return Path(override).expanduser()
    cfg = config or global_config
    configured = _optional_path(getattr(cfg, "embodiedgen_catalog_dir", None))
    if configured is not None:
        return configured
    return default_catalog_dir()


def search_roots(
    config: GlobalConfig | None = None,
    *,
    export_dir: Path | None = None,
    include_catalog: bool = True,
) -> list[Path]:
    """Directories to scan for EmbodiedGen assets (existing paths only)."""
    roots: list[Path] = []
    seen: set[Path] = set()

    def _add(path: Path | None) -> None:
        if path is None:
            return
        resolved = path.expanduser()
        if not resolved.exists() or resolved in seen:
            return
        seen.add(resolved)
        roots.append(resolved)

    _add(export_dir)
    _add(embodiedgen_export_dir(config))
    if include_catalog:
        _add(catalog_dir(config))
    return roots
