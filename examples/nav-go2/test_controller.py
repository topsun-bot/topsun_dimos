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

from pathlib import Path
import sys
import threading

sys.path.insert(0, str(Path(__file__).parent))

from controller import WaypointFollowerConfig, WaypointFollowerModule

from dimos.msgs.geometry_msgs.PoseStamped import PoseStamped
from dimos.msgs.nav_msgs.Path import Path as NavPath


class DummyController:
    def reset_errors(self) -> None:
        pass


def test_path_frame_odom_uses_latched_path_frame_id() -> None:
    module = WaypointFollowerModule.__new__(WaypointFollowerModule)
    module.config = WaypointFollowerConfig()
    module._lock = threading.Lock()
    module._path = None
    module._path_frame_x = 0.0
    module._path_frame_y = 0.0
    module._path_frame_yaw = 0.0
    module._path_frame_id = "base_link"
    module._controller = DummyController()

    path = NavPath(
        frame_id="camera_link",
        poses=[PoseStamped(frame_id="camera_link", position=[1.0, 0.0, 0.0])],
    )
    module._on_path(path)

    assert module._path_frame_odom().frame_id == "camera_link"
