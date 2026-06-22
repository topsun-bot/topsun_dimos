xhu#!/usr/bin/env python3
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

"""Run G1 Orin greeter lite (no nix / no nav). Sport mode R2+A before start.

Without TTS keys or Whisper, modules still start; inject text via::

    dimos topic send /human_input '"你好"'
"""

from dimos.core.coordination.module_coordinator import ModuleCoordinator
from dimos.robot.unitree.g1.blueprints.agentic.unitree_g1_greeter_dds_lite import (
    unitree_g1_greeter_dds_lite,
)

if __name__ == "__main__":
    ModuleCoordinator.build(unitree_g1_greeter_dds_lite).loop()
