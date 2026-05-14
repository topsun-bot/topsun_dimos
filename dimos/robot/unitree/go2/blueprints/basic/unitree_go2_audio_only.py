#!/usr/bin/env python3

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

"""Audio-only Unitree Go2 blueprint.

Boots just ``StartupBarkModule`` so the robot speaks ``主人 你好`` over the
on-device TTS (``AudioClient.TtsMaker``, DDS-based). No ``GO2Connection`` is
instantiated, so this blueprint works even when the robot's WebRTC HTTP
signaling service (port 8081) is not running — only DDS (port 9991) is
required.

Usage:
    dimos run unitree-go2-audio-only --robot-ip <robot_ip>

    # Multi-NIC hosts: pin the DDS interface
    ROBOT_INTERFACE=<iface> dimos run unitree-go2-audio-only --robot-ip <robot_ip>
"""

from __future__ import annotations

from dimos.core.coordination.blueprints import autoconnect
from dimos.robot.unitree.go2.modules import StartupBarkModule

unitree_go2_audio_only = autoconnect(
    StartupBarkModule.blueprint(),
).global_config(n_workers=1, robot_model="unitree_go2")

__all__ = ["unitree_go2_audio_only"]
