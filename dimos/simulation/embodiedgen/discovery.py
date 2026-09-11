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

"""Discover and register EmbodiedGen-exported URDF / MJCF / USD assets."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
import shutil
from typing import Literal

from dimos.core.global_config import GlobalConfig
from dimos.simulation.embodiedgen.paths import catalog_dir, search_roots

SimKind = Literal["mujoco", "genesis", "isaac", "urdf"]

_MESH_SUFFIXES = {".obj", ".stl", ".dae", ".glb", ".gltf", ".ply"}
_USD_SUFFIXES = {".usd", ".usda", ".usdc"}
_SKIP_DIR_NAMES = {".git", "__pycache__", ".venv", "node_modules"}


@dataclass(frozen=True)
class EmbodiedGenAsset:
    """One EmbodiedGen-exported object (or a DimOS-registered copy)."""

    name: str
    root: Path
    urdf: Path | None = None
    mjcf: Path | None = None
    usd: Path | None = None
    meshes: tuple[Path, ...] = ()
    source: str = "export"

    def primary_for(self, engine: SimKind) -> Path | None:
        if engine in ("mujoco", "genesis"):
            return self.mjcf or self.urdf
        if engine == "isaac":
            return self.usd
        return self.urdf or self.mjcf

    def files(self) -> list[Path]:
        found: list[Path] = []
        for path in (self.urdf, self.mjcf, self.usd, *self.meshes):
            if path is not None and path.is_file():
                found.append(path)
        return found


@dataclass(frozen=True)
class AssetPlacement:
    """Named asset plus a world-frame translation (meters)."""

    name: str
    pos: tuple[float, float, float] = (0.5, 0.0, 0.3)


@dataclass
class _PartialAsset:
    name: str
    root: Path
    urdf: Path | None = None
    mjcf: Path | None = None
    usd: Path | None = None
    meshes: list[Path] = field(default_factory=list)
    source: str = "export"

    def freeze(self) -> EmbodiedGenAsset:
        return EmbodiedGenAsset(
            name=self.name,
            root=self.root,
            urdf=self.urdf,
            mjcf=self.mjcf,
            usd=self.usd,
            meshes=tuple(self.meshes),
            source=self.source,
        )


def _sanitize_name(name: str) -> str:
    cleaned = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in name.strip())
    cleaned = cleaned.strip("_-")
    if not cleaned:
        raise ValueError("Asset name is empty")
    return cleaned


def _asset_name_from_file(path: Path) -> str:
    """Infer the EmbodiedGen asset name from a typical export path.

    ``outputs/imageto3d/sample_00/result/sample_00.urdf`` → ``sample_00``
    ``outputs/imageto3d/sample_00/mjcf/sample_00.xml`` → ``sample_00``
    """
    if path.parent.name in {"result", "mjcf", "usd", "usda"}:
        parent = path.parent.parent
        if parent.name:
            return _sanitize_name(parent.name)
    return _sanitize_name(path.stem)


def _root_from_file(path: Path) -> Path:
    if path.parent.name in {"result", "mjcf", "usd", "usda"}:
        return path.parent.parent
    return path.parent


def _collect_meshes(root: Path) -> list[Path]:
    meshes: list[Path] = []
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if any(part in _SKIP_DIR_NAMES for part in path.parts):
            continue
        if path.suffix.lower() in _MESH_SUFFIXES:
            meshes.append(path)
    return sorted(meshes)


def _is_likely_mjcf(path: Path) -> bool:
    if path.suffix.lower() != ".xml":
        return False
    try:
        head = path.read_text(encoding="utf-8", errors="ignore")[:2000].lstrip()
    except OSError:
        return False
    lowered = head.lower()
    return "<mujoco" in lowered or "<mujoco " in lowered


def discover_assets(
    roots: list[Path] | None = None,
    *,
    config: GlobalConfig | None = None,
    export_dir: Path | None = None,
    include_catalog: bool = True,
) -> list[EmbodiedGenAsset]:
    """Walk export / catalog trees and return unique EmbodiedGen assets.

    Recognizes the upstream layout (``result/*.urdf``, ``mjcf/*.xml``,
    ``usd/*.usd``) plus a flat folder of URDF/MJCF files.
    """
    scan_roots = (
        roots
        if roots is not None
        else search_roots(config, export_dir=export_dir, include_catalog=include_catalog)
    )
    partials: dict[tuple[str, Path], _PartialAsset] = {}

    def _entry(name: str, root: Path, source: str) -> _PartialAsset:
        key = (name, root.resolve())
        if key not in partials:
            partials[key] = _PartialAsset(name=name, root=root, source=source)
        return partials[key]

    for root in scan_roots:
        resolved_root = root.resolve()
        source = "catalog" if _is_under_catalog(resolved_root, config) else "export"
        if resolved_root.is_file():
            _ingest_file(resolved_root, _entry, source)
            continue
        for path in sorted(resolved_root.rglob("*")):
            if not path.is_file():
                continue
            if any(part in _SKIP_DIR_NAMES for part in path.parts):
                continue
            _ingest_file(path, _entry, source)

    assets: list[EmbodiedGenAsset] = []
    for partial in partials.values():
        if partial.urdf is None and partial.mjcf is None and partial.usd is None:
            continue
        if not partial.meshes:
            partial.meshes = _collect_meshes(partial.root)
        assets.append(partial.freeze())
    assets.sort(key=lambda a: (a.name, str(a.root)))
    return assets


def _is_under_catalog(path: Path, config: GlobalConfig | None) -> bool:
    try:
        path.relative_to(catalog_dir(config).resolve())
        return True
    except ValueError:
        return False


def _ingest_file(
    path: Path,
    entry: Callable[[str, Path, str], _PartialAsset],
    source: str,
) -> None:
    suffix = path.suffix.lower()
    if suffix == ".urdf":
        name = _asset_name_from_file(path)
        rec = entry(name, _root_from_file(path), source)
        if rec.urdf is None:
            rec.urdf = path
        return
    if suffix in _USD_SUFFIXES:
        name = _asset_name_from_file(path)
        rec = entry(name, _root_from_file(path), source)
        if rec.usd is None:
            rec.usd = path
        return
    if _is_likely_mjcf(path):
        name = _asset_name_from_file(path)
        rec = entry(name, _root_from_file(path), source)
        if rec.mjcf is None:
            rec.mjcf = path


def find_asset(
    name: str,
    *,
    config: GlobalConfig | None = None,
    export_dir: Path | None = None,
    include_catalog: bool = True,
) -> EmbodiedGenAsset:
    """Return the first asset matching ``name`` (case-sensitive)."""
    wanted = name.strip()
    matches = [
        asset
        for asset in discover_assets(
            config=config, export_dir=export_dir, include_catalog=include_catalog
        )
        if asset.name == wanted
    ]
    if not matches:
        raise FileNotFoundError(
            f"EmbodiedGen asset {wanted!r} not found. "
            "Register an export with `dimos embodiedgen register` or set "
            "EMBODIEDGEN_EXPORT_DIR."
        )
    # Prefer a catalog copy when both exist.
    catalog_hits = [asset for asset in matches if asset.source == "catalog"]
    return catalog_hits[0] if catalog_hits else matches[0]


def register_asset(
    src: Path,
    *,
    name: str | None = None,
    config: GlobalConfig | None = None,
    dest_root: Path | None = None,
) -> EmbodiedGenAsset:
    """Copy an EmbodiedGen export into the DimOS catalog.

    ``src`` may be an asset folder (``sample_00/``), a ``result/`` dir, or a
    single URDF/MJCF/USD file. Existing catalog entries with the same name
    are replaced.
    """
    src = src.expanduser().resolve()
    if not src.exists():
        raise FileNotFoundError(f"EmbodiedGen export not found: {src}")

    discovered = discover_assets(roots=[src], include_catalog=False)
    if not discovered:
        raise ValueError(
            f"No EmbodiedGen URDF/MJCF/USD found under {src}. "
            "Expected result/*.urdf, mjcf/*.xml, or a .urdf/.xml file."
        )
    asset = discovered[0]
    dest_name = _sanitize_name(name or asset.name)
    catalog = dest_root if dest_root is not None else catalog_dir(config)
    dest = catalog / dest_name
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        shutil.rmtree(dest)
    copy_src = asset.root if asset.root.is_dir() else src.parent
    if copy_src.is_file():
        dest.mkdir(parents=True, exist_ok=True)
        shutil.copy2(copy_src, dest / copy_src.name)
    else:
        shutil.copytree(copy_src, dest)

    registered = discover_assets(roots=[dest], include_catalog=False)
    if not registered:
        raise RuntimeError(f"Register copied files to {dest} but none were discoverable")
    frozen = registered[0]
    return EmbodiedGenAsset(
        name=dest_name,
        root=frozen.root,
        urdf=frozen.urdf,
        mjcf=frozen.mjcf,
        usd=frozen.usd,
        meshes=frozen.meshes,
        source="catalog",
    )


def parse_placements(spec: str) -> list[AssetPlacement]:
    """Parse ``mujoco_embodiedgen_assets`` / CLI placement strings.

    Tokens are ``name`` or ``name@x,y,z``. Separate tokens with commas or
    semicolons: ``mug@1,0,0.2;box``.
    """
    import re

    if not spec or not spec.strip():
        return []
    token_re = re.compile(r"([^\s@,;]+)(?:@(-?[\d.]+)\s*,\s*(-?[\d.]+)\s*,\s*(-?[\d.]+))?")
    placements: list[AssetPlacement] = []
    for match in token_re.finditer(spec):
        name = match.group(1).strip()
        if not name:
            continue
        if match.group(2) is not None:
            pos = (
                float(match.group(2)),
                float(match.group(3)),
                float(match.group(4)),
            )
        else:
            pos = (0.5, 0.0, 0.3)
        placements.append(AssetPlacement(name=name, pos=pos))
    return placements
