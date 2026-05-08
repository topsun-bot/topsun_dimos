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

from dataclasses import dataclass, field
from enum import Enum

from dimos.msgs.geometry_msgs import PoseStamped


class PatrolStateEnum(Enum):
    IDLE = "idle"
    PLANNING = "planning"
    NAVIGATING = "navigating"
    LOITERING = "loitering"
    RECOVERING = "recovering"
    COMPLETED = "completed"


@dataclass
class Waypoint:
    name: str
    pose: PoseStamped
    loiter_time: float = 5.0  # seconds to dwell at this waypoint
    order: int = 0


@dataclass
class PatrolRoute:
    name: str
    waypoints: list[Waypoint] = field(default_factory=list)
    loop: bool = False  # repeat after completion


@dataclass
class PatrolState:
    route: PatrolRoute | None = None
    current_waypoint_index: int = 0
    state: PatrolStateEnum = PatrolStateEnum.IDLE
    visited_waypoints: list[str] = field(default_factory=list)
    start_time: float | None = None
    estimated_completion: float | None = None
    retry_count: int = 0
