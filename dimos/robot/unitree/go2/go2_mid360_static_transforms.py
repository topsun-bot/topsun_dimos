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

"""Static mount frames for the Go2 + Mid-360 + front-camera rig.

Published continuously onto tf while recording (see :class:`Go2Mid360StaticTf`) so the
mount geometry lands in the recording's tf stream and companion streams (camera, go2
lidar) can be anchored to ``base_link``.

Mount geometry (legacy rig profile)
-----------------------------------
- base_link -> front_camera: 32.7cm forward, ~4.3cm up (URDF front_camera mount).
- front_camera -> mid360_link: lidar is 3.2cm back, 12cm up, pitched 44 deg down.
- front_camera -> camera_optical: the standard ROS optical rotation (x-right, y-down,
  z-forward).
"""

from __future__ import annotations

import math

from dimos.msgs.geometry_msgs.Transform import Transform
from dimos.protocol.tf.static_tf_publisher import (
    FrameSpec,
    StaticTfPublisher,
    frames_to_edge_transforms,
)

MID360_PITCH_DOWN = math.radians(44.0)

# rpy that maps a sensor frame to its optical frame (z-forward, x-right, y-down)
OPTICAL_RPY = (-math.pi / 2, 0.0, -math.pi / 2)

FRAMES: list[FrameSpec] = [
    ("base_link", None, (0.0, 0.0, 0.0), (0.0, 0.0, 0.0)),
    ("front_camera", "base_link", (0.32715, -0.00003, 0.04297), (0.0, 0.0, 0.0)),
    ("mid360_link", "front_camera", (-0.032, 0.0, 0.12), (0.0, MID360_PITCH_DOWN, 0.0)),
    ("camera_optical", "front_camera", (0.0, 0.0, 0.0), OPTICAL_RPY),
]


def base_link_from_mid360() -> Transform:
    """Composed base_link -> mid360_link transform from the static mount tree."""
    edges = {t.child_frame_id: t for t in frames_to_edge_transforms(FRAMES)}
    return edges["front_camera"] + edges["mid360_link"]


class Go2Mid360StaticTf(StaticTfPublisher):
    """Publishes the Go2/Mid-360 mount tree onto tf on a fixed interval."""

    def transforms(self) -> list[Transform]:
        return frames_to_edge_transforms(FRAMES)


# Current Orin Navigation rig. These values are intentionally separate from
# FRAMES above: that profile is a different physical rig with a 44-degree mount.
# Runtime source on the vehicle was /home/unitree/work/Navigation. Point-LIO's
# body cloud and odometry are in its IMU state frame, so both edges are kept:
#   T_imu_lidar = [-0.011, -0.02329, 0.04412, I]
#   T_base_imu  = [0.16143, 0, 0.12262, rpy(0, 13deg, 0)]
ORIN_NAVIGATION_MID360_PITCH = math.radians(13.0)
# Increment whenever the active configured mount transform changes. Maps built with a
# different value must not be loaded into the Mid360 navigation blueprint.
ORIN_NAVIGATION_EXTRINSIC_VERSION = "go2_orin_navigation_20260813_v1"
ORIN_NAVIGATION_BASE_TO_IMU = (
    (0.16143, 0.0, 0.12262),
    (0.0, ORIN_NAVIGATION_MID360_PITCH, 0.0),
)
ORIN_NAVIGATION_IMU_TO_MID360 = (
    (-0.011, -0.02329, 0.04412),
    (0.0, 0.0, 0.0),
)
ORIN_NAVIGATION_BASE_TO_MID360 = (
    (0.160636770, -0.023290000, 0.168083669),
    (0.0, ORIN_NAVIGATION_MID360_PITCH, 0.0),
)

ORIN_NAVIGATION_FRAMES: list[FrameSpec] = [
    ("base_link", None, (0.0, 0.0, 0.0), (0.0, 0.0, 0.0)),
    (
        "mid360_imu_link",
        "base_link",
        ORIN_NAVIGATION_BASE_TO_IMU[0],
        ORIN_NAVIGATION_BASE_TO_IMU[1],
    ),
    (
        "mid360_link",
        "mid360_imu_link",
        ORIN_NAVIGATION_IMU_TO_MID360[0],
        ORIN_NAVIGATION_IMU_TO_MID360[1],
    ),
    ("camera_link", "base_link", (0.3, 0.0, 0.0), (0.0, 0.0, 0.0)),
    ("camera_optical", "camera_link", (0.0, 0.0, 0.0), OPTICAL_RPY),
]


def orin_navigation_base_from_mid360() -> Transform:
    """Return the Navigation project's configured base_link -> mid360_link transform."""
    edges = {t.child_frame_id: t for t in frames_to_edge_transforms(ORIN_NAVIGATION_FRAMES)}
    return edges["mid360_imu_link"] + edges["mid360_link"]


def orin_navigation_base_from_pointlio_body() -> Transform:
    """Return base_link -> Point-LIO's IMU/body state frame."""
    edges = {t.child_frame_id: t for t in frames_to_edge_transforms(ORIN_NAVIGATION_FRAMES)}
    return edges["mid360_imu_link"]


class Go2OrinMid360StaticTf(StaticTfPublisher):
    """Publish the current Orin Go2 camera and Mid360 rigid mount tree."""

    def transforms(self) -> list[Transform]:
        return frames_to_edge_transforms(ORIN_NAVIGATION_FRAMES)
