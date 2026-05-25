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


from pathlib import Path
import xml.etree.ElementTree as ET

from etils import epath
import mujoco
from mujoco_playground._src import mjx_env
import numpy as np

from dimos.core.global_config import GlobalConfig
from dimos.mapping.occupancy.extrude_occupancy import generate_mujoco_scene
from dimos.msgs.nav_msgs.OccupancyGrid import OccupancyGrid
from dimos.simulation.mujoco.input_controller import InputController
from dimos.simulation.mujoco.policy import G1OnnxController, Go1OnnxController, OnnxController
from dimos.utils.data import get_data


def _get_data_dir() -> epath.Path:
    return epath.Path(str(get_data("mujoco_sim")))


def get_assets() -> dict[str, bytes]:
    data_dir = _get_data_dir()
    assets: dict[str, bytes] = {}

    # Assets used from https://sketchfab.com/3d-models/mersus-office-8714be387bcd406898b2615f7dae3a47
    # Created by Ryan Cassidy and Coleman Costello
    mjx_env.update_assets(assets, data_dir, "*.xml")
    mjx_env.update_assets(assets, data_dir / "scene_office1/textures", "*.png")
    mjx_env.update_assets(assets, data_dir / "scene_office1/office_split", "*.obj")
    mjx_env.update_assets(assets, mjx_env.MENAGERIE_PATH / "unitree_go1" / "assets")
    mjx_env.update_assets(assets, mjx_env.MENAGERIE_PATH / "unitree_g1" / "assets")

    # From: https://sketchfab.com/3d-models/jeong-seun-34-42956ca979404a038b8e0d3e496160fd
    person_dir = epath.Path(str(get_data("person")))
    mjx_env.update_assets(assets, person_dir, "*.obj")
    mjx_env.update_assets(assets, person_dir, "*.png")

    return assets


def load_model(
    input_device: InputController,
    robot: str,
    scene_xml: str,
    config: GlobalConfig | None = None,
) -> tuple[mujoco.MjModel, mujoco.MjData]:
    mujoco.set_mjcb_control(None)

    xml_string = get_model_xml(robot, scene_xml, config=config)
    model = mujoco.MjModel.from_xml_string(xml_string, assets=get_assets())
    data = mujoco.MjData(model)

    mujoco.mj_resetDataKeyframe(model, data, 0)

    match robot:
        case "unitree_g1":
            sim_dt = 0.002
        case _:
            sim_dt = 0.005

    ctrl_dt = 0.02
    n_substeps = round(ctrl_dt / sim_dt)
    model.opt.timestep = sim_dt

    params = {
        "policy_path": (_get_data_dir() / f"{robot}_policy.onnx").as_posix(),
        "default_angles": np.array(model.keyframe("home").qpos[7:]),
        "n_substeps": n_substeps,
        "action_scale": 0.5,
        "input_controller": input_device,
        "ctrl_dt": ctrl_dt,
    }

    match robot:
        case "unitree_go1":
            policy: OnnxController = Go1OnnxController(**params)
        case "unitree_g1":
            policy = G1OnnxController(**params, drift_compensation=[-0.18, 0.0, -0.09])
        case _:
            raise ValueError(f"Unknown robot policy: {robot}")

    mujoco.set_mjcb_control(policy.get_control)

    return model, data


def get_model_xml(robot: str, scene_xml: str, config: GlobalConfig | None = None) -> str:
    root = ET.fromstring(scene_xml)
    root.set("model", f"{robot}_scene")
    root.insert(0, ET.Element("include", file=f"{robot}.xml"))

    # Ensure visual/map element exists with znear and zfar
    visual = root.find("visual")
    if visual is None:
        visual = ET.SubElement(root, "visual")
    map_elem = visual.find("map")
    if map_elem is None:
        map_elem = ET.SubElement(visual, "map")
    map_elem.set("znear", "0.01")
    map_elem.set("zfar", "10000")

    if config is not None and _should_inject_navigation_test_obstacles(config):
        _strip_office_furniture_for_navigation_mvp(root)
        _add_navigation_test_obstacles(root)
    elif config is not None:
        _add_person_object(root)

    return ET.tostring(root, encoding="unicode")


def _should_inject_navigation_test_obstacles(config: GlobalConfig) -> bool:
    return config.mujoco_navigation_test_obstacles or config.obstacle_crossing_mvp


_FURNITURE_BODY_MARKERS: tuple[str, ...] = (
    "Chair",
    "Desk",
    "Table",
    "Desktop",
    "meetingTable",
    "SwivlChair",
    "woodenDesk",
    "BigWhite",
    "SmallWhite",
    "Shelving",
)


def _strip_office_furniture_for_navigation_mvp(root: ET.Element) -> None:
    """Remove chairs, desks, tables, and shelving from office1 for isolated MVP testing."""
    worldbody = root.find("worldbody")
    if worldbody is None:
        return

    to_remove: list[ET.Element] = []
    for body in worldbody.findall("body"):
        name = body.get("name", "")
        if any(marker in name for marker in _FURNITURE_BODY_MARKERS):
            to_remove.append(body)

    for body in to_remove:
        worldbody.remove(body)


def _add_navigation_test_obstacles(root: ET.Element) -> None:
    """Add thin door-sill thresholds along the north corridor for obstacle MVP sim tests."""
    asset = root.find("asset")
    if asset is None:
        asset = ET.SubElement(root, "asset")

    if asset.find("material[@name='mat_mvp_obstacle_low']") is None:
        ET.SubElement(asset, "material", name="mat_mvp_obstacle_low", rgba="0.92 0.55 0.12 1")
        ET.SubElement(asset, "material", name="mat_mvp_obstacle_high", rgba="0.85 0.15 0.12 1")
        ET.SubElement(asset, "material", name="mat_mvp_obstacle_medium", rgba="0.75 0.35 0.15 1")

    worldbody = root.find("worldbody")
    if worldbody is None:
        worldbody = ET.SubElement(root, "worldbody")

    if worldbody.find("body[@name='mvp_navigation_obstacles']") is not None:
        return

    # Robot default spawn is (-1, 1). Thin thresholds span the corridor (+Y north), away from
    # the origin furniture cluster (stripped when this inject runs).
    obstacle_body = ET.SubElement(worldbody, "body", name="mvp_navigation_obstacles")
    # Sill centers along +Y north corridor; adjacent spacing ≥ 1.5 m (here 1.7 m).
    # Half-size: sx = half span along corridor width (~1.5 m total), sy = half thickness
    # (~0.05 m along travel), sz = half height. X center aligned with typical northbound path.
    obstacles: list[tuple[str, str, str, str, str]] = [
        # name, pos (center), half-size (box: sx sy sz), type, material
        (
            "mvp_threshold_low",
            "-1.12 1.45 0.025",
            "0.75 0.025 0.025",
            "box",
            "mat_mvp_obstacle_low",
        ),
        (
            "mvp_threshold_medium",
            "-1.12 3.15 0.075",
            "0.75 0.025 0.075",
            "box",
            "mat_mvp_obstacle_medium",
        ),
        (
            "mvp_threshold_high",
            "-1.12 4.85 0.29",
            "0.75 0.025 0.29",
            "box",
            "mat_mvp_obstacle_high",
        ),
    ]
    for name, pos, size, geom_type, material in obstacles:
        ET.SubElement(
            obstacle_body,
            "geom",
            name=name,
            type=geom_type,
            pos=pos,
            size=size,
            material=material,
            contype="1",
            conaffinity="1",
        )


def inject_navigation_test_obstacles(scene_xml: str) -> str:
    root = ET.fromstring(scene_xml)
    _strip_office_furniture_for_navigation_mvp(root)
    _add_navigation_test_obstacles(root)
    return ET.tostring(root, encoding="unicode")


def _add_person_object(root: ET.Element) -> None:
    asset = root.find("asset")

    if asset is None:
        asset = ET.SubElement(root, "asset")

    ET.SubElement(asset, "mesh", name="person_mesh", file="jeong_seun_34.obj")
    ET.SubElement(asset, "texture", name="person_texture", file="material_0.png", type="2d")
    ET.SubElement(asset, "material", name="person_material", texture="person_texture")

    worldbody = root.find("worldbody")

    if worldbody is None:
        worldbody = ET.SubElement(root, "worldbody")

    person_body = ET.SubElement(worldbody, "body", name="person", pos="0 0 0", mocap="true")

    ET.SubElement(
        person_body,
        "geom",
        type="mesh",
        mesh="person_mesh",
        material="person_material",
        euler="1.5708 0 0",
    )


def load_scene_xml(config: GlobalConfig) -> str:
    if config.mujoco_room_from_occupancy:
        path = Path(config.mujoco_room_from_occupancy)
        return generate_mujoco_scene(OccupancyGrid.from_path(path))

    mujoco_room = config.mujoco_room or "office1"
    xml_file = (_get_data_dir() / f"scene_{mujoco_room}.xml").as_posix()
    with open(xml_file) as f:
        scene_xml = f.read()

    if _should_inject_navigation_test_obstacles(config):
        scene_xml = inject_navigation_test_obstacles(scene_xml)

    return scene_xml
