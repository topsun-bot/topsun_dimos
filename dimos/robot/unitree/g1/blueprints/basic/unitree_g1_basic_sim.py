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

"""Basic G1 sim stack: base sensors plus sim connection and planner."""

from typing import Any

from dimos.core.coordination.blueprints import autoconnect
from dimos.core.global_config import global_config
from dimos.core.transport import LCMTransport
from dimos.mapping.costmapper import CostMapper
from dimos.mapping.voxels import VoxelGridMapper
from dimos.msgs.geometry_msgs.PoseStamped import PoseStamped
from dimos.msgs.geometry_msgs.Twist import Twist
from dimos.msgs.nav_msgs.Odometry import Odometry
from dimos.msgs.nav_msgs.Path import Path
from dimos.msgs.sensor_msgs.CameraInfo import CameraInfo
from dimos.msgs.sensor_msgs.Image import Image
from dimos.msgs.sensor_msgs.PointCloud2 import PointCloud2
from dimos.msgs.std_msgs.Bool import Bool
from dimos.navigation.frontier_exploration.wavefront_frontier_goal_selector import (
    WavefrontFrontierExplorer,
)
from dimos.navigation.replanning_a_star.module import ReplanningAStarPlanner
from dimos.protocol.pubsub.impl.lcmpubsub import LCM
from dimos.robot.unitree.g1.mujoco_sim import G1SimConnection
from dimos.web.websocket_vis.websocket_vis_module import WebsocketVisModule


def _convert_camera_info(camera_info: Any) -> Any:
    return camera_info.to_rerun(
        image_topic="/world/color_image",
        optical_frame="camera_optical",
    )


def _convert_navigation_costmap(grid: Any) -> Any:
    return grid.to_rerun(
        colormap="Accent",
        z_offset=0.015,
        opacity=0.2,
        background="#484981",
    )


def _static_base_link(rr: Any) -> list[Any]:
    return [
        rr.Boxes3D(
            half_sizes=[0.2, 0.15, 0.75],
            colors=[(0, 255, 127)],
            fill_mode="MajorWireframe",
        ),
        rr.Transform3D(parent_frame="tf#/base_link"),
    ]


def _g1_rerun_blueprint() -> Any:
    """Split layout: camera feed + 3D world view side by side."""
    import rerun as rr
    import rerun.blueprint as rrb

    return rrb.Blueprint(
        rrb.Horizontal(
            rrb.Spatial2DView(origin="world/color_image", name="Camera"),
            rrb.Spatial3DView(
                origin="world",
                name="3D",
                background=rrb.Background(kind="SolidColor", color=[0, 0, 0]),
                line_grid=rrb.LineGrid3D(
                    plane=rr.components.Plane3D.XY.with_distance(0.5),
                ),
            ),
            column_shares=[1, 2],
        ),
    )


if global_config.viewer == "foxglove":
    from dimos.robot.foxglove_bridge import FoxgloveBridge

    _with_vis = autoconnect(FoxgloveBridge.blueprint())
elif global_config.viewer.startswith("rerun"):
    from dimos.visualization.rerun.bridge import RerunBridgeModule

    rerun_config = {
        "blueprint": _g1_rerun_blueprint,
        "pubsubs": [LCM()],
        "visual_override": {
            "world/camera_info": _convert_camera_info,
            "world/navigation_costmap": _convert_navigation_costmap,
        },
        "static": {
            "world/tf/base_link": _static_base_link,
        },
    }

    _with_vis = autoconnect(RerunBridgeModule.blueprint(**rerun_config))
else:
    _with_vis = autoconnect()

unitree_g1_basic_sim = (
    autoconnect(
        _with_vis,
        G1SimConnection.blueprint(),
        VoxelGridMapper.blueprint(),
        CostMapper.blueprint(),
        WavefrontFrontierExplorer.blueprint(),
        ReplanningAStarPlanner.blueprint(),
        WebsocketVisModule.blueprint(),
    )
    .global_config(n_workers=4, robot_model="unitree_g1")
    .transports(
        {
            ("cmd_vel", Twist): LCMTransport("/cmd_vel", Twist),
            ("state_estimation", Odometry): LCMTransport("/state_estimation", Odometry),
            ("odom", PoseStamped): LCMTransport("/odom", PoseStamped),
            ("goal_req", PoseStamped): LCMTransport("/goal_req", PoseStamped),
            ("goal_active", PoseStamped): LCMTransport("/goal_active", PoseStamped),
            ("path_active", Path): LCMTransport("/path_active", Path),
            ("pointcloud", PointCloud2): LCMTransport("/lidar", PointCloud2),
            ("global_pointcloud", PointCloud2): LCMTransport("/map", PointCloud2),
            ("goal_pose", PoseStamped): LCMTransport("/goal_pose", PoseStamped),
            ("goal_reached", Bool): LCMTransport("/goal_reached", Bool),
            ("cancel_goal", Bool): LCMTransport("/cancel_goal", Bool),
            ("color_image", Image): LCMTransport("/color_image", Image),
            ("camera_info", CameraInfo): LCMTransport("/camera_info", CameraInfo),
        }
    )
)
