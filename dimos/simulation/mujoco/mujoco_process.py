#!/usr/bin/env python3

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

import base64
from collections.abc import Callable
from dataclasses import dataclass
import json
import math
import os
import pickle
import signal
import sys
import time
from typing import Any

import mujoco
import numpy as np
from numpy.typing import NDArray
import open3d as o3d  # type: ignore[import-untyped]

from dimos.core.global_config import GlobalConfig
from dimos.core.torch_cuda_warnings import configure_torch_cuda_warning_filters
from dimos.experimental.navigation_obstacle_mvp.fake_obstacle_lidar import (
    FAKE_LIDAR_LOG_MESSAGE,
    build_mvp_obstacle_pointcloud,
    should_enable_fake_obstacle_lidar,
)
from dimos.experimental.navigation_obstacle_mvp.forward_obstacle_height import MVP_SILLS
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
from dimos.simulation.mujoco.display_env import (
    OffscreenGlBackend,
    check_nvidia_driver_mismatch,
    explain_mujoco_headless,
    launch_passive_with_timeout,
    offscreen_gl_try_order,
    patch_mujoco_renderer_del,
    suppress_repeat_glfw_gl_errors,
    temporary_mujoco_gl,
    temporary_osmesa_software_env,
    temporary_viewer_gl_env,
)
from dimos.simulation.mujoco.model import load_model, load_scene_xml
from dimos.simulation.mujoco.person_on_track import PersonPositionController
from dimos.simulation.mujoco.shared_memory import ShmReader
from dimos.utils.logging_config import setup_logger

logger = setup_logger()

configure_torch_cuda_warning_filters()
patch_mujoco_renderer_del()


@dataclass
class OffscreenRenderers:
    rgb: mujoco.Renderer
    depth: mujoco.Renderer
    depth_left: mujoco.Renderer
    depth_right: mujoco.Renderer
    backend: OffscreenGlBackend


def _create_offscreen_renderers_for_backend(
    model: mujoco.MjModel,
    width: int,
    height: int,
) -> OffscreenRenderers:
    rgb_renderer = mujoco.Renderer(model, height=height, width=width)
    depth_renderer = mujoco.Renderer(model, height=height, width=width)
    depth_renderer.enable_depth_rendering()

    depth_left_renderer = mujoco.Renderer(model, height=height, width=width)
    depth_left_renderer.enable_depth_rendering()

    depth_right_renderer = mujoco.Renderer(model, height=height, width=width)
    depth_right_renderer.enable_depth_rendering()

    return OffscreenRenderers(
        rgb=rgb_renderer,
        depth=depth_renderer,
        depth_left=depth_left_renderer,
        depth_right=depth_right_renderer,
        backend="egl",  # placeholder; caller overwrites
    )


def try_create_offscreen_renderers(
    model: mujoco.MjModel,
    width: int,
    height: int,
    backends: list[OffscreenGlBackend],
) -> OffscreenRenderers | None:
    """Create offscreen renderers; return None when all backends fail or list is empty."""
    if not backends:
        logger.info("Offscreen MuJoCo rendering disabled (mujoco_offscreen_gl=off)")
        return None

    last_error: Exception | None = None
    glfw_errors_suppressed = False
    for backend in backends:
        gl_context = (
            temporary_osmesa_software_env() if backend == "osmesa" else temporary_mujoco_gl(backend)
        )
        with gl_context:
            try:
                renderers = _create_offscreen_renderers_for_backend(model, width, height)
                renderers.backend = backend
                logger.info(
                    "Offscreen MuJoCo renderers ready",
                    backend=backend,
                    width=width,
                    height=height,
                    libgl_always_software=os.environ.get("LIBGL_ALWAYS_SOFTWARE"),
                )
                return renderers
            except Exception as exc:
                last_error = exc
                logger.warning(
                    "Offscreen MuJoCo renderer failed; trying next backend",
                    backend=backend,
                    error_type=type(exc).__name__,
                    error=str(exc),
                )
                if not glfw_errors_suppressed:
                    suppress_repeat_glfw_gl_errors()
                    glfw_errors_suppressed = True

    logger.error(
        "All offscreen MuJoCo GL backends failed; continuing without camera/lidar rendering",
        backends=backends,
        error_type=type(last_error).__name__ if last_error else None,
        error=str(last_error) if last_error else None,
    )
    return None


class MockController:
    """Controller that reads commands from shared memory."""

    _MVP_NEAR_SILL_DISTANCE_M = 0.20
    _MVP_MAX_LINEAR = 0.35
    _MVP_MAX_ANGULAR = 0.45

    def __init__(self, shm_interface: ShmReader, config: GlobalConfig) -> None:
        self.shm = shm_interface
        self._command = np.zeros(3, dtype=np.float32)
        self._limit_mvp_cmd = (
            config.obstacle_crossing_mvp or config.mujoco_navigation_test_obstacles
        )

    def _distance_to_low_sill(self) -> float | None:
        pos = self.shm.read_odom_position()
        if pos is None:
            return None
        low = MVP_SILLS[0]
        return float(math.hypot(pos[0] - low.x, pos[1] - low.y))

    def get_command(self) -> NDArray[Any]:
        """Get the current movement command."""
        cmd_data = self.shm.read_command()
        if cmd_data is not None:
            linear, angular = cmd_data
            # MuJoCo expects [forward, lateral, rotational]
            self._command[0] = linear[0]  # forward/backward
            self._command[1] = linear[1]  # left/right
            self._command[2] = angular[2]  # rotation
            if self._limit_mvp_cmd:
                dist = self._distance_to_low_sill()
                if dist is not None and dist < self._MVP_NEAR_SILL_DISTANCE_M:
                    self._command[0] = np.clip(
                        self._command[0], -self._MVP_MAX_LINEAR, self._MVP_MAX_LINEAR
                    )
                    self._command[1] = np.clip(
                        self._command[1], -self._MVP_MAX_LINEAR, self._MVP_MAX_LINEAR
                    )
                    self._command[2] = np.clip(
                        self._command[2], -self._MVP_MAX_ANGULAR, self._MVP_MAX_ANGULAR
                    )
        result: NDArray[Any] = self._command.copy()
        return result

    def stop(self) -> None:
        """Stop method to satisfy InputController protocol."""
        pass


def _run_simulation(config: GlobalConfig, shm: ShmReader) -> None:
    robot_name = config.robot_model or "unitree_go1"
    if robot_name == "unitree_go2":
        robot_name = "unitree_go1"

    controller = MockController(shm, config)
    scene_xml = load_scene_xml(config)
    model, data = load_model(controller, robot=robot_name, scene_xml=scene_xml, config=config)

    if model is None or data is None:
        raise ValueError("Failed to load MuJoCo model: model or data is None")

    match robot_name:
        case "unitree_go1":
            z = 0.3
        case "unitree_g1":
            z = 0.8
        case _:
            z = 0

    start_pos = config.mujoco_start_pos_float

    data.qpos[0:3] = [start_pos[0], start_pos[1], z]

    mujoco.mj_forward(model, data)

    camera_id = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_CAMERA, "head_camera")
    lidar_camera_id = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_CAMERA, "lidar_front_camera")

    person_position_controller = PersonPositionController(model)

    lidar_left_camera_id = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_CAMERA, "lidar_left_camera")
    lidar_right_camera_id = mujoco.mj_name2id(
        model, mujoco.mjtObj.mjOBJ_CAMERA, "lidar_right_camera"
    )

    shm.signal_ready()

    headless, headless_reason = explain_mujoco_headless(config)
    if headless:
        logger.info(
            "Running MuJoCo simulation headless (no passive viewer window)",
            reason=headless_reason,
        )
    else:
        logger.info(
            "Opening MuJoCo passive viewer (GLX preflight, then launch_passive on main thread)",
            reason=headless_reason,
            display=os.environ.get("DISPLAY"),
            wayland_display=os.environ.get("WAYLAND_DISPLAY"),
            mujoco_gl=os.environ.get("MUJOCO_GL"),
        )

    camera_size = (VIDEO_WIDTH, VIDEO_HEIGHT)
    offscreen_backends = offscreen_gl_try_order(config)

    offscreen_renderers: OffscreenRenderers | None = None
    offscreen_attempted = False
    fake_lidar_enabled = False

    def _ensure_offscreen_renderers() -> None:
        nonlocal offscreen_renderers, offscreen_attempted, fake_lidar_enabled
        if offscreen_attempted:
            return
        offscreen_attempted = True
        offscreen_renderers = try_create_offscreen_renderers(
            model,
            width=camera_size[0],
            height=camera_size[1],
            backends=offscreen_backends,
        )
        fake_lidar_enabled = should_enable_fake_obstacle_lidar(
            config,
            offscreen_renderers_available=offscreen_renderers is not None,
            offscreen_backends=offscreen_backends,
        )
        if fake_lidar_enabled:
            logger.warning(
                FAKE_LIDAR_LOG_MESSAGE,
                mujoco_offscreen_gl=config.mujoco_offscreen_gl,
                offscreen_backends=offscreen_backends,
            )

    if headless:
        _ensure_offscreen_renderers()

    scene_option = mujoco.MjvOption()

    # Timing control
    last_video_time = 0.0
    last_lidar_time = 0.0
    fake_lidar_frame_logged = False
    video_interval = 1.0 / VIDEO_FPS
    lidar_interval = 1.0 / LIDAR_FPS

    def _simulation_step(sync_viewer: Callable[[], None] | None) -> None:
        nonlocal last_video_time, last_lidar_time, fake_lidar_frame_logged

        step_start = time.time()

        for _ in range(config.mujoco_steps_per_frame):
            mujoco.mj_step(model, data)

        person_position_controller.tick(data)

        if sync_viewer is not None:
            sync_viewer()

        pos = data.qpos[0:3].copy()
        quat = data.qpos[3:7].copy()  # (w, x, y, z)
        shm.write_odom(pos, quat, time.time())

        current_time = time.time()

        if offscreen_renderers is not None and current_time - last_video_time >= video_interval:
            offscreen_renderers.rgb.update_scene(data, camera=camera_id, scene_option=scene_option)
            pixels = offscreen_renderers.rgb.render()
            shm.write_video(pixels)
            last_video_time = current_time

        if offscreen_renderers is not None and current_time - last_lidar_time >= lidar_interval:
            offscreen_renderers.depth.update_scene(
                data, camera=lidar_camera_id, scene_option=scene_option
            )
            depth_front = offscreen_renderers.depth.render()

            offscreen_renderers.depth_left.update_scene(
                data, camera=lidar_left_camera_id, scene_option=scene_option
            )
            depth_left = offscreen_renderers.depth_left.render()

            offscreen_renderers.depth_right.update_scene(
                data, camera=lidar_right_camera_id, scene_option=scene_option
            )
            depth_right = offscreen_renderers.depth_right.render()

            shm.write_depth(depth_front, depth_left, depth_right)

            all_points = []
            cameras_data = [
                (
                    depth_front,
                    data.cam_xpos[lidar_camera_id],
                    data.cam_xmat[lidar_camera_id].reshape(3, 3),
                ),
                (
                    depth_left,
                    data.cam_xpos[lidar_left_camera_id],
                    data.cam_xmat[lidar_left_camera_id].reshape(3, 3),
                ),
                (
                    depth_right,
                    data.cam_xpos[lidar_right_camera_id],
                    data.cam_xmat[lidar_right_camera_id].reshape(3, 3),
                ),
            ]

            for depth_image, camera_pos, camera_mat in cameras_data:
                points = depth_image_to_point_cloud(
                    depth_image, camera_pos, camera_mat, fov_degrees=DEPTH_CAMERA_FOV
                )
                if points.size > 0:
                    all_points.append(points)

            if all_points:
                combined_points = np.vstack(all_points)
                pcd = o3d.geometry.PointCloud()
                pcd.points = o3d.utility.Vector3dVector(combined_points)
                pcd = pcd.voxel_down_sample(voxel_size=LIDAR_RESOLUTION)

                lidar_msg = PointCloud2(
                    pointcloud=pcd,
                    ts=time.time(),
                    frame_id="world",
                )
                shm.write_lidar(lidar_msg)

            last_lidar_time = current_time

        if (
            fake_lidar_enabled
            and offscreen_renderers is None
            and current_time - last_lidar_time >= lidar_interval
        ):
            lidar_msg = build_mvp_obstacle_pointcloud(model, data, current_time)
            if lidar_msg is not None:
                shm.write_lidar(lidar_msg)
                if not fake_lidar_frame_logged:
                    n_pts = len(np.asarray(lidar_msg.pointcloud.points))
                    logger.info(
                        "Published first fake obstacle lidar frame",
                        n_points=n_pts,
                    )
                    fake_lidar_frame_logged = True
            last_lidar_time = current_time

        time_until_next_step = model.opt.timestep - (time.time() - step_start)
        if time_until_next_step > 0:
            time.sleep(time_until_next_step)

    def _run_headless_loop() -> None:
        while not shm.should_stop():
            _simulation_step(sync_viewer=None)

    try:
        if headless:
            _run_headless_loop()
        else:
            suppress_repeat_glfw_gl_errors()
            try:
                with temporary_viewer_gl_env():
                    with launch_passive_with_timeout(
                        model,
                        data,
                        show_left_ui=False,
                        show_right_ui=False,
                    ) as m_viewer:
                        logger.info(
                            "MuJoCo passive viewer opened successfully",
                            display=os.environ.get("DISPLAY"),
                            wayland_display=os.environ.get("WAYLAND_DISPLAY"),
                            mujoco_gl=os.environ.get("MUJOCO_GL"),
                        )
                        _ensure_offscreen_renderers()
                        m_viewer.cam.lookat = config.mujoco_camera_position_float[0:3]
                        m_viewer.cam.distance = config.mujoco_camera_position_float[3]
                        m_viewer.cam.azimuth = config.mujoco_camera_position_float[4]
                        m_viewer.cam.elevation = config.mujoco_camera_position_float[5]

                        while m_viewer.is_running() and not shm.should_stop():
                            _simulation_step(sync_viewer=m_viewer.sync)
            except Exception as exc:
                nvidia_hint = check_nvidia_driver_mismatch()
                logger.error(
                    "Failed to open MuJoCo passive viewer; falling back to headless simulation",
                    error_type=type(exc).__name__,
                    error=str(exc),
                    display=os.environ.get("DISPLAY"),
                    wayland_display=os.environ.get("WAYLAND_DISPLAY"),
                    mujoco_gl=os.environ.get("MUJOCO_GL"),
                    nvidia_driver_hint=nvidia_hint,
                    exc_info=True,
                )
                _ensure_offscreen_renderers()
                _run_headless_loop()
    finally:
        person_position_controller.stop()


if __name__ == "__main__":
    global_config = pickle.loads(base64.b64decode(sys.argv[1]))
    shm_names = json.loads(sys.argv[2])

    shm = ShmReader(shm_names)

    def signal_handler(_signum: int, _frame: Any) -> None:
        # Signal the main loop to exit gracefully so the viewer context
        # manager can close the window and clean up resources.
        shm.signal_stop()

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    try:
        _run_simulation(global_config, shm)
    finally:
        shm.cleanup()
