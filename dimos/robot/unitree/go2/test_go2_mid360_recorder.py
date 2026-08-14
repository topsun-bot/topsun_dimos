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

import asyncio

import numpy as np
import pytest

from dimos.msgs.geometry_msgs.PoseStamped import PoseStamped
from dimos.msgs.geometry_msgs.Quaternion import Quaternion
from dimos.msgs.geometry_msgs.Vector3 import Vector3
from dimos.msgs.sensor_msgs.PointCloud2 import PointCloud2
from dimos.robot.unitree.go2.go2_mid360_recorder import Go2Mid360NavigationRecorder


def test_navigation_recorder_attaches_nearest_adapted_base_pose() -> None:
    recorder = Go2Mid360NavigationRecorder(tf_tolerance=0.1)
    earlier = PoseStamped(
        ts=10.0,
        frame_id="world",
        position=Vector3(1.0, 0.0, 0.0),
        orientation=Quaternion(0.0, 0.0, 0.0, 1.0),
    )
    later = PoseStamped(
        ts=10.08,
        frame_id="world",
        position=Vector3(2.0, 0.0, 0.0),
        orientation=Quaternion(0.0, 0.0, 0.0, 1.0),
    )
    try:
        asyncio.run(recorder._odom_pose(earlier))
        asyncio.run(recorder._odom_pose(later))
        cloud = PointCloud2.from_numpy(
            np.array([[1.0, 0.0, 0.0]], dtype=np.float32),
            frame_id="world",
            timestamp=10.07,
        )

        pose = asyncio.run(recorder._lidar_pose(cloud))

        assert pose is not None
        assert pose.position.x == pytest.approx(2.0)
    finally:
        recorder.stop()


def test_navigation_recorder_rejects_pose_outside_tolerance() -> None:
    recorder = Go2Mid360NavigationRecorder(tf_tolerance=0.05)
    try:
        asyncio.run(recorder._odom_pose(PoseStamped(ts=10.0, frame_id="world")))
        cloud = PointCloud2.from_numpy(
            np.array([[1.0, 0.0, 0.0]], dtype=np.float32),
            frame_id="world",
            timestamp=10.2,
        )

        assert asyncio.run(recorder._lidar_pose(cloud)) is None
    finally:
        recorder.stop()
