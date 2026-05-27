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

import logging
import math
import time

from reactivex.disposable import Disposable

from dimos.core.core import rpc
from dimos.core.module import Module, ModuleConfig
from dimos.core.stream import In, Out
from dimos.msgs.geometry_msgs.PoseStamped import PoseStamped
from dimos.msgs.geometry_msgs.Quaternion import Quaternion
from dimos.msgs.geometry_msgs.Transform import Transform
from dimos.msgs.geometry_msgs.Vector3 import Vector3
from dimos.msgs.sensor_msgs.CameraInfo import CameraInfo
from dimos.msgs.vision_msgs.Detection2DArray import Detection2DArray
from dimos.utils.logging_config import setup_logger

logger = setup_logger(level=logging.DEBUG)


class Config(ModuleConfig):
    enabled: bool = True
    goal_distance: float = 1.0
    world_frame: str = "world"
    min_goal_interval_s: float = 1.0
    min_goal_translation_delta_m: float = 0.25
    min_goal_yaw_delta_deg: float = 15.0
    lock_goal_until_detection_lost: bool = False


class BBoxNavigationModule(Module):
    """Minimal module that converts 2D bbox center to navigation goals."""

    config: Config

    detection2d: In[Detection2DArray]
    camera_info: In[CameraInfo]
    goal_request: Out[PoseStamped]
    camera_intrinsics = None
    _last_goal: PoseStamped | None = None
    _last_goal_publish_time: float = 0.0
    _goal_locked: bool = False

    @rpc
    def start(self) -> None:
        unsub = self.camera_info.subscribe(
            lambda msg: setattr(self, "camera_intrinsics", [msg.K[0], msg.K[4], msg.K[2], msg.K[5]])
        )
        self.register_disposable(Disposable(unsub))

        unsub = self.detection2d.subscribe(self._on_detection)
        self.register_disposable(Disposable(unsub))

    @rpc
    def stop(self) -> None:
        super().stop()

    def _on_detection(self, det: Detection2DArray) -> None:
        if not self.config.enabled:
            self._goal_locked = False
            self._last_goal = None
            return
        if det.detections_length == 0 or not self.camera_intrinsics:
            if det.detections_length == 0:
                self._goal_locked = False
                self._last_goal = None
            return
        fx, fy, cx, cy = self.camera_intrinsics
        center_x, center_y = (
            det.detections[0].bbox.center.position.x,
            det.detections[0].bbox.center.position.y,
        )
        x, y, z = (
            (center_x - cx) / fx * self.config.goal_distance,
            (center_y - cy) / fy * self.config.goal_distance,
            self.config.goal_distance,
        )
        camera_goal = Transform(
            translation=Vector3(z, -x, -y),
            rotation=Quaternion(0, 0, 0, 1),
            frame_id=det.header.frame_id,
            child_frame_id="bbox_goal",
            ts=det.ts,
        )
        world_to_camera = self.tf.get(
            self.config.world_frame,
            det.header.frame_id,
            det.ts,
            time_tolerance=1.0,
        )
        if world_to_camera is None:
            logger.warning(
                "Could not transform bbox goal from %r to %r",
                det.header.frame_id,
                self.config.world_frame,
            )
            return

        goal_tf = world_to_camera + camera_goal
        goal = goal_tf.to_pose(ts=det.ts)
        if not self._should_publish_goal(goal):
            return

        logger.debug(
            f"BBox center: ({center_x:.1f}, {center_y:.1f}) → "
            f"Goal pose: ({goal.x:.2f}, {goal.y:.2f}, {goal.z:.2f}) "
            f"in frame '{goal.frame_id}'"
        )
        self.goal_request.publish(goal)
        self._last_goal = goal
        self._last_goal_publish_time = time.monotonic()
        self._goal_locked = self.config.lock_goal_until_detection_lost

    def _should_publish_goal(self, goal: PoseStamped) -> bool:
        if self._goal_locked:
            logger.debug("Skipping bbox goal update: goal locked until detection is lost")
            return False

        if self._last_goal is None:
            return True

        elapsed = time.monotonic() - self._last_goal_publish_time
        if elapsed < self.config.min_goal_interval_s:
            return False

        dx = goal.x - self._last_goal.x
        dy = goal.y - self._last_goal.y
        distance = math.hypot(dx, dy)
        yaw_diff = abs(
            math.atan2(
                math.sin(goal.yaw - self._last_goal.yaw),
                math.cos(goal.yaw - self._last_goal.yaw),
            )
        )
        if distance < self.config.min_goal_translation_delta_m and yaw_diff < math.radians(
            self.config.min_goal_yaw_delta_deg
        ):
            logger.debug(
                "Skipping bbox goal update: delta=%.2fm yaw=%.1f°",
                distance,
                math.degrees(yaw_diff),
            )
            return False

        return True
