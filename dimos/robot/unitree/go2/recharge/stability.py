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

"""Robust short-window filtering for Go2 recharge-tag observations."""

from __future__ import annotations

from collections import deque
import math

import cv2
import numpy as np

from dimos.robot.unitree.go2.recharge.config import RechargeConfig
from dimos.robot.unitree.go2.recharge.types import DockObservation, StableDockObservation


def _mad(values: np.ndarray) -> float:
    median = float(np.median(values))
    return float(np.median(np.abs(values - median)))


def _wrap(angle: float) -> float:
    return math.atan2(math.sin(angle), math.cos(angle))


def marker_normal_yaw_in_camera(observation: DockObservation) -> float:
    """Return the tag +Z normal bearing in the camera X/Z plane."""
    rotation, _ = cv2.Rodrigues(observation.rvec.reshape(3, 1))
    normal = rotation[:, 2]
    return math.atan2(float(normal[0]), float(normal[2]))


def _circular_median(values: np.ndarray) -> float:
    candidates = [float(value) for value in values]
    return min(candidates, key=lambda value: sum(abs(_wrap(value - other)) for other in candidates))


def _circular_mad(values: np.ndarray, median: float) -> float:
    return float(np.median([abs(_wrap(float(value) - median)) for value in values]))


class PoseStabilityWindow:
    """Require a configurable number of consistent valid frames before motion."""

    def __init__(self, config: RechargeConfig) -> None:
        self._config = config
        self._items: deque[DockObservation | None] = deque(maxlen=config.stability_window_frames)

    def clear(self) -> None:
        """Discard observations from a previous motion or task."""
        self._items.clear()

    def push(self, observation: DockObservation | None) -> StableDockObservation | None:
        """Append one camera frame and return a stable median pose when available."""
        self._items.append(observation)
        return self.stable()

    def stable(self) -> StableDockObservation | None:
        """Return the robust window summary only when every quality gate passes."""
        valid = [item for item in self._items if item is not None]
        if len(valid) < self._config.stability_min_valid_frames:
            return None
        if len({item.marker_id for item in valid}) != 1:
            return None

        x_values = np.array([item.x_m for item in valid], dtype=np.float64)
        y_values = np.array([item.y_m for item in valid], dtype=np.float64)
        z_values = np.array([item.z_m for item in valid], dtype=np.float64)
        bearing_values = np.array([item.bearing_rad for item in valid], dtype=np.float64)
        normal_values = np.array(
            [marker_normal_yaw_in_camera(item) for item in valid], dtype=np.float64
        )

        x_mad = _mad(x_values)
        z_mad = _mad(z_values)
        bearing_median = _circular_median(bearing_values)
        bearing_mad = _circular_mad(bearing_values, bearing_median)
        normal_median = _circular_median(normal_values)
        normal_mad = _circular_mad(normal_values, normal_median)
        if (
            x_mad > self._config.stable_x_mad_m
            or z_mad > self._config.stable_z_mad_m
            or bearing_mad > self._config.stable_bearing_mad_rad
            or normal_mad > self._config.stable_normal_yaw_mad_rad
        ):
            return None

        latest = valid[-1]
        observation = DockObservation(
            corners_px=np.median(
                np.stack([item.corners_px for item in valid], axis=0), axis=0
            ).astype(np.float64),
            x_m=float(np.median(x_values)),
            y_m=float(np.median(y_values)),
            z_m=float(np.median(z_values)),
            yaw_rad=bearing_median,
            reprojection_error_px=float(np.max([item.reprojection_error_px for item in valid])),
            observed_at=latest.observed_at,
            marker_id=latest.marker_id,
            image_width=latest.image_width,
            image_height=latest.image_height,
            rvec=np.median(np.stack([item.rvec for item in valid], axis=0), axis=0),
            tvec=np.array(
                [
                    float(np.median(x_values)),
                    float(np.median(y_values)),
                    float(np.median(z_values)),
                ],
                dtype=np.float64,
            ),
            min_corner_margin_px=float(np.min([item.min_corner_margin_px for item in valid])),
            marker_side_px=float(np.median([item.marker_side_px for item in valid])),
        )
        return StableDockObservation(
            observation=observation,
            valid_frames=len(valid),
            window_frames=len(self._items),
            z_mad_m=z_mad,
            x_mad_m=x_mad,
            bearing_mad_rad=bearing_mad,
            normal_yaw_mad_rad=normal_mad,
        )
