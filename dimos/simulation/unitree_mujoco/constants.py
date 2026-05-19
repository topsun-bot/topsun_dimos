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

from pathlib import Path

import sys

# CycloneDDS domain / interface — domain 1 matches ``simulate_python/config.py``.
UNITREE_DDS_DOMAIN_ID = 1
# Linux: loopback is ``lo``; macOS/BSD: ``lo0``.
UNITREE_DDS_INTERFACE = "lo0" if sys.platform == "darwin" else "lo"

LAUNCHER_PATH = Path(__file__).parent / "launcher.py"

# Faster than default LIDAR_FPS=2 so the first global_costmap appears sooner in sim.
UNITREE_LIDAR_FPS = 5.0
