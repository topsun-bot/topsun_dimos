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

from dimos.core.coordination.blueprints import autoconnect
from dimos.core.global_config import global_config
from dimos.experimental.security_demo.security_module import SecurityModule
from dimos.mapping.relocalization.module import RelocalizationModule
from dimos.navigation.bbox_navigation import BBoxNavigationModule
from dimos.perception.detection.door.door_spatial_memory_module import SpatialLandmarkMemoryModule
from dimos.perception.object_tracker_2d import ObjectTracker2D
from dimos.perception.perceive_loop_skill import PerceiveLoopSkill
from dimos.perception.spatial_perception import SpatialMemory
from dimos.robot.unitree.go2.blueprints.smart.unitree_go2 import unitree_go2
from dimos.robot.unitree.go2.connection import GO2Connection

unitree_go2_spatial = (
    autoconnect(
        unitree_go2,
        SpatialMemory.blueprint(new_memory=global_config.new_memory),
        SpatialLandmarkMemoryModule.blueprint(),
        ObjectTracker2D.blueprint(frame_id="camera_link"),
        BBoxNavigationModule.blueprint(),
        PerceiveLoopSkill.blueprint(),
        SecurityModule.blueprint(camera_info=GO2Connection.camera_info_static),
    )
    .remappings(
        [
            (BBoxNavigationModule, "detection2d", "detection2darray"),
        ]
    )
    .global_config(n_workers=8)
)

unitree_go2_relocalization_memory = (
    autoconnect(
        unitree_go2,
        RelocalizationModule.blueprint(),
        SpatialMemory.blueprint(new_memory=global_config.new_memory),
        SpatialLandmarkMemoryModule.blueprint(),
        ObjectTracker2D.blueprint(frame_id="camera_link"),
        BBoxNavigationModule.blueprint(),
        PerceiveLoopSkill.blueprint(),
        SecurityModule.blueprint(camera_info=GO2Connection.camera_info_static),
    )
    .remappings(
        [
            (BBoxNavigationModule, "detection2d", "detection2darray"),
        ]
    )
    .global_config(n_workers=11, robot_model="unitree_go2")
)
