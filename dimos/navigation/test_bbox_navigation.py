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

from types import SimpleNamespace

from dimos.msgs.geometry_msgs.PoseStamped import PoseStamped
from dimos.navigation.bbox_navigation import BBoxNavigationModule, Config


def test_disabled_detection_clears_locked_goal_state() -> None:
    nav = object.__new__(BBoxNavigationModule)
    nav.config = Config(enabled=False)
    nav._goal_locked = True
    nav._last_goal = PoseStamped()

    nav._on_detection(SimpleNamespace())

    assert nav._goal_locked is False
    assert nav._last_goal is None


def test_bbox_navigation_does_not_lock_goals_by_default() -> None:
    nav = object.__new__(BBoxNavigationModule)
    nav.config = Config()
    nav._goal_locked = False
    nav._last_goal = None
    nav._last_goal_publish_time = 0.0

    nav._last_goal = PoseStamped()
    nav._last_goal_publish_time = 0.0
    nav._goal_locked = nav.config.lock_goal_until_detection_lost

    assert nav._goal_locked is False
