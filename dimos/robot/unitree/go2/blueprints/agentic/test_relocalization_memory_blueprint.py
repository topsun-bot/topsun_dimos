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

"""Merge-boundary tests for the relocalization-aware Go2 memory blueprint."""

from dimos.agents.skills.navigation import NavigationSkillContainer
from dimos.mapping.relocalization.module import RelocalizationModule
from dimos.msgs.geometry_msgs.Twist import Twist
from dimos.navigation.movement_manager.movement_manager import MovementManager
from dimos.navigation.navigation_spec import NavigationInterfaceSpec
from dimos.perception.detection.door.door_spatial_memory_module import (
    SpatialLandmarkMemoryModule,
)
from dimos.perception.experimental.spatial_memory_spec import SpatialMemorySpec
from dimos.perception.experimental.spatial_perception import SpatialMemory
from dimos.robot.unitree.go2.blueprints.agentic.unitree_go2_relocalization_memory_agentic_deepseek import (
    unitree_go2_relocalization_memory_agentic_deepseek,
)
from dimos.robot.unitree.go2.connection import GO2Connection
from dimos.robot.unitree.unitree_skill_container import UnitreeSkillContainer
from dimos.spec.utils import spec_annotation_compliance
from dimos.types.relocalization_spec import RelocalizationStateSpec


def _atom(module: type):  # type: ignore[no-untyped-def]
    return next(
        atom
        for atom in unitree_go2_relocalization_memory_agentic_deepseek.active_blueprints
        if atom.module is module
    )


def test_blueprint_contains_one_platform_and_one_memory_stack() -> None:
    modules = [
        atom.module for atom in unitree_go2_relocalization_memory_agentic_deepseek.active_blueprints
    ]

    for module in (
        GO2Connection,
        MovementManager,
        RelocalizationModule,
        SpatialMemory,
        SpatialLandmarkMemoryModule,
        NavigationSkillContainer,
        UnitreeSkillContainer,
    ):
        assert modules.count(module) == 1


def test_navigation_and_memory_resolve_to_jtlinux_platform_chain() -> None:
    navigation_refs = {ref.name: ref for ref in _atom(NavigationSkillContainer).module_refs}
    unitree_refs = {ref.name: ref for ref in _atom(UnitreeSkillContainer).module_refs}

    assert navigation_refs["_navigation"].spec is NavigationInterfaceSpec
    assert navigation_refs["_relocalization"].spec is RelocalizationStateSpec
    assert navigation_refs["_relocalization"].optional is True
    assert unitree_refs["_navigation"].spec is NavigationInterfaceSpec

    movement_streams = {
        (stream.name, stream.type, stream.direction) for stream in _atom(MovementManager).streams
    }
    connection_streams = {
        (stream.name, stream.type, stream.direction) for stream in _atom(GO2Connection).streams
    }
    assert ("cmd_vel", Twist, "out") in movement_streams
    assert ("cmd_vel", Twist, "in") in connection_streams


def test_spatial_memory_matches_runtime_injection_spec() -> None:
    assert spec_annotation_compliance(SpatialMemory, SpatialMemorySpec)
