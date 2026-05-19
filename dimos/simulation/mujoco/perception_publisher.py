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

"""Publish lidar / camera frames from MuJoCo into sim shared memory."""

from __future__ import annotations

import time
from typing import Any

import mujoco
import numpy as np
import open3d as o3d  # type: ignore[import-untyped]
from numpy.typing import NDArray

from dimos.msgs.sensor_msgs.PointCloud2 import PointCloud2
from dimos.simulation.mujoco.constants import (
    DEPTH_CAMERA_FOV,
    LIDAR_FPS,
    LIDAR_RESOLUTION,
    VIDEO_FPS,
    VIDEO_HEIGHT,
    VIDEO_WIDTH,
)
from dimos.simulation.mujoco.depth_camera import depth_image_to_point_cloud
from dimos.simulation.mujoco.shared_memory import ShmReader

_CAMERA_NAMES = (
    "head_camera",
    "lidar_front_camera",
    "lidar_left_camera",
    "lidar_right_camera",
)


def _camera_ids(model: mujoco.MjModel) -> dict[str, int]:
    ids: dict[str, int] = {}
    for name in _CAMERA_NAMES:
        cid = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_CAMERA, name)
        if cid >= 0:
            ids[name] = cid
    return ids


def raycast_lidar_points(
    model: mujoco.MjModel,
    data: mujoco.MjData,
    *,
    n_azimuth: int = 360,
    z_offsets: tuple[float, ...] = (0.12, 0.28, 0.45),
    max_range: float = 8.0,
    min_range: float = 0.15,
) -> NDArray[Any]:
    """Horizontal lidar-style scan from the robot base pose (world frame)."""
    base = np.asarray(data.qpos[0:3], dtype=np.float64)
    quat = np.asarray(data.qpos[3:7], dtype=np.float64)
    w, x, y, z = quat
    yaw = np.arctan2(2.0 * (w * z + x * y), 1.0 - 2.0 * (y * y + z * z))
    cos_y, sin_y = float(np.cos(yaw)), float(np.sin(yaw))

    geomid = np.zeros(1, dtype=np.int32)
    points: list[list[float]] = []
    for z_off in z_offsets:
        origin = base + np.array([0.0, 0.0, z_off], dtype=np.float64)
        for i in range(n_azimuth):
            angle = 2.0 * np.pi * i / n_azimuth
            dx = cos_y * np.cos(angle) - sin_y * np.sin(angle)
            dy = sin_y * np.cos(angle) + cos_y * np.sin(angle)
            vec = np.array([[dx], [dy], [0.0]], dtype=np.float64)
            dist = mujoco.mj_ray(
                model,
                data,
                origin.reshape(3, 1),
                vec,
                None,
                1,
                -1,
                geomid,
            )
            if min_range < dist < max_range:
                hit = origin + vec.ravel() * dist
                points.append(hit.tolist())

    if not points:
        return np.empty((0, 3), dtype=np.float64)
    return np.asarray(points, dtype=np.float64)


class MujocoPerceptionPublisher:
    """Write odom, lidar, and optional RGB video into ``ShmReader`` buffers."""

    def __init__(
        self,
        model: mujoco.MjModel,
        shm: ShmReader,
        *,
        lidar_fps: float | None = None,
    ) -> None:
        self._model = model
        self._shm = shm
        self._camera_ids = _camera_ids(model)
        self._scene_option = mujoco.MjvOption()
        self._last_video = 0.0
        self._last_lidar = 0.0
        self._video_interval = 1.0 / VIDEO_FPS
        self._lidar_interval = 1.0 / (lidar_fps if lidar_fps is not None else LIDAR_FPS)

        size = (VIDEO_HEIGHT, VIDEO_WIDTH)
        self._rgb_renderer: mujoco.Renderer | None = None
        self._depth_renderers: list[mujoco.Renderer] = []
        if self._camera_ids:
            self._rgb_renderer = mujoco.Renderer(model, height=size[0], width=size[1])
            for _ in range(3):
                r = mujoco.Renderer(model, height=size[0], width=size[1])
                r.enable_depth_rendering()
                self._depth_renderers.append(r)

    def tick(self, data: mujoco.MjData) -> None:
        now = time.time()
        pos = data.qpos[0:3].copy()
        quat = data.qpos[3:7].copy()
        self._shm.write_odom(pos, quat, now)

        if now - self._last_lidar >= self._lidar_interval:
            self._publish_lidar(data, now)
            self._last_lidar = now

        if self._rgb_renderer is not None and now - self._last_video >= self._video_interval:
            head_id = self._camera_ids.get("head_camera")
            if head_id is not None:
                self._rgb_renderer.update_scene(
                    data, camera=head_id, scene_option=self._scene_option
                )
                self._shm.write_video(self._rgb_renderer.render())
                self._last_video = now

    def _publish_lidar(self, data: mujoco.MjData, ts: float) -> None:
        if len(self._camera_ids) >= 3 and len(self._depth_renderers) == 3:
            pts = self._lidar_from_depth_cameras(data)
        else:
            pts = raycast_lidar_points(self._model, data)

        if pts.size == 0:
            return

        pcd = o3d.geometry.PointCloud()
        pcd.points = o3d.utility.Vector3dVector(pts)
        pcd = pcd.voxel_down_sample(voxel_size=LIDAR_RESOLUTION)
        self._shm.write_lidar(PointCloud2(pointcloud=pcd, ts=ts, frame_id="world"))

    def _lidar_from_depth_cameras(self, data: mujoco.MjData) -> NDArray[Any]:
        names = ("lidar_front_camera", "lidar_left_camera", "lidar_right_camera")
        all_points: list[NDArray[Any]] = []
        for name, renderer in zip(names, self._depth_renderers, strict=True):
            cam_id = self._camera_ids.get(name)
            if cam_id is None:
                continue
            renderer.update_scene(data, camera=cam_id, scene_option=self._scene_option)
            depth = renderer.render()
            points = depth_image_to_point_cloud(
                depth,
                data.cam_xpos[cam_id],
                data.cam_xmat[cam_id].reshape(3, 3),
                fov_degrees=DEPTH_CAMERA_FOV,
            )
            if points.size > 0:
                all_points.append(points)
        if not all_points:
            return np.empty((0, 3))
        return np.vstack(all_points)
