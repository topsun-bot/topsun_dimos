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

import math

import numpy as np
import pytest

from dimos.msgs.geometry_msgs.PoseStamped import PoseStamped
from dimos.msgs.geometry_msgs.Quaternion import Quaternion
from dimos.msgs.geometry_msgs.Vector3 import Vector3
from dimos.msgs.nav_msgs.Odometry import Odometry
from dimos.msgs.sensor_msgs.PointCloud2 import PointCloud2
from dimos.robot.unitree.go2.mid360_navigation_source import Go2Mid360NavigationSource
from dimos.robot.unitree.go2.mid360_simulation_adapter import Go2Mid360SimulationAdapter


@pytest.fixture()
def adapter():  # type: ignore[no-untyped-def]
    module = Go2Mid360SimulationAdapter()
    yield module
    module._pending_cloud = None
    module._odom_history.clear()
    module._pointlio_world_from_simulation_world = None
    module._close_module()


def _pose(ts: float, x: float, yaw: float = 0.0) -> PoseStamped:
    return PoseStamped(
        ts=ts,
        frame_id="world",
        position=Vector3(x, 0.0, 0.0),
        orientation=Quaternion.from_euler(Vector3(0.0, 0.0, yaw)),
    )


def test_adapter_requires_explicit_mujoco_mode(adapter) -> None:  # type: ignore[no-untyped-def]
    adapter.config.g.simulation = ""
    with pytest.raises(RuntimeError, match="requires --simulation mujoco"):
        adapter.start()


def test_adapter_publishes_pointlio_body_odometry(adapter) -> None:  # type: ignore[no-untyped-def]
    messages: list[Odometry] = []
    adapter.pointlio_odometry.subscribe(messages.append)

    adapter._on_simulation_odom(_pose(10.0, 2.0, math.pi / 2))

    assert len(messages) == 1
    output = messages[0]
    assert output.ts == pytest.approx(10.0)
    assert output.frame_id == "world"
    assert output.child_frame_id == "mid360_imu_link"
    # Point-LIO initializes world at the first IMU/body pose.
    assert output.position.to_tuple() == pytest.approx((0.0, 0.0, 0.0), abs=1e-6)
    assert output.yaw == pytest.approx(0.0)

    adapter._on_simulation_odom(_pose(10.1, 2.2, math.pi / 2))
    assert messages[1].position.to_tuple() == pytest.approx((0.0, -0.2, 0.0), abs=1e-6)
    assert messages[1].yaw == pytest.approx(0.0)


def test_world_cloud_round_trips_through_production_mid360_source(adapter) -> None:  # type: ignore[no-untyped-def]
    source = Go2Mid360NavigationSource(min_range_m=0.0, voxel_size_m=0.0)
    try:
        adapter_clouds: list[PointCloud2] = []
        adapter_odometry: list[Odometry] = []
        adapter.pointlio_lidar.subscribe(adapter_clouds.append)
        adapter.pointlio_odometry.subscribe(adapter_odometry.append)

        world_points = np.asarray(
            [[3.0, 2.0, 0.3], [1.0, -1.0, -0.2]],
            dtype=np.float32,
        )
        adapter._on_simulation_odom(_pose(20.0, 1.0, math.pi / 4))
        adapter._on_simulation_lidar(
            PointCloud2.from_numpy(
                world_points,
                frame_id="world",
                timestamp=20.05,
                intensities=np.asarray([4.0, 7.0], dtype=np.float32),
            )
        )
        assert adapter_clouds == []

        adapter._on_simulation_odom(_pose(20.1, 1.2, math.pi / 4))
        assert len(adapter_clouds) == 1
        body_cloud = adapter_clouds[0]
        assert body_cloud.frame_id == "mid360_imu_link"
        assert body_cloud.ts == pytest.approx(20.05)
        assert len(adapter_odometry) == 2

        with source._condition:
            source._insert_odom_locked(adapter_odometry[0])
            source._insert_odom_locked(adapter_odometry[1])
            world_to_body = source._interpolate_world_to_pointlio_body_locked(20.05)
        assert world_to_body is not None

        recovered = source._prepare_world_cloud(body_cloud, world_to_body)
        anchor = adapter._pointlio_world_from_simulation_world
        assert anchor is not None
        expected_points = world_points @ anchor[:3, :3].T + anchor[:3, 3]
        assert recovered.points_f32() == pytest.approx(expected_points, abs=1e-5)
        assert recovered.intensities_f32() == pytest.approx([4.0, 7.0])
    finally:
        source._close_module()


def test_unbracketed_cloud_is_replaced_by_latest_without_publish(adapter) -> None:  # type: ignore[no-untyped-def]
    clouds: list[PointCloud2] = []
    adapter.pointlio_lidar.subscribe(clouds.append)
    adapter._on_simulation_odom(_pose(1.0, 0.0))

    first = PointCloud2.from_numpy(
        np.asarray([[1.0, 0.0, 0.0]], dtype=np.float32),
        frame_id="world",
        timestamp=2.0,
    )
    second = PointCloud2.from_numpy(
        np.asarray([[2.0, 0.0, 0.0]], dtype=np.float32),
        frame_id="world",
        timestamp=3.0,
    )
    adapter._on_simulation_lidar(first)
    adapter._on_simulation_lidar(second)

    assert clouds == []
    assert adapter._pending_cloud is second
