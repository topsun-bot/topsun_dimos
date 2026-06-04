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

from typing import Any

from dimos.mapping.costmapper import CostMapper
from dimos.mapping.voxels import VoxelGridMapper
from dimos.agents.mcp.mcp_client import McpClient
from dimos.agents.mcp.mcp_server import McpServer
from dimos.agents.skills.navigation_open_goal import OpenGoalNavigationSkillContainer
from dimos.agents.skills.speak_skill import SpeakSkill
from dimos.agents.web_human_input import WebInput
from dimos.core.coordination.blueprints import autoconnect
from dimos.core.global_config import global_config
from dimos.navigation.replanning_a_star.module import ReplanningAStarPlanner
from dimos.navigation.movement_manager.movement_manager import MovementManager
from dimos.navigation.frontier_exploration.wavefront_frontier_goal_selector import (
    WavefrontFrontierExplorer,
)
from dimos.experimental.security_demo.security_module import SecurityModule
from dimos.robot.unitree.go2.connection import GO2Connection
from dimos.robot.unitree.go2.blueprints.basic.unitree_go2_basic import unitree_go2_basic
from dimos.robot.unitree.unitree_skill_container import UnitreeSkillContainer
from dimos.visualization.vis_module import vis_module

def _convert_camera_info(camera_info: Any) -> Any:
    """Add Pinhole for wildos_image so it renders in Rerun 3D view."""
    import rerun as rr

    result = camera_info.to_rerun(
        image_topic="/world/color_image",
        optical_frame="camera_optical",
    )
    fx, fy = camera_info.K[0], camera_info.K[4]
    cx, cy = camera_info.K[2], camera_info.K[5]
    result.append((
        "world/wildos_image",
        rr.Pinhole(
            focal_length=[fx, fy],
            principal_point=[cx, cy],
            width=camera_info.width,
            height=camera_info.height,
        ),
    ))
    return result


_rerun_config: dict[str, Any] = {
    "visual_override": {
        "world/camera_info": _convert_camera_info,
    },
}


unitree_go2_agentic_open_goal_search = autoconnect(
    unitree_go2_basic,
    VoxelGridMapper.blueprint(),
    CostMapper.blueprint(),
    ReplanningAStarPlanner.blueprint(),
    MovementManager.blueprint(),
    SecurityModule.blueprint(camera_info=GO2Connection.camera_info_static),
    McpServer.blueprint(),
    McpClient.blueprint(
        model="deepseek-v4-pro",
        model_provider="openai",
        model_kwargs={"extra_body": {"thinking": {"type": "disabled"}}},
    ),
    OpenGoalNavigationSkillContainer.blueprint(
        debug_save_dir="/home/lenovo/Projects/topsun_dimos/assets/output/wildos",
    ),
    UnitreeSkillContainer.blueprint(),
    WebInput.blueprint(),
    SpeakSkill.blueprint(),
    vis_module(viewer_backend=global_config.viewer, rerun_config=_rerun_config),
).global_config(n_workers=8).disabled_modules(WavefrontFrontierExplorer)

__all__ = ["unitree_go2_agentic_open_goal_search"]
