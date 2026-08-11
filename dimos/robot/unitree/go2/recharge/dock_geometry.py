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

"""Planar marker-to-dock geometry for navigation staging and final docking."""

from __future__ import annotations

import math

import cv2

from dimos.robot.unitree.go2.recharge.config import RechargeConfig
from dimos.robot.unitree.go2.recharge.types import (
    DockObservation,
    DockTarget,
    PlanarPose,
)


def _rotate(x: float, y: float, yaw: float) -> tuple[float, float]:
    cosine = math.cos(yaw)
    sine = math.sin(yaw)
    return cosine * x - sine * y, sine * x + cosine * y


def build_dock_target(
    observation: DockObservation,
    robot_pose: PlanarPose,
    config: RechargeConfig,
) -> DockTarget | None:
    """Freeze marker, centreline staging and final robot poses in odometry frame.

    Camera optical ``z`` maps to robot forward and optical ``x`` maps to robot
    right, hence base-left is ``-x_C``.  The tag normal sign is selected so it
    points from the marker toward the observing robot/charging-pad open side.
    """
    if observation.z_m <= 0.0 or observation.rvec.shape != (3,):
        return None

    marker_forward_b = config.camera_to_robot_center_m + observation.z_m
    marker_left_b = -observation.x_m
    marker_dx, marker_dy = _rotate(marker_forward_b, marker_left_b, robot_pose.yaw)
    marker_x = robot_pose.x + marker_dx
    marker_y = robot_pose.y + marker_dy

    rotation_c_m, _ = cv2.Rodrigues(observation.rvec.reshape(3, 1))
    normal_c = rotation_c_m[:, 2]
    normal_forward_b = float(normal_c[2])
    normal_left_b = -float(normal_c[0])
    normal_x, normal_y = _rotate(normal_forward_b, normal_left_b, robot_pose.yaw)
    norm = math.hypot(normal_x, normal_y)
    if norm < 1e-6:
        return None
    normal_x /= norm
    normal_y /= norm

    marker_to_robot_x = robot_pose.x - marker_x
    marker_to_robot_y = robot_pose.y - marker_y
    if normal_x * marker_to_robot_x + normal_y * marker_to_robot_y < 0.0:
        normal_x = -normal_x
        normal_y = -normal_y

    approach_yaw = math.atan2(-normal_y, -normal_x)
    marker_pose = PlanarPose(
        marker_x,
        marker_y,
        approach_yaw,
        robot_pose.frame_id,
        observation.observed_at,
    )
    staging_pose = PlanarPose(
        marker_x + normal_x * config.staging_base_distance_from_marker_m,
        marker_y + normal_y * config.staging_base_distance_from_marker_m,
        approach_yaw,
        robot_pose.frame_id,
        observation.observed_at,
    )
    final_pose = PlanarPose(
        marker_x + normal_x * config.final_base_distance_from_marker_m,
        marker_y + normal_y * config.final_base_distance_from_marker_m,
        approach_yaw,
        robot_pose.frame_id,
        observation.observed_at,
    )
    return DockTarget(
        marker_pose=marker_pose,
        staging_pose=staging_pose,
        final_pose=final_pose,
        marker_normal_x=normal_x,
        marker_normal_y=normal_y,
        target_camera_z_m=config.target_camera_z_m,
        frozen_at=observation.observed_at,
    )


def centerline_error_m(robot_pose: PlanarPose, target: DockTarget) -> float:
    """Absolute perpendicular distance from the robot centre to dock centreline."""
    marker_to_robot_x = robot_pose.x - target.marker_pose.x
    marker_to_robot_y = robot_pose.y - target.marker_pose.y
    return abs(
        marker_to_robot_x * target.marker_normal_y - marker_to_robot_y * target.marker_normal_x
    )


def distance_between(a: PlanarPose, b: PlanarPose) -> float:
    """Euclidean planar distance between two poses."""
    return math.hypot(a.x - b.x, a.y - b.y)


def direct_servo_reachable(
    observation: DockObservation,
    robot_pose: PlanarPose,
    target: DockTarget,
    config: RechargeConfig,
) -> bool:
    """Conservative gate for skipping navigation staging."""
    return (
        abs(observation.bearing_rad) <= config.direct_servo_max_bearing_rad
        and abs(observation.x_m) <= config.direct_servo_max_abs_x_m
        and observation.z_m <= config.direct_servo_max_z_m
        and centerline_error_m(robot_pose, target) <= config.direct_servo_centerline_tolerance_m
    )
