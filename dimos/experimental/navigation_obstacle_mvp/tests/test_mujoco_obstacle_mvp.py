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

"""MuJoCo / sim smoke tests for navigation obstacle MVP (excluded from default pytest)."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import numpy as np
import pytest

from dimos.core.global_config import GlobalConfig
from dimos.e2e_tests.dimos_cli_call import DimosCliCall
from dimos.e2e_tests.lcm_spy import LcmSpy
from dimos.experimental.navigation_obstacle_mvp.tests.sim_test_deps import require_lcm_multicast
from dimos.mapping.pointclouds.occupancy import height_cost_occupancy
from dimos.msgs.geometry_msgs.PoseStamped import PoseStamped
from dimos.msgs.geometry_msgs.Vector3 import Vector3
from dimos.msgs.nav_msgs.Path import Path
from dimos.navigation.replanning_a_star.global_planner import GlobalPlanner
from dimos.navigation.replanning_a_star.path_clearance import PathClearance
from dimos.robot.get_all_blueprints import get_blueprint_by_name

pytest_plugins = ("dimos.e2e_tests.conftest",)


class _ZeroCommandController:
    """Minimal InputController for loading the Go2 MuJoCo model in tests."""

    def get_command(self) -> np.ndarray[Any, Any]:
        return np.zeros(3, dtype=np.float32)

    def stop(self) -> None:
        return None


def _require_mujoco_sim_deps() -> None:
    try:
        import mujoco  # noqa: F401
    except ImportError:
        pytest.skip("MuJoCo not installed. Run: uv sync --extra sim")

    try:
        from mujoco_playground._src import mjx_env

        mjx_env.ensure_menagerie_exists()
        from dimos.utils.data import get_data

        get_data("mujoco_sim")
    except Exception as exc:
        pytest.skip(f"MuJoCo sim assets unavailable: {exc}")


def _obstacle_mvp_sim_config() -> GlobalConfig:
    return GlobalConfig(
        _env_file=None,
        simulation="mujoco",
        robot_model="unitree_go2",
        viewer="none",
        obstacle_crossing_mvp=True,
        obstacle_crossing_mvp_height_cm=8.0,
    )


def _sample_mvp_obstacle_surface_points(
    model: Any,
    data: Any,
    geom_names: tuple[str, ...],
    *,
    points_per_geom: int = 80,
) -> np.ndarray[Any, Any]:
    """Sample world-frame points on MVP obstacle geoms (no MuJoCo Renderer / GL)."""
    import mujoco

    rng = np.random.default_rng(0)
    blocks: list[np.ndarray[Any, Any]] = []
    for name in geom_names:
        geom_id = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_GEOM, name)
        center = np.asarray(data.geom_xpos[geom_id], dtype=np.float64)
        half_size = np.asarray(model.geom_size[geom_id], dtype=np.float64)
        offsets = rng.uniform(-1.0, 1.0, size=(points_per_geom, 3))
        if model.geom_type[geom_id] == mujoco.mjtGeom.mjGEOM_CYLINDER:
            offsets[:, 0] *= half_size[0]
            offsets[:, 1] *= half_size[0]
            offsets[:, 2] *= half_size[1]
        else:
            offsets *= half_size[:3]
        blocks.append(center + offsets)
    return np.vstack(blocks)


@pytest.mark.mujoco
def test_unitree_go2_blueprint_accepts_obstacle_mvp_sim_overrides() -> None:
    """Go2 navigation blueprint merges obstacle MVP + mujoco global_config overrides."""
    require_lcm_multicast()
    blueprint = get_blueprint_by_name("unitree-go2")
    configured = blueprint.global_config(
        simulation="mujoco",
        viewer="none",
        obstacle_crossing_mvp=True,
        obstacle_crossing_mvp_height_cm=8.0,
    )
    assert configured.global_config_overrides["obstacle_crossing_mvp"] is True
    assert configured.global_config_overrides["obstacle_crossing_mvp_height_cm"] == 8.0
    assert configured.global_config_overrides["simulation"] == "mujoco"


@pytest.mark.mujoco
def test_mujoco_scene_includes_navigation_test_obstacles() -> None:
    """MVP / --mujoco-navigation-test-obstacles injects static geoms into office1."""
    _require_mujoco_sim_deps()

    from dimos.simulation.mujoco.model import load_scene_xml

    with_obstacles = load_scene_xml(_obstacle_mvp_sim_config())
    without_obstacles = load_scene_xml(
        GlobalConfig(_env_file=None, simulation="mujoco", robot_model="unitree_go2")
    )

    for name in (
        "mvp_threshold_low",
        "mvp_threshold_high",
        "mvp_threshold_medium",
    ):
        assert name in with_obstacles
        assert name not in without_obstacles
    assert 'body name="body_BezierCurve_009_SwivlChair"' not in with_obstacles
    assert 'body name="body_Cube_007_woodenDesk"' not in with_obstacles


@pytest.mark.mujoco
def test_mujoco_scene_and_planner_honor_obstacle_mvp_config() -> None:
    """MuJoCo office scene + Go1 model load; GlobalPlanner/LocalPlanner see MVP flags."""
    _require_mujoco_sim_deps()
    import mujoco

    from dimos.simulation.mujoco.model import load_model, load_scene_xml

    config = _obstacle_mvp_sim_config()
    scene_xml = load_scene_xml(config)
    assert "<mujoco" in scene_xml

    model, data = load_model(_ZeroCommandController(), "unitree_go1", scene_xml, config=config)
    assert model.nq > 0
    assert data.qpos is not None
    for geom_name in (
        "mvp_threshold_low",
        "mvp_threshold_high",
        "mvp_threshold_medium",
    ):
        assert mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_GEOM, geom_name) >= 0

    planner = GlobalPlanner(config)
    try:
        assert planner._local_planner._global_config.obstacle_crossing_mvp is True
        assert planner._local_planner._global_config.obstacle_crossing_mvp_height_cm == 8.0
        assert planner._global_config.simulation == "mujoco"
    finally:
        planner.stop()


@pytest.mark.mujoco
def test_mujoco_obstacles_visible_in_path_clearance_costmap() -> None:
    """MVP geom surface points produce occupied cells on a forward path corridor."""
    _require_mujoco_sim_deps()

    import mujoco
    import open3d as o3d  # type: ignore[import-untyped]

    from dimos.msgs.sensor_msgs.PointCloud2 import PointCloud2
    from dimos.simulation.mujoco.model import load_model, load_scene_xml

    config = _obstacle_mvp_sim_config()
    scene_xml = load_scene_xml(config)
    model, data = load_model(_ZeroCommandController(), "unitree_go1", scene_xml, config=config)

    start = config.mujoco_start_pos_float
    data.qpos[0:3] = [start[0], start[1], 0.3]
    mujoco.mj_forward(model, data)

    obstacle_names = (
        "mvp_threshold_low",
        "mvp_threshold_high",
        "mvp_threshold_medium",
    )
    points = _sample_mvp_obstacle_surface_points(model, data, obstacle_names)
    assert len(points) > 100

    pcd = o3d.geometry.PointCloud()
    pcd.points = o3d.utility.Vector3dVector(points)
    cloud = PointCloud2(pointcloud=pcd, frame_id="world")
    costmap = height_cost_occupancy(cloud, resolution=0.05)
    assert np.any(costmap.grid >= 50)

    near_low_sill = points[
        (np.abs(points[:, 0] - (-1.12)) < 0.9) & (np.abs(points[:, 1] - 1.45) < 0.4)
    ]
    assert len(near_low_sill) > 5

    # North-lane corridor through MVP sills. Low sill (5 cm) may sit below height_cost
    # slope sensitivity; medium/high sills must register on the path corridor.
    path = Path(
        poses=[
            PoseStamped(
                position=Vector3(
                    x=-1.12,
                    y=1.0 + 0.2 * i,
                    z=0.0,
                )
            )
            for i in range(21)
        ]
    )

    clearance = PathClearance(config, path)
    clearance.update_costmap(costmap)

    clearance.update_pose_index(0)
    assert not clearance.is_obstacle_ahead()

    # Medium sill at y≈3.15 — pose index 10 is y=3.0 on this path.
    clearance.update_pose_index(10)
    corridor_medium = costmap.grid[clearance.mask]
    assert np.any((corridor_medium >= 0) & (corridor_medium >= 30))
    assert clearance.is_obstacle_ahead()


@pytest.mark.skipif_in_ci
@pytest.mark.mujoco
def test_unitree_go2_mujoco_navigation_obstacle_mvp_smoke(
    lcm_spy: LcmSpy,
    start_blueprint: Callable[..., DimosCliCall],
) -> None:
    """Start unitree-go2 in MuJoCo with obstacle MVP enabled; expect odom from the sim stack."""
    _require_mujoco_sim_deps()
    require_lcm_multicast()

    start_blueprint(
        "--obstacle-crossing-mvp",
        "--obstacle-crossing-mvp-height-cm",
        "8",
        "--viewer",
        "none",
        "run",
        "unitree-go2",
    )

    odom_topic = "/odom#geometry_msgs.PoseStamped"
    lcm_spy.save_topic(odom_topic)
    lcm_spy.wait_for_saved_topic(odom_topic, timeout=180.0)
