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

from dimos.navigation.nav_stack.modules.pgo.pgo import PGO
from dimos.robot.unitree.g1.blueprints.navigation.unitree_g1_nav_onboard import (
    unitree_g1_nav_onboard,
)


def test_g1_nav_onboard_keeps_fastlio_cloud_in_sensor_frame() -> None:
    pgo_atoms = [atom for atom in unitree_g1_nav_onboard.active_blueprints if atom.module is PGO]
    assert pgo_atoms
    assert pgo_atoms[0].kwargs.get("unregister_input") is False


def test_g1_nav_onboard_pins_lcm_for_cpp_natives() -> None:
    assert unitree_g1_nav_onboard.global_config_overrides.get("transport") == "lcm"
