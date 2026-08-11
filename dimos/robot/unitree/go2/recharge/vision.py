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

"""AprilTag 36h11 检测 + PnP, 供 Go2 4G WebRTC 视觉回充.

输出坐标系: 相机光学系, x 右 / y 下 / z 前 (OpenCV solvePnP 惯例).
yaw_rad 不是码平面旋转角, 而是码中心方位角 atan2(x_m, z_m):
  码在画面右侧 → yaw_rad>0; 左侧 → yaw_rad<0.
控制器直接用该角作对准误差, 转向符号见 RechargeConfig.yaw_sign.
"""

from __future__ import annotations

import math

import cv2
import numpy as np

from dimos.msgs.sensor_msgs.Image import Image
from dimos.perception.fiducial.marker_pose import (
    create_aruco_detector,
    estimate_marker_pose,
    marker_reprojection_error,
)
from dimos.robot.unitree.go2.recharge.config import RechargeConfig
from dimos.robot.unitree.go2.recharge.types import DockObservation


class ArucoRechargeVision:
    """Convert a Go2 front-camera image into one quality-gated marker pose."""

    def __init__(self, config: RechargeConfig) -> None:
        self._config = config
        self._detector = create_aruco_detector(config.marker_dictionary)

    def observe(self, image: Image) -> DockObservation | None:
        """Return ID 0 marker pose, or ``None`` when the frame is not usable."""
        calibration = self._config.calibration
        # WebRTC may deliver 640x360 even though the official calibration is 1280x720.
        # Reject non-proportional streams because their principal point is unknown.
        camera_matrix = self._camera_matrix_for_image(image.width, image.height)
        if camera_matrix is None:
            return None
        bgr = image.to_opencv()
        gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY) if bgr.ndim == 3 else bgr
        corners, ids, _ = self._detector.detectMarkers(gray)
        if ids is None:
            return None
        # The official recharge marker is AprilTag 36h11 id=0. Multiple id=0
        # detections in one frame are ambiguous, so only exactly one match is usable.
        matches = [
            np.asarray(corner, dtype=np.float64).reshape(4, 2)
            for corner, marker_id in zip(corners, ids.flatten(), strict=True)
            if int(marker_id) == self._config.marker_id
        ]
        if len(matches) != 1:
            return None
        marker_corners = matches[0]
        # Very small tags produce unstable PnP and can cause wrong forward decisions.
        side_lengths = np.linalg.norm(marker_corners - np.roll(marker_corners, -1, axis=0), axis=1)
        marker_side_px = float(np.min(side_lengths))
        if marker_side_px < self._config.min_marker_side_px:
            return None
        pose = estimate_marker_pose(
            marker_corners,
            self._config.marker_length_m,
            camera_matrix,
            calibration.dist_coeffs,
            distortion_model=calibration.distortion_model,
        )
        if pose is None:
            return None
        rvec, tvec = pose
        # Reprojection error catches wrong dictionaries, partial tags, and glare cases
        # even when the detector returns four corners.
        reprojection_error = marker_reprojection_error(
            marker_corners,
            self._config.marker_length_m,
            camera_matrix,
            calibration.dist_coeffs,
            rvec,
            tvec,
            distortion_model=calibration.distortion_model,
        )
        if reprojection_error > self._config.max_reprojection_error_px:
            return None
        x_m, y_m, z_m = (float(value) for value in tvec.reshape(3))
        if z_m <= 0.0:
            return None
        # 第一版只用中心点 bearing, 不用 rvec 的码面旋转 (近场 rvec 抖动大).
        bearing_rad = math.atan2(x_m, z_m)
        min_corner_margin_px = float(
            min(
                np.min(marker_corners[:, 0]),
                np.min(marker_corners[:, 1]),
                image.width - 1 - np.max(marker_corners[:, 0]),
                image.height - 1 - np.max(marker_corners[:, 1]),
            )
        )
        return DockObservation(
            corners_px=marker_corners,
            x_m=x_m,
            y_m=y_m,
            z_m=z_m,
            yaw_rad=bearing_rad,
            reprojection_error_px=reprojection_error,
            observed_at=float(image.ts),
            marker_id=self._config.marker_id,
            image_width=image.width,
            image_height=image.height,
            rvec=np.asarray(rvec, dtype=np.float64).reshape(3),
            tvec=np.asarray(tvec, dtype=np.float64).reshape(3),
            min_corner_margin_px=min_corner_margin_px,
            marker_side_px=marker_side_px,
        )

    def _camera_matrix_for_image(self, width: int, height: int) -> np.ndarray | None:
        """Scale the official pixel-space intrinsic matrix for a same-aspect video stream.

        Unitree's supplied calibration is for 1280x720. Remote WebRTC currently
        delivers the same physical camera at 640x360, so the focal lengths,
        skew and principal point scale with the image while distortion remains
        unchanged. Cropped or aspect-ratio-changing streams are rejected because
        they require a separately calibrated principal point.
        """
        source_width, source_height = self._config.calibration.image_size
        if width <= 0 or height <= 0 or width * source_height != height * source_width:
            return None
        scale_x = width / source_width
        scale_y = height / source_height
        matrix = self._config.calibration.camera_matrix.copy()
        matrix[0, :] *= scale_x
        matrix[1, :] *= scale_y
        return matrix
