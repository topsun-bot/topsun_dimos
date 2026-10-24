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

from dimos.core.coordination.blueprints import Blueprint, BlueprintAtom, autoconnect
from dimos.robot.unitree.go2.blueprints.basic.unitree_go2_basic import (
    rerun_config,
    unitree_go2_basic,
)
from dimos.visualization.rerun.bridge import RerunBridgeModule


def _rerun_bridge(blueprint: Blueprint) -> BlueprintAtom:
    (bridge,) = (atom for atom in blueprint.active_blueprints if atom.module is RerunBridgeModule)
    return bridge


def test_basic_stack_does_not_enable_rerun_web() -> None:
    assert _rerun_bridge(unitree_go2_basic).kwargs.get("rerun_web") is not True


def test_later_rerun_bridge_can_enable_web_without_dropping_overrides() -> None:
    blueprint = autoconnect(
        unitree_go2_basic,
        RerunBridgeModule.blueprint(**rerun_config, rerun_web=True),
    )
    bridge = _rerun_bridge(blueprint)
    assert bridge.kwargs["rerun_web"] is True
    assert "world/camera_info" in bridge.kwargs["visual_override"]
