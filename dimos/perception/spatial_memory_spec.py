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

from typing import Protocol

import numpy as np

from dimos.spec.utils import Spec
from dimos.types.robot_location import RobotLocation


class SpatialMemorySpec(Spec, Protocol):
    def tag_location(self, robot_location: RobotLocation) -> bool: ...
    def tag_location_with_image(self, robot_location: RobotLocation, image: np.ndarray) -> bool: ...
    def query_tagged_location(self, query: str) -> RobotLocation | None: ...
    def query_location_by_image(self, image: np.ndarray) -> RobotLocation | None: ...
    def query_by_text(self, text: str, limit: int = 5) -> list[dict]: ...  # type: ignore[type-arg]
    def query_by_text_with_images(self, text: str, limit: int = 3) -> list[dict]: ...  # type: ignore[type-arg]
    def get_room_image(self, location_id: str) -> np.ndarray | None: ...
    def get_room_images(self) -> list[dict[str, object]]: ...
    def get_image_by_id(self, frame_id: str) -> np.ndarray | None: ...
    def clear_all(self) -> dict[str, int]: ...
