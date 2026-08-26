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

import numpy as np
import pytest

from dimos.simulation.mujoco import shared_memory
from dimos.simulation.mujoco.depth_camera import depth_image_to_point_cloud
from dimos.simulation.mujoco.mujoco_process import _step_sleep_duration
from dimos.simulation.mujoco.shared_memory import ShmReader, ShmWriter


def test_depth_projection_uses_numpy_without_changing_world_geometry() -> None:
    depth = np.zeros((2, 2), dtype=np.float32)
    depth[1, 1] = 2.0

    points = depth_image_to_point_cloud(
        depth,
        camera_pos=np.array([1.0, 2.0, 3.0]),
        camera_mat=np.eye(3),
        fov_degrees=90.0,
    )

    np.testing.assert_allclose(points, np.array([[1.0, 2.0, 1.0]]), atol=1e-6)


def test_depth_projection_ignores_invalid_depth_values() -> None:
    depth = np.array([[0.0, np.nan], [np.inf, -1.0]], dtype=np.float32)

    points = depth_image_to_point_cloud(
        depth,
        camera_pos=np.zeros(3),
        camera_mat=np.eye(3),
    )

    assert points.shape == (0, 3)


def test_physics_batch_throttle_accounts_for_every_substep() -> None:
    assert _step_sleep_duration(timestep=0.005, n_steps=7, elapsed=0.010) == pytest.approx(0.025)
    assert _step_sleep_duration(timestep=0.005, n_steps=7, elapsed=0.040) == 0.0


def test_lidar_shared_memory_round_trip_constructs_pointcloud_on_parent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    writer = ShmWriter()
    # Reader and writer normally live in separate processes. Avoid unregistering
    # the creator's handles when this contract test attaches in the same process.
    monkeypatch.setattr(shared_memory, "_unregister", lambda shm: shm)
    reader = ShmReader(writer.shm.to_names())
    points = np.array([[1.0, 2.0, 3.0], [-1.0, 0.5, 0.25]], dtype=np.float64)

    try:
        reader.write_lidar(points, timestamp=123.5, frame_id="world")
        cloud, sequence = writer.read_lidar()

        assert sequence == 1
        assert cloud is not None
        assert cloud.frame_id == "world"
        assert cloud.ts == 123.5
        np.testing.assert_allclose(cloud.points_f32(), points.astype(np.float32))
    finally:
        reader.cleanup()
        writer.cleanup()
