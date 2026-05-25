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

"""Unit tests for synthetic MVP obstacle LiDAR (no GL / no LCM required)."""

from __future__ import annotations

import numpy as np
import pytest

from dimos.core.global_config import GlobalConfig
from dimos.experimental.navigation_obstacle_mvp.fake_obstacle_lidar import (
    FAKE_LIDAR_LOG_MESSAGE,
    build_mvp_obstacle_pointcloud,
    build_synthetic_wall_pointcloud,
    should_enable_fake_obstacle_lidar,
)


def test_should_enable_fake_lidar_when_gl_failed_and_mvp_on() -> None:
    config = GlobalConfig(
        _env_file=None,
        obstacle_crossing_mvp=True,
        mujoco_offscreen_gl="auto",
    )
    assert (
        should_enable_fake_obstacle_lidar(
            config,
            offscreen_renderers_available=False,
            offscreen_backends=["egl", "osmesa", "glfw"],
        )
        is True
    )


def test_should_not_enable_fake_lidar_when_renderers_work() -> None:
    config = GlobalConfig(_env_file=None, obstacle_crossing_mvp=True)
    assert (
        should_enable_fake_obstacle_lidar(
            config,
            offscreen_renderers_available=True,
            offscreen_backends=["egl", "osmesa", "glfw"],
        )
        is False
    )


def test_should_enable_fake_lidar_when_offscreen_gl_off_and_mvp_on() -> None:
    config = GlobalConfig(
        _env_file=None,
        obstacle_crossing_mvp=True,
        mujoco_offscreen_gl="off",
    )
    assert (
        should_enable_fake_obstacle_lidar(
            config,
            offscreen_renderers_available=False,
            offscreen_backends=[],
        )
        is True
    )


def test_should_not_enable_fake_lidar_without_mvp_flags() -> None:
    config = GlobalConfig(_env_file=None, mujoco_offscreen_gl="off")
    assert (
        should_enable_fake_obstacle_lidar(
            config,
            offscreen_renderers_available=False,
            offscreen_backends=[],
        )
        is False
    )


def test_synthetic_wall_pointcloud_has_points_ahead_of_robot() -> None:
    robot_pos = np.array([-1.0, 1.0, 0.3])
    robot_quat = np.array([1.0, 0.0, 0.0, 0.0])
    cloud = build_synthetic_wall_pointcloud(robot_pos, robot_quat, distance_m=1.5)
    points = np.asarray(cloud.pointcloud.points)
    assert len(points) > 20
    assert np.all(points[:, 0] > robot_pos[0] - 0.5)


@pytest.mark.mujoco
def test_mvp_geom_pointcloud_from_scene() -> None:
    try:
        import mujoco  # noqa: F401
    except ImportError:
        pytest.skip("MuJoCo not installed")

    from dimos.simulation.mujoco.model import load_model, load_scene_xml

    class _ZeroCmd:
        def get_command(self) -> np.ndarray:
            return np.zeros(3, dtype=np.float32)

        def stop(self) -> None:
            return None

    config = GlobalConfig(
        _env_file=None,
        simulation="mujoco",
        robot_model="unitree_go2",
        obstacle_crossing_mvp=True,
    )
    scene_xml = load_scene_xml(config)
    model, data = load_model(_ZeroCmd(), "unitree_go1", scene_xml, config=config)
    import mujoco as mj

    data.qpos[0:3] = [-1.0, 1.0, 0.3]
    mj.mj_forward(model, data)

    cloud = build_mvp_obstacle_pointcloud(model, data)
    assert cloud is not None
    assert len(np.asarray(cloud.pointcloud.points)) > 50


def test_fake_lidar_log_message_is_distinctive() -> None:
    assert "FAKE OBSTACLE LIDAR" in FAKE_LIDAR_LOG_MESSAGE
