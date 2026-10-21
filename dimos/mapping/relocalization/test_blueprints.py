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

from dimos.core.coordination.blueprints import Blueprint, BlueprintAtom
from dimos.mapping.ray_tracing.module import RayTracingVoxelMap
from dimos.mapping.relocalization.blueprints import relocalize_mid360
from dimos.mapping.relocalization.lidar.module import LocalMapRelocalization


def _topic(blueprint: Blueprint, atom: BlueprintAtom, port: str) -> str:
    remapped = blueprint.remapping_map.get((atom.name, port), port)
    assert isinstance(remapped, str)
    return remapped


def test_relocalizer_loaded_map_seeds_the_voxel_map() -> None:
    mapper = next(a for a in relocalize_mid360.active_blueprints if a.module is RayTracingVoxelMap)
    reloc = next(a for a in relocalize_mid360.active_blueprints if a.module is LocalMapRelocalization)
    assert _topic(relocalize_mid360, mapper, "loaded_map") == _topic(
        relocalize_mid360, reloc, "loaded_map"
    )
