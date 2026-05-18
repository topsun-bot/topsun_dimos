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

"""End-to-end pipeline test: synthetic map -> detect -> plan -> locomotion twist."""

from __future__ import annotations

import numpy as np
import pytest

from dimos.core.global_config import GlobalConfig
from dimos.mapping.terrain.fixtures.synthetic import straight_stair_5_steps_17cm
from dimos.mapping.terrain.stair_detection import (
    candidates_to_corridors,
    detect_stairs_from_height_map,
)
from dimos.msgs.geometry_msgs.PoseStamped import PoseStamped
from dimos.msgs.geometry_msgs.Vector3 import Vector3
from dimos.msgs.sensor_msgs.PointCloud2 import PointCloud2
from dimos.navigation.replanning_a_star.global_planner import GlobalPlanner
from dimos.navigation.stairs.contracts import StairDetectionConfig
from dimos.navigation.stairs.plan_in_corridor import plan_in_corridor
from dimos.robot.unitree.go2.stair_locomotion.locomotion_policy import StairLocomotionPolicy
from dimos.robot.unitree.go2.stair_locomotion.test_stair_locomotion_policy import MockGo2Connection


def _pointcloud_from_height_map(synth) -> PointCloud2:
    hm = synth.height_map
    gy, gx = hm.shape
    points = []
    for iy in range(gy):
        for ix in range(gx):
            z = hm[iy, ix]
            if np.isnan(z):
                continue
            x = synth.origin_x + ix * synth.resolution
            y = synth.origin_y + iy * synth.resolution
            points.append([x, y, z])
    arr = np.array(points, dtype=np.float64)
    return PointCloud2.from_numpy(arr, frame_id="map")


@pytest.mark.slow
def test_detect_plan_locomotion_pipeline() -> None:
    synth = straight_stair_5_steps_17cm()
    cfg = StairDetectionConfig(resolution=synth.resolution)
    candidates = detect_stairs_from_height_map(
        synth.height_map,
        synth.origin_x,
        synth.origin_y,
        cfg,
    )
    assert len(candidates) == 1
    corridor = candidates_to_corridors(candidates)[0]
    cl = corridor.centerline
    path = plan_in_corridor(
        Vector3(cl[0][0], cl[0][1], 0.0),
        Vector3(cl[-1][0], cl[-1][1], 0.0),
        corridor,
    )
    assert path is not None

    conn = MockGo2Connection()
    policy = StairLocomotionPolicy(conn)
    policy.start(corridor, path)
    odom = PoseStamped(position=Vector3(cl[0][0], cl[0][1], 0.0))
    twist = policy.step(odom)
    assert twist is not None


def test_global_planner_stair_corridor_path() -> None:
    synth = straight_stair_5_steps_17cm()
    cfg = StairDetectionConfig(resolution=synth.resolution)
    candidates = detect_stairs_from_height_map(
        synth.height_map,
        synth.origin_x,
        synth.origin_y,
        cfg,
    )
    corridor = candidates_to_corridors(candidates)[0]
    cl = corridor.centerline

    gcfg = GlobalConfig(stair_navigation=True)
    planner = GlobalPlanner(gcfg)
    planner.set_stair_corridor(corridor)

    path = plan_in_corridor(
        Vector3(cl[0][0], cl[0][1], 0.0),
        Vector3(cl[-1][0], cl[-1][1], 0.0),
        corridor,
    )
    assert path is not None
    assert len(path.poses) >= 3


def test_pointcloud_detection_roundtrip() -> None:
    synth = straight_stair_5_steps_17cm()
    cloud = _pointcloud_from_height_map(synth)
    from dimos.mapping.terrain.stair_detection import detect_stairs_from_pointcloud

    candidates = detect_stairs_from_pointcloud(cloud, StairDetectionConfig(resolution=synth.resolution))
    assert len(candidates) >= 1
