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

"""Offline marker-recognition tests for the official AprilTag family."""

from __future__ import annotations

import cv2
import numpy as np

from dimos.msgs.sensor_msgs.Image import Image, ImageFormat
from dimos.robot.unitree.go2.recharge.config import CalibrationProfile, RechargeConfig
from dimos.robot.unitree.go2.recharge.vision import ArucoRechargeVision


def test_vision_detects_apriltag_36h11_id_zero_with_white_border() -> None:
    dictionary = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_APRILTAG_36h11)
    tag = cv2.aruco.generateImageMarker(dictionary, 0, 200)
    padded = cv2.copyMakeBorder(tag, 40, 40, 40, 40, cv2.BORDER_CONSTANT, value=255)
    config = RechargeConfig(
        calibration=CalibrationProfile(
            camera_matrix=np.array(
                [[797.6243, 1.6166, 140.0], [0.0, 797.8026, 140.0], [0.0, 0.0, 1.0]]
            ),
            dist_coeffs=np.zeros(5),
            distortion_model="plumb_bob",
            image_size=(280, 280),
        )
    )

    observation = ArucoRechargeVision(config).observe(
        Image(data=padded, format=ImageFormat.GRAY, ts=12.0)
    )

    assert observation is not None
    assert observation.corners_px.shape == (4, 2)
    assert observation.z_m > 0.0
    assert observation.marker_id == 0
    assert observation.rvec.shape == (3,)
    assert observation.tvec.shape == (3,)
    assert observation.min_corner_margin_px > 0.0


def test_vision_rejects_wrong_marker_dictionary() -> None:
    dictionary = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_50)
    tag = cv2.aruco.generateImageMarker(dictionary, 0, 200)
    padded = cv2.copyMakeBorder(tag, 40, 40, 40, 40, cv2.BORDER_CONSTANT, value=255)
    config = RechargeConfig(
        calibration=CalibrationProfile(
            camera_matrix=np.array([[797.0, 0.0, 140.0], [0.0, 797.0, 140.0], [0.0, 0.0, 1.0]]),
            dist_coeffs=np.zeros(5),
            distortion_model="plumb_bob",
            image_size=(280, 280),
        )
    )

    observation = ArucoRechargeVision(config).observe(Image(data=padded, format=ImageFormat.GRAY))

    assert observation is None


def test_vision_scales_official_intrinsics_for_same_aspect_remote_video() -> None:
    dictionary = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_APRILTAG_36h11)
    tag = cv2.aruco.generateImageMarker(dictionary, 0, 200)
    full_resolution = np.full((720, 1280), 255, dtype=np.uint8)
    full_resolution[260:460, 540:740] = tag
    remote_resolution = cv2.resize(full_resolution, (640, 360), interpolation=cv2.INTER_NEAREST)
    config = RechargeConfig(
        calibration=CalibrationProfile(
            camera_matrix=np.array([[800.0, 0.0, 640.0], [0.0, 800.0, 360.0], [0.0, 0.0, 1.0]]),
            dist_coeffs=np.zeros(5),
            distortion_model="plumb_bob",
            image_size=(1280, 720),
        )
    )

    observation = ArucoRechargeVision(config).observe(
        Image(data=remote_resolution, format=ImageFormat.GRAY)
    )

    assert observation is not None
    assert observation.z_m > 0.0
