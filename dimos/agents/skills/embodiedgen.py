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

"""Optional @skill facade over the EmbodiedGen catalog. No EmbodiedGen import."""

from __future__ import annotations

from dimos.agents.annotation import skill
from dimos.core.core import rpc
from dimos.core.module import Module
from dimos.simulation.embodiedgen.skill_api import (
    list_assets_text,
    load_asset_text,
    register_asset_text,
    status_text,
)


class EmbodiedGenSkill(Module):
    """Register and describe how to load EmbodiedGen-exported assets into DimOS sim."""

    @rpc
    def start(self) -> None:
        super().start()

    @rpc
    def stop(self) -> None:
        super().stop()

    @skill
    def list_embodiedgen_assets(self) -> str:
        """List EmbodiedGen assets in EMBODIEDGEN_EXPORT_DIR and the DimOS catalog.

        Does not run EmbodiedGen. Returns names plus URDF/MJCF/USD paths.
        """
        return list_assets_text()

    @skill
    def register_embodiedgen_asset(self, path: str, name: str = "") -> str:
        """Copy an EmbodiedGen export folder or URDF/MJCF into the DimOS catalog.

        Args:
            path: Export folder (sample_00/) or a .urdf/.xml/.usd file.
            name: Optional catalog name. Defaults to the EmbodiedGen folder name.
        """
        return register_asset_text(path, name)

    @skill
    def load_embodiedgen_asset(
        self, name: str, x: float = 0.5, y: float = 0.0, z: float = 0.3
    ) -> str:
        """Resolve an EmbodiedGen asset and return the DimOS sim load command.

        MuJoCo cannot hot-add bodies; restart the blueprint with the flag below.
        Genesis entity JSON is included when an MJCF or URDF is present.

        Args:
            name: Catalog or export asset name (see list_embodiedgen_assets).
            x: World X in meters.
            y: World Y in meters.
            z: World Z in meters.
        """
        return load_asset_text(name, x, y, z)

    @skill
    def embodiedgen_status(self) -> str:
        """Report EmbodiedGen paths and whether the optional CLI is on PATH."""
        return status_text()
