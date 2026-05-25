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

"""Perception and memory modules used by higher-level G1 blueprints."""

from dimos.core.coordination.blueprints import autoconnect
from dimos.core.global_config import global_config
from dimos.navigation.bbox_navigation import BBoxNavigationModule
from dimos.perception.detection.door.door_spatial_memory_module import SpatialLandmarkMemoryModule
from dimos.perception.object_tracker_2d import ObjectTracker2D
from dimos.perception.spatial_perception import SpatialMemory

_perception_and_memory = autoconnect(
    SpatialMemory.blueprint(new_memory=global_config.new_memory),
    SpatialLandmarkMemoryModule.blueprint(),
    ObjectTracker2D.blueprint(frame_id="camera_link"),
    BBoxNavigationModule.blueprint(),
).remappings(
    [
        (BBoxNavigationModule, "detection2d", "detection2darray"),
    ]
)

__all__ = ["_perception_and_memory"]
