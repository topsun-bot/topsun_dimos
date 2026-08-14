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
import threading
import time

import numpy as np
import pytest

from dimos.msgs.geometry_msgs.Pose import Pose
from dimos.msgs.geometry_msgs.Quaternion import Quaternion
from dimos.msgs.geometry_msgs.Transform import Transform
from dimos.msgs.geometry_msgs.Vector3 import Vector3
from dimos.msgs.nav_msgs.Odometry import Odometry
from dimos.msgs.sensor_msgs.PointCloud2 import PointCloud2
from dimos.robot.unitree.go2.go2_mid360_static_transforms import (
    ORIN_NAVIGATION_BASE_TO_MID360,
    orin_navigation_base_from_mid360,
    orin_navigation_base_from_pointlio_body,
)
from dimos.robot.unitree.go2.mid360_navigation_source import (
    Go2Mid360NavigationSource,
    _slerp,
)


@pytest.fixture()
def make_module():  # type: ignore[no-untyped-def]
    modules: list[Go2Mid360NavigationSource] = []

    def factory(**kwargs):  # type: ignore[no-untyped-def]
        module = Go2Mid360NavigationSource(**kwargs)
        modules.append(module)
        return module

    yield factory
    for module in reversed(modules):
        # Some tests intentionally leave a latest-wins cloud pending. Release
        # its Open3D tensor before interpreter teardown so leak diagnostics do
        # not turn an otherwise successful test process into exit status 1.
        with module._condition:
            module._pending_cloud = None
            module._odom_history.clear()
        module._close_module()


def _odom(ts: float, transform: Transform) -> Odometry:
    return Odometry(
        ts=ts,
        frame_id="world",
        child_frame_id="mid360_imu_link",
        pose=Pose(transform.translation, transform.rotation),
    )


def test_orin_navigation_extrinsic_matches_verified_composition() -> None:
    transform = orin_navigation_base_from_mid360()
    expected_translation, expected_rpy = ORIN_NAVIGATION_BASE_TO_MID360
    assert transform.translation.to_tuple() == pytest.approx(expected_translation, abs=1e-8)
    assert transform.rotation.to_euler().to_tuple() == pytest.approx(expected_rpy, abs=1e-8)
    assert transform.to_matrix() == pytest.approx(
        np.array(
            [
                [0.974370065, 0.0, 0.224951054, 0.160636770],
                [0.0, 1.0, 0.0, -0.023290000],
                [-0.224951054, 0.0, 0.974370065, 0.168083669],
                [0.0, 0.0, 0.0, 1.0],
            ]
        ),
        abs=1e-8,
    )


def test_world_to_base_removes_sensor_mount_offset(make_module) -> None:  # type: ignore[no-untyped-def]
    module = make_module()
    base_to_body = orin_navigation_base_from_pointlio_body()
    world_to_base = module._world_to_base(_odom(time.time(), base_to_body))

    assert world_to_base.translation.to_tuple() == pytest.approx((0.0, 0.0, 0.0), abs=1e-8)
    assert world_to_base.rotation.to_tuple() == pytest.approx((0.0, 0.0, 0.0, 1.0), abs=1e-8)


def test_odom_interpolation_uses_position_and_shortest_quaternion_path(
    make_module,  # type: ignore[no-untyped-def]
) -> None:
    module = make_module()
    first = Transform(
        translation=Vector3(0.0, 0.0, 0.0),
        rotation=Quaternion(),
        frame_id="world",
        child_frame_id="mid360_imu_link",
        ts=10.0,
    )
    second = Transform(
        translation=Vector3(2.0, 0.0, 0.0),
        rotation=Quaternion.from_euler(Vector3(0.0, 0.0, math.pi / 2)),
        frame_id="world",
        child_frame_id="mid360_imu_link",
        ts=10.1,
    )
    with module._condition:
        module._insert_odom_locked(_odom(10.0, first))
        module._insert_odom_locked(_odom(10.1, second))
        interpolated = module._interpolate_world_to_pointlio_body_locked(10.05)

    assert interpolated is not None
    assert interpolated.translation.to_tuple() == pytest.approx((1.0, 0.0, 0.0))
    assert interpolated.rotation.to_euler().z == pytest.approx(math.pi / 4)


def test_exact_cloud_timestamp_uses_exact_odom_pose(make_module) -> None:  # type: ignore[no-untyped-def]
    module = make_module()
    exact = Transform(
        translation=Vector3(1.25, -0.5, 0.1),
        rotation=Quaternion.from_euler(Vector3(0.0, 0.0, 0.3)),
        frame_id="world",
        child_frame_id="mid360_imu_link",
        ts=10.0,
    )
    later = Transform(
        translation=Vector3(9.0, 9.0, 9.0),
        frame_id="world",
        child_frame_id="mid360_imu_link",
        ts=10.1,
    )
    with module._condition:
        module._insert_odom_locked(_odom(10.0, exact))
        module._insert_odom_locked(_odom(10.1, later))
        matched = module._interpolate_world_to_pointlio_body_locked(10.0)

    assert matched is not None
    assert matched.translation.to_tuple() == pytest.approx(exact.translation.to_tuple())
    assert matched.rotation.to_tuple() == pytest.approx(exact.rotation.to_tuple())
    assert matched.ts == pytest.approx(10.0)


def test_odom_bracket_outside_tolerance_is_rejected(make_module) -> None:  # type: ignore[no-untyped-def]
    module = make_module(max_odom_bracket_sec=0.1)
    pose = Transform(frame_id="world", child_frame_id="mid360_imu_link")
    with module._condition:
        module._insert_odom_locked(_odom(10.0, pose))
        module._insert_odom_locked(_odom(10.3, pose))
        matched = module._interpolate_world_to_pointlio_body_locked(10.15)

    assert matched is None


def test_slerp_handles_quaternion_double_cover() -> None:
    first = Quaternion(0.0, 0.0, 0.0, 1.0)
    second = Quaternion(0.0, 0.0, 0.0, -1.0)
    assert _slerp(first, second, 0.5).to_tuple() == pytest.approx(first.to_tuple())


def test_pointcloud_processing_filters_robot_body_but_preserves_floor(
    make_module,  # type: ignore[no-untyped-def]
) -> None:
    module = make_module(min_range_m=0.0, voxel_size_m=0.0)
    body_to_base = orin_navigation_base_from_pointlio_body().inverse()
    robot_return = np.asarray(body_to_base.translation.to_tuple(), dtype=np.float32)
    floor_return = np.asarray([1.5, 0.0, -1.0], dtype=np.float32)
    cloud = PointCloud2.from_numpy(
        np.vstack((robot_return, floor_return)),
        frame_id="mid360_imu_link",
        timestamp=20.0,
        intensities=np.asarray([1.0, 2.0], dtype=np.float32),
    )
    world_to_sensor = Transform(
        translation=Vector3(3.0, 4.0, 0.0),
        frame_id="world",
        child_frame_id="mid360_imu_link",
        ts=20.0,
    )

    output = module._prepare_world_cloud(cloud, world_to_sensor)
    points = output.points_f32()
    assert output.frame_id == "world"
    assert output.ts == pytest.approx(20.0)
    assert points.shape == (1, 3)
    assert points[0] == pytest.approx([4.5, 4.0, -1.0])
    assert output.intensities_f32() == pytest.approx([2.0])


def test_latest_cloud_replaces_pending_work_without_queue_growth(
    make_module,  # type: ignore[no-untyped-def]
) -> None:
    module = make_module()
    first = PointCloud2.from_numpy(np.asarray([[1.0, 0.0, 0.0]]), timestamp=1.0)
    second = PointCloud2.from_numpy(np.asarray([[2.0, 0.0, 0.0]]), timestamp=2.0)
    module._on_pointlio_lidar(first)
    module._on_pointlio_lidar(second)
    assert module._pending_cloud is not None
    assert module._pending_cloud[0] is second


def test_cloud_without_later_odom_is_dropped_after_bounded_wait(
    make_module,  # type: ignore[no-untyped-def]
) -> None:
    module = make_module(cloud_wait_timeout_sec=0.02)
    statuses: list[str] = []
    world_clouds: list[PointCloud2] = []
    module.navigation_source_status.subscribe(lambda msg: statuses.append(msg.data))
    module.lidar.subscribe(world_clouds.append)
    pose = Transform(frame_id="world", child_frame_id="mid360_imu_link")
    module._on_pointlio_odometry(_odom(10.0, pose))
    cloud = PointCloud2.from_numpy(
        np.asarray([[1.0, 0.0, 0.0]], dtype=np.float32),
        frame_id="mid360_imu_link",
        timestamp=10.05,
    )

    worker = threading.Thread(target=module._worker_loop, daemon=True)
    with module._condition:
        module._started_monotonic = time.monotonic()
        module._running = True
    worker.start()
    module._on_pointlio_lidar(cloud)

    deadline = time.monotonic() + 1.0
    while module._clouds_dropped_no_odom == 0 and time.monotonic() < deadline:
        time.sleep(0.01)
    with module._condition:
        module._running = False
        module._condition.notify_all()
    worker.join(timeout=1.0)

    assert module._pending_cloud is None
    assert module._clouds_dropped_no_odom == 1
    assert world_clouds == []
    assert statuses[-1].startswith("CLOUD_DROPPED_NO_ODOM: ts=10.050000")
    assert module._fault_latched is False


def test_ray_mapper_streams_publish_only_after_source_gates_pass(
    make_module,  # type: ignore[no-untyped-def]
) -> None:
    module = make_module(min_range_m=0.0, voxel_size_m=0.0)
    mapping_odometry: list[Odometry] = []
    mapping_clouds: list[PointCloud2] = []
    world_clouds: list[PointCloud2] = []
    module.mapping_odometry.subscribe(mapping_odometry.append)
    module.mapping_lidar.subscribe(mapping_clouds.append)
    module.lidar.subscribe(world_clouds.append)

    pose = Transform(frame_id="world", child_frame_id="mid360_imu_link")
    module._on_pointlio_odometry(_odom(10.0, pose))
    module._on_pointlio_odometry(_odom(10.1, pose))
    cloud = PointCloud2.from_numpy(
        np.asarray([[1.0, 0.0, -0.2]], dtype=np.float32),
        frame_id="mid360_imu_link",
        timestamp=10.05,
    )

    worker = threading.Thread(target=module._worker_loop, daemon=True)
    with module._condition:
        module._started_monotonic = time.monotonic()
        module._running = True
    worker.start()
    module._on_pointlio_lidar(cloud)

    deadline = time.monotonic() + 1.0
    while not mapping_clouds and time.monotonic() < deadline:
        time.sleep(0.01)
    with module._condition:
        module._running = False
        module._condition.notify_all()
    worker.join(timeout=1.0)

    assert [msg.ts for msg in mapping_odometry] == [10.05]
    assert mapping_odometry[0].frame_id == "world"
    assert mapping_odometry[0].child_frame_id == "mid360_imu_link"
    assert mapping_odometry[0].position.data == pytest.approx([0.0, 0.0, 0.0])
    assert mapping_clouds == [cloud]
    assert mapping_odometry[0].ts == mapping_clouds[0].ts
    assert mapping_clouds[0].frame_id == "mid360_imu_link"
    assert len(world_clouds) == 1
    assert world_clouds[0].frame_id == "world"


def test_navigation_source_rpc_reports_startup_ready_and_latched_fault(make_module) -> None:  # type: ignore[no-untyped-def]
    module = make_module()

    assert module.is_navigation_source_healthy() is False
    assert module.get_navigation_source_status() == "CREATED"

    module._publish_status("STARTING")
    assert module.get_navigation_source_status() == "STARTING"

    module._mark_ready()
    assert module.is_navigation_source_healthy() is True
    assert module.get_navigation_source_status() == "RUNNING"

    module._latch_fault("ODOM_STALE: age=0.501s")
    assert module.is_navigation_source_healthy() is False
    assert module.get_navigation_source_status() == "ERROR: ODOM_STALE: age=0.501s"


def test_startup_timeout_latches_fault_without_any_sensor_data(make_module) -> None:  # type: ignore[no-untyped-def]
    module = make_module(startup_timeout_sec=0.01)
    health: list[bool] = []
    statuses: list[str] = []
    module.navigation_source_healthy.subscribe(lambda msg: health.append(msg.data))
    module.navigation_source_status.subscribe(lambda msg: statuses.append(msg.data))
    with module._condition:
        module._started_monotonic = time.monotonic() - 0.02
        module._check_stale_locked()

    assert module._fault_latched is True
    assert module.is_navigation_source_healthy() is False
    assert health == [False]
    assert statuses == ["ERROR: STARTUP_TIMEOUT"]


@pytest.mark.parametrize(
    ("stale_source", "expected_reason"),
    [("lidar", "LIDAR_STALE:"), ("odom", "ODOM_STALE:")],
)
def test_running_source_staleness_latches_and_cannot_auto_recover(
    make_module,  # type: ignore[no-untyped-def]
    stale_source: str,
    expected_reason: str,
) -> None:
    module = make_module(lidar_stale_timeout_sec=0.05, odom_stale_timeout_sec=0.05)
    health: list[bool] = []
    statuses: list[str] = []
    module.navigation_source_healthy.subscribe(lambda msg: health.append(msg.data))
    module.navigation_source_status.subscribe(lambda msg: statuses.append(msg.data))
    module._mark_ready()
    now = time.monotonic()
    module._last_lidar_rx_monotonic = now
    module._last_odom_rx_monotonic = now
    if stale_source == "lidar":
        module._last_lidar_rx_monotonic = now - 0.1
    else:
        module._last_odom_rx_monotonic = now - 0.1

    with module._condition:
        module._check_stale_locked()

    assert module._fault_latched is True
    assert health == [True, False]
    assert statuses[-1].startswith(f"ERROR: {expected_reason}")

    pose = Transform(frame_id="world", child_frame_id="mid360_imu_link")
    module._on_pointlio_odometry(_odom(10.0, pose))
    module._on_pointlio_lidar(
        PointCloud2.from_numpy(
            np.asarray([[1.0, 0.0, 0.0]], dtype=np.float32),
            frame_id="mid360_imu_link",
            timestamp=10.0,
        )
    )

    assert module._fault_latched is True
    assert module.is_navigation_source_healthy() is False
    assert len(module._odom_history) == 0
    assert module._pending_cloud is None


def test_running_health_is_republished_for_late_stream_subscribers(make_module) -> None:  # type: ignore[no-untyped-def]
    module = make_module(diagnostics_interval_sec=0.001)
    module._mark_ready()
    health: list[bool] = []
    statuses: list[str] = []
    module.navigation_source_healthy.subscribe(lambda msg: health.append(msg.data))
    module.navigation_source_status.subscribe(lambda msg: statuses.append(msg.data))
    module._last_diagnostics_monotonic = 0.0

    module._record_processing_sample(
        filter_ms=1.0,
        voxel_ms=2.0,
        transform_publish_ms=1.0,
        total_ms=4.0,
        input_points=100,
        output_points=80,
    )

    assert health == [True]
    assert statuses == ["RUNNING"]


def test_odom_timestamp_rollback_latches_fault_and_rejects_pose(
    make_module,  # type: ignore[no-untyped-def]
) -> None:
    module = make_module()
    statuses: list[str] = []
    health: list[bool] = []
    published_poses = []
    module.navigation_source_status.subscribe(lambda msg: statuses.append(msg.data))
    module.navigation_source_healthy.subscribe(lambda msg: health.append(msg.data))
    module.odom.subscribe(published_poses.append)
    pose = Transform(frame_id="world", child_frame_id="mid360_imu_link")

    module._on_pointlio_odometry(_odom(10.0, pose))
    module._on_pointlio_odometry(_odom(9.9, pose))

    assert len(published_poses) == 1
    assert health == [False]
    assert statuses[-1].startswith("ERROR: ODOM_TIMESTAMP_ROLLBACK:")
    assert [odom.ts for odom in module._odom_history] == [10.0]


def test_lidar_timestamp_jump_latches_fault_and_clears_pending_cloud(
    make_module,  # type: ignore[no-untyped-def]
) -> None:
    module = make_module(max_lidar_timestamp_step_sec=0.5)
    statuses: list[str] = []
    health: list[bool] = []
    module.navigation_source_status.subscribe(lambda msg: statuses.append(msg.data))
    module.navigation_source_healthy.subscribe(lambda msg: health.append(msg.data))
    first = PointCloud2.from_numpy(np.asarray([[1.0, 0.0, 0.0]]), timestamp=10.0)
    jumped = PointCloud2.from_numpy(np.asarray([[2.0, 0.0, 0.0]]), timestamp=11.0)

    module._on_pointlio_lidar(first)
    module._on_pointlio_lidar(jumped)

    assert module._pending_cloud is None
    assert health == [False]
    assert statuses[-1].startswith("ERROR: LIDAR_TIMESTAMP_JUMP:")


def test_duplicate_odom_timestamp_is_dropped_without_fault(
    make_module,  # type: ignore[no-untyped-def]
) -> None:
    module = make_module()
    health: list[bool] = []
    published_poses = []
    module.navigation_source_healthy.subscribe(lambda msg: health.append(msg.data))
    module.odom.subscribe(published_poses.append)
    pose = Transform(frame_id="world", child_frame_id="mid360_imu_link")

    module._on_pointlio_odometry(_odom(10.0, pose))
    module._on_pointlio_odometry(_odom(10.0, pose))

    assert len(published_poses) == 1
    assert [odom.ts for odom in module._odom_history] == [10.0]
    assert health == []
    assert module._fault_latched is False


def test_zero_quaternion_is_ignored_only_before_first_valid_odom(
    make_module,  # type: ignore[no-untyped-def]
) -> None:
    module = make_module()
    statuses: list[str] = []
    health: list[bool] = []
    published_poses = []
    module.navigation_source_status.subscribe(lambda msg: statuses.append(msg.data))
    module.navigation_source_healthy.subscribe(lambda msg: health.append(msg.data))
    module.odom.subscribe(published_poses.append)
    placeholder = Transform(
        rotation=Quaternion(0.0, 0.0, 0.0, 0.0),
        frame_id="world",
        child_frame_id="mid360_imu_link",
    )
    valid = Transform(frame_id="world", child_frame_id="mid360_imu_link")

    module._on_pointlio_odometry(_odom(10.0, placeholder))
    module._on_pointlio_odometry(_odom(10.1, valid))

    assert len(published_poses) == 1
    assert [odom.ts for odom in module._odom_history] == [10.1]
    assert statuses == ["WAITING_FOR_VALID_ODOM: ODOM_POSE_INVALID: zero-length orientation"]
    assert health == []
    assert module._fault_latched is False

    module._on_pointlio_odometry(_odom(10.2, placeholder))

    assert len(published_poses) == 1
    assert health == [False]
    assert statuses[-1] == "ERROR: ODOM_POSE_INVALID: zero-length orientation"


@pytest.mark.parametrize(
    "jumped_transform, expected_detail",
    [
        (
            Transform(
                translation=Vector3(1.0, 0.0, 0.0),
                frame_id="world",
                child_frame_id="mid360_imu_link",
            ),
            "translation=1.000m",
        ),
        (
            Transform(
                rotation=Quaternion.from_euler(Vector3(0.0, 0.0, math.pi / 2)),
                frame_id="world",
                child_frame_id="mid360_imu_link",
            ),
            "rotation=90.0deg",
        ),
    ],
)
def test_odom_pose_jump_latches_fault_and_stops_publication(
    make_module,  # type: ignore[no-untyped-def]
    jumped_transform: Transform,
    expected_detail: str,
) -> None:
    module = make_module()
    statuses: list[str] = []
    health: list[bool] = []
    published_poses = []
    module.navigation_source_status.subscribe(lambda msg: statuses.append(msg.data))
    module.navigation_source_healthy.subscribe(lambda msg: health.append(msg.data))
    module.odom.subscribe(published_poses.append)
    initial = Transform(frame_id="world", child_frame_id="mid360_imu_link")

    module._on_pointlio_odometry(_odom(10.0, initial))
    module._on_pointlio_odometry(_odom(10.1, jumped_transform))
    module._on_pointlio_odometry(_odom(10.2, initial))

    assert len(published_poses) == 1
    assert health == [False]
    assert statuses[-1].startswith("ERROR: ODOM_POSE_JUMP:")
    assert expected_detail in statuses[-1]


def test_processing_diagnostics_window_is_bounded(
    make_module,  # type: ignore[no-untyped-def]
) -> None:
    module = make_module(
        min_range_m=0.0,
        voxel_size_m=0.0,
        diagnostics_interval_sec=60.0,
        diagnostics_window_size=2,
    )
    cloud = PointCloud2.from_numpy(
        np.asarray([[1.0, 0.0, 0.0]], dtype=np.float32),
        frame_id="mid360_imu_link",
        timestamp=10.0,
    )
    pose = Transform(frame_id="world", child_frame_id="mid360_imu_link")

    for _ in range(3):
        module._prepare_world_cloud(cloud, pose)

    assert len(module._processing_samples) == 2
    assert module._processing_samples[-1][4:] == (1, 1)
