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

"""Lifecycle tests for the patrolling goal stream."""

import asyncio
from unittest.mock import MagicMock

from dimos.navigation.patrolling.module import PatrollingModule


def _module(*, active: bool) -> tuple[PatrollingModule, MagicMock]:
    module = PatrollingModule.__new__(PatrollingModule)
    module._patrol_task = None
    module._latest_pose = MagicMock()
    module._planner_spec = MagicMock()
    module.stop_tool = MagicMock()
    module.goal_request = MagicMock()
    if active:
        module._patrol_task = asyncio.create_task(asyncio.sleep(60.0))
    return module, module.goal_request


def test_shutdown_without_patrol_does_not_publish_current_pose() -> None:
    async def scenario() -> None:
        module, goal_request = _module(active=False)
        await module._stop_patrolling()
        goal_request.publish.assert_not_called()
        module._planner_spec.set_replanning_enabled.assert_called_once_with(True)
        module.stop_tool.assert_called_once_with("start_patrol")

    asyncio.run(scenario())


def test_stopping_active_patrol_publishes_current_pose_after_cancel() -> None:
    async def scenario() -> None:
        module, goal_request = _module(active=True)
        await module._stop_patrolling()
        goal_request.publish.assert_called_once_with(module._latest_pose)
        module._planner_spec.reset_safe_goal_clearance.assert_called_once_with()
        module.stop_tool.assert_called_once_with("start_patrol")

    asyncio.run(scenario())
