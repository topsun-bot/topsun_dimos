#!/usr/bin/env python3
# Copyright 2026 Dimensional Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Lightweight agentic skills for Go2 stairs (no PersonFollow / SpatialMemory / hydra).

``_common_agentic`` pulls in ``NavigationSkillContainer`` (needs ``SpatialMemory``)
and ``PersonFollowSkillContainer`` (needs hydra). This bundle uses
``Go2LocomotionSkillContainer`` for stair/cmd_vel skills and ``UnitreeSkillContainer``
for ``relative_move`` / sport commands.
"""

from dimos.agents.web_human_input import WebInput
from dimos.core.coordination.blueprints import autoconnect
from dimos.robot.unitree.go2.go2_locomotion_skill_container import Go2LocomotionSkillContainer
from dimos.robot.unitree.unitree_skill_container import UnitreeSkillContainer

_stairs_agentic = autoconnect(
    UnitreeSkillContainer.blueprint(),
    Go2LocomotionSkillContainer.blueprint(),
    WebInput.blueprint(),
)

__all__ = ["_stairs_agentic"]
