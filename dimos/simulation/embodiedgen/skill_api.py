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

"""String-returning helpers used by EmbodiedGenSkill and unit tests."""

from __future__ import annotations

from pathlib import Path

from dimos.simulation.embodiedgen.compose import genesis_entities
from dimos.simulation.embodiedgen.discovery import (
    AssetPlacement,
    discover_assets,
    find_asset,
    register_asset,
)
from dimos.simulation.embodiedgen.paths import catalog_dir, embodiedgen_export_dir, embodiedgen_root
from dimos.simulation.embodiedgen.runner import cli_status


def list_assets_text() -> str:
    assets = discover_assets()
    if not assets:
        return (
            "No EmbodiedGen assets found. Generate them with the separate "
            "EmbodiedGen install, then set EMBODIEDGEN_EXPORT_DIR or call "
            "register_embodiedgen_asset."
        )
    lines = [f"{len(assets)} EmbodiedGen asset(s):"]
    for asset in assets:
        kinds = []
        if asset.mjcf is not None:
            kinds.append("mjcf")
        if asset.urdf is not None:
            kinds.append("urdf")
        if asset.usd is not None:
            kinds.append("usd")
        lines.append(
            f"- {asset.name} [{asset.source}] ({', '.join(kinds) or 'unknown'}) root={asset.root}"
        )
    return "\n".join(lines)


def register_asset_text(path: str, name: str = "") -> str:
    dest_name = name.strip() or None
    asset = register_asset(Path(path), name=dest_name)
    return (
        f"Registered {asset.name} in {asset.root}. "
        f"Load with load_embodiedgen_asset({asset.name!r}) or "
        f"dimos --simulation --mujoco-embodiedgen-assets={asset.name} run <blueprint>."
    )


def load_asset_text(name: str, x: float = 0.5, y: float = 0.0, z: float = 0.3) -> str:
    asset = find_asset(name)
    placement = AssetPlacement(name=asset.name, pos=(x, y, z))
    spec = f"{asset.name}@{x:g},{y:g},{z:g}"
    lines = [
        f"Resolved {asset.name} ({asset.source}) at ({x:g}, {y:g}, {z:g}).",
        "MuJoCo (existing Unitree Go2/G1 sim blueprints):",
        f"  dimos --simulation --mujoco-embodiedgen-assets={spec} run unitree-go2",
    ]
    try:
        entities = genesis_entities([placement])
        lines.append(f"Genesis entity: {entities[0]}")
    except ValueError as exc:
        lines.append(f"Genesis: {exc}")
    if asset.usd is not None:
        lines.append(f"Isaac USD: {asset.usd}")
    else:
        lines.append("Isaac: no USD in this export. Convert with EmbodiedGen MeshtoUSDConverter.")
    return "\n".join(lines)


def status_text() -> str:
    status = cli_status()
    root = embodiedgen_root()
    export = embodiedgen_export_dir()
    catalog = catalog_dir()
    return (
        f"embodiedgen_root={root or '(unset)'}\n"
        f"embodiedgen_export_dir={export or '(unset)'}\n"
        f"catalog_dir={catalog}\n"
        f"cli={status.detail}"
    )
