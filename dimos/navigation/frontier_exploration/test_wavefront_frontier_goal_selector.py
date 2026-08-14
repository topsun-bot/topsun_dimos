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

from dimos_lcm.std_msgs import Bool

from dimos.navigation.frontier_exploration.wavefront_frontier_goal_selector import (
    WavefrontFrontierExplorer,
)


def test_required_navigation_source_health_stops_and_latches_exploration() -> None:
    explorer = WavefrontFrontierExplorer(require_navigation_source_health=True)
    try:
        assert explorer.explore() is False

        explorer._on_navigation_source_health(Bool(data=True))
        explorer.exploration_active = True
        explorer._on_navigation_source_health(Bool(data=False))

        assert explorer.exploration_active is False
        assert explorer.stop_event.is_set()

        explorer._on_navigation_source_health(Bool(data=True))
        assert explorer.explore() is False
    finally:
        explorer._close_module()
