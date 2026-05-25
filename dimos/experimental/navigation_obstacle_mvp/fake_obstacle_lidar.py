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

"""Synthetic LiDAR for navigation obstacle MVP when MuJoCo offscreen GL is unavailable."""

from __future__ import annotations

import math
import time
from typing import TYPE_CHECKING, Any

import numpy as np
from numpy.typing import NDArray
import open3d as o3d  # type: ignore[import-untyped]

from dimos.msgs.sensor_msgs.PointCloud2 import PointCloud2
from dimos.simulation.mujoco.constants import LIDAR_FPS, LIDAR_RESOLUTION

if TYPE_CHECKING:
    import mujoco

    from dimos.core.global_config import GlobalConfig
    from dimos.simulation.mujoco.display_env import OffscreenGlBackend

FAKE_LIDAR_LOG_MESSAGE = (
    "FAKE OBSTACLE LIDAR enabled — MuJoCo offscreen GL unavailable; "
    "publishing synthetic MVP obstacle point cloud (demo only)"
)

MVP_OBSTACLE_GEOM_NAMES: tuple[str, ...] = (
    "mvp_threshold_low",
    "mvp_threshold_medium",
    "mvp_threshold_high",
)


def should_enable_fake_obstacle_lidar(
    config: GlobalConfig,
    *,
    offscreen_renderers_available: bool,
    offscreen_backends: list[OffscreenGlBackend],
) -> bool:
    """Return True when synthetic LiDAR should replace failed/off MuJoCo renderers."""
    if offscreen_renderers_available:
        return False
    if not (config.obstacle_crossing_mvp or config.mujoco_navigation_test_obstacles):
        return False
    if config.mujoco_offscreen_gl == "off":
        return True
    return bool(offscreen_backends)


def sample_mvp_obstacle_surface_points(
    model: mujoco.MjModel,
    data: mujoco.MjData,
    geom_names: tuple[str, ...] = MVP_OBSTACLE_GEOM_NAMES,
    *,
    points_per_geom: int = 80,
    rng: np.random.Generator | None = None,
) -> NDArray[Any]:
    """Sample world-frame points on MVP obstacle geoms (no MuJoCo Renderer / GL)."""
    import mujoco

    generator = rng if rng is not None else np.random.default_rng(0)
    blocks: list[NDArray[Any]] = []
    for name in geom_names:
        geom_id = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_GEOM, name)
        if geom_id < 0:
            continue
        center = np.asarray(data.geom_xpos[geom_id], dtype=np.float64)
        half_size = np.asarray(model.geom_size[geom_id], dtype=np.float64)
        offsets = generator.uniform(-1.0, 1.0, size=(points_per_geom, 3))
        if model.geom_type[geom_id] == mujoco.mjtGeom.mjGEOM_CYLINDER:
            offsets[:, 0] *= half_size[0]
            offsets[:, 1] *= half_size[0]
            offsets[:, 2] *= half_size[1]
        else:
            offsets *= half_size[:3]
        blocks.append(center + offsets)
    if not blocks:
        return np.empty((0, 3), dtype=np.float64)
    return np.vstack(blocks)


def _quat_wxyz_to_yaw(quat_wxyz: NDArray[Any]) -> float:
    w, x, y, z = (float(v) for v in quat_wxyz)
    return math.atan2(2.0 * (w * z + x * y), 1.0 - 2.0 * (y * y + z * z))


def build_synthetic_wall_pointcloud(
    robot_pos: NDArray[Any],
    robot_quat_wxyz: NDArray[Any],
    *,
    distance_m: float = 1.5,
    width_m: float = 1.2,
    height_m: float = 0.25,
    grid_n: int = 12,
    ts: float | None = None,
) -> PointCloud2:
    """Build a vertical wall of points ahead of the robot in world frame."""
    yaw = _quat_wxyz_to_yaw(robot_quat_wxyz)
    forward = np.array([math.cos(yaw), math.sin(yaw), 0.0], dtype=np.float64)
    lateral = np.array([-forward[1], forward[0], 0.0], dtype=np.float64)
    wall_center = np.asarray(robot_pos[:3], dtype=np.float64) + forward * distance_m

    u = np.linspace(-width_m / 2.0, width_m / 2.0, grid_n)
    v = np.linspace(0.0, height_m, max(4, grid_n // 2))
    points: list[NDArray[Any]] = []
    for du in u:
        for dv in v:
            points.append(wall_center + lateral * du + np.array([0.0, 0.0, dv]))
    cloud_array = np.vstack(points)
    pcd = o3d.geometry.PointCloud()
    pcd.points = o3d.utility.Vector3dVector(cloud_array)
    pcd = pcd.voxel_down_sample(voxel_size=LIDAR_RESOLUTION)
    return PointCloud2(pointcloud=pcd, ts=ts if ts is not None else time.time(), frame_id="world")


def build_mvp_obstacle_pointcloud(
    model: mujoco.MjModel,
    data: mujoco.MjData,
    ts: float | None = None,
) -> PointCloud2 | None:
    """Build a PointCloud2 from MVP geoms, or a synthetic wall from robot pose."""
    timestamp = ts if ts is not None else time.time()
    points = sample_mvp_obstacle_surface_points(model, data)
    if len(points) == 0:
        return build_synthetic_wall_pointcloud(
            data.qpos[0:3],
            data.qpos[3:7],
            ts=timestamp,
        )

    pcd = o3d.geometry.PointCloud()
    pcd.points = o3d.utility.Vector3dVector(points)
    pcd = pcd.voxel_down_sample(voxel_size=LIDAR_RESOLUTION)
    if len(pcd.points) == 0:
        return None
    return PointCloud2(pointcloud=pcd, ts=timestamp, frame_id="world")


def run_lcm_fake_lidar_publisher(
    *,
    rate_hz: float = float(LIDAR_FPS),
    duration_s: float | None = None,
) -> None:
    """Standalone process: publish synthetic wall PointCloud2 on ``/lidar`` via LCM."""
    import signal

    from dimos.core.transport import LCMTransport

    transport = LCMTransport("/lidar", PointCloud2)
    transport.start()

    stop = False

    def _handle_signal(_signum: int, _frame: object) -> None:
        nonlocal stop
        stop = True

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    # Default Go2 spawn in office1; wall sits on the forward corridor toward MVP geoms.
    robot_pos = np.array([-1.0, 1.0, 0.3], dtype=np.float64)
    robot_quat = np.array([1.0, 0.0, 0.0, 0.0], dtype=np.float64)
    interval = 1.0 / rate_hz
    deadline = time.time() + duration_s if duration_s is not None else None

    print(FAKE_LIDAR_LOG_MESSAGE)
    print(f"Publishing synthetic /lidar at {rate_hz:.1f} Hz (Ctrl-C to stop)")

    while not stop:
        if deadline is not None and time.time() >= deadline:
            break
        msg = build_synthetic_wall_pointcloud(robot_pos, robot_quat)
        transport.broadcast(None, msg)
        time.sleep(interval)

    transport.stop()


if __name__ == "__main__":
    run_lcm_fake_lidar_publisher()
