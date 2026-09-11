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

"""Thin EmbodiedGen bridge: discover/register exports and load them into DimOS sim.

EmbodiedGen itself is never imported. Generate assets with the separate
HorizonRobotics / topsun-bot EmbodiedGen install, then point DimOS at the
export directory or register a copy into the local catalog.
"""

from dimos.simulation.embodiedgen.compose import (
    ComposedScene,
    apply_embodiedgen_scene,
    attach_asset,
    attach_placements,
    compose_standalone_scene,
    genesis_entities,
    isaac_usd_paths,
    mujoco_vfs_for_config,
)
from dimos.simulation.embodiedgen.discovery import (
    AssetPlacement,
    EmbodiedGenAsset,
    discover_assets,
    find_asset,
    parse_placements,
    register_asset,
)
from dimos.simulation.embodiedgen.paths import (
    catalog_dir,
    embodiedgen_export_dir,
    embodiedgen_root,
)
from dimos.simulation.embodiedgen.runner import cli_status, find_executable, run_cli

__all__ = [
    "AssetPlacement",
    "ComposedScene",
    "EmbodiedGenAsset",
    "apply_embodiedgen_scene",
    "attach_asset",
    "attach_placements",
    "catalog_dir",
    "cli_status",
    "compose_standalone_scene",
    "discover_assets",
    "embodiedgen_export_dir",
    "embodiedgen_root",
    "find_asset",
    "find_executable",
    "genesis_entities",
    "isaac_usd_paths",
    "mujoco_vfs_for_config",
    "parse_placements",
    "register_asset",
    "run_cli",
]
