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

"""Records the Go2 + Mid-360 rig into a memory2 SQLite db.

Captures Point-LIO odom + lidar (trajectory baked into ``pointlio_lidar`` via the
inherited ``@pose_setter_for``) plus the Go2's companion streams. The raw Livox
stream is NOT recorded here — enable the pcap recorder in the record blueprint to
capture it. Companion streams are recorded as-is and anchored via the static mount
frames published on tf.
"""

from __future__ import annotations

from collections import deque
from typing import Any

from dimos.core.stream import In
from dimos.hardware.sensors.lidar.pointlio.recorder import PointlioRecorder
from dimos.memory2.module import OnExisting, Recorder, RecorderConfig, pose_setter_for
from dimos.msgs.geometry_msgs.Pose import Pose
from dimos.msgs.geometry_msgs.PoseStamped import PoseStamped
from dimos.msgs.sensor_msgs.Image import Image
from dimos.msgs.sensor_msgs.PointCloud2 import PointCloud2


class Go2Mid360Recorder(PointlioRecorder):
    go2_lidar: In[PointCloud2]
    go2_odom: In[PoseStamped]
    color_image: In[Image]


class Go2Mid360NavigationRecorderConfig(RecorderConfig):
    """Configuration for an adapted Mid360 navigation-source recording."""

    on_existing: OnExisting = OnExisting.ERROR
    root_frame: str = "world"
    default_frame_id: str = "base_link"


class Go2Mid360NavigationRecorder(Recorder):
    """Record the exact world cloud and base pose consumed by Go2 navigation.

    Unlike :class:`Go2Mid360Recorder`, this recorder does not subscribe to raw
    Point-LIO or Go2 lidar. It attaches the nearest adapted base pose to each
    already-world-registered cloud, which makes the recording suitable for the
    same-source map export and PGO pipeline.
    """

    config: Go2Mid360NavigationRecorderConfig

    lidar: In[PointCloud2]
    odom: In[PoseStamped]

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._odom_history: deque[PoseStamped] = deque(maxlen=256)

    @staticmethod
    def _as_pose(msg: PoseStamped) -> Pose:
        return Pose(position=msg.position, orientation=msg.orientation)

    @pose_setter_for("odom")
    async def _odom_pose(self, msg: PoseStamped) -> Pose:
        self._odom_history.append(msg)
        return self._as_pose(msg)

    @pose_setter_for("lidar")
    async def _lidar_pose(self, msg: PointCloud2) -> Pose | None:
        if not self._odom_history:
            return None
        nearest = min(self._odom_history, key=lambda odom: abs(odom.ts - msg.ts))
        if abs(nearest.ts - msg.ts) > self.config.tf_tolerance:
            return None
        return self._as_pose(nearest)
