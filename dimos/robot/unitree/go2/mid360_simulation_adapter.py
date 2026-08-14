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

"""Adapt MuJoCo Go2 sensors to the native Point-LIO stream contract.

MuJoCo already renders a world-frame point cloud and publishes the simulated
``world -> base_link`` pose. Production Mid360 navigation instead receives a
body-frame cloud plus ``world -> mid360_imu_link`` odometry from Point-LIO.
This module performs that inverse conversion so simulation exercises the same
``Go2Mid360NavigationSource`` and every downstream navigation module as the
real vehicle, without opening a Livox UDP socket.
"""

from __future__ import annotations

from bisect import bisect_left
from collections import deque
import math
import threading
from typing import Any

import numpy as np
from pydantic import Field
from reactivex.disposable import Disposable

from dimos.core.core import rpc
from dimos.core.module import Module, ModuleConfig
from dimos.core.stream import In, Out
from dimos.msgs.geometry_msgs.Pose import Pose
from dimos.msgs.geometry_msgs.PoseStamped import PoseStamped
from dimos.msgs.geometry_msgs.PoseWithCovariance import PoseWithCovariance
from dimos.msgs.geometry_msgs.Transform import Transform
from dimos.msgs.geometry_msgs.Vector3 import Vector3
from dimos.msgs.nav_msgs.Odometry import Odometry
from dimos.msgs.sensor_msgs.PointCloud2 import PointCloud2
from dimos.robot.unitree.go2.go2_mid360_static_transforms import (
    orin_navigation_base_from_pointlio_body,
)
from dimos.robot.unitree.go2.mid360_navigation_source import _slerp
from dimos.utils.logging_config import setup_logger

logger = setup_logger()


class Go2Mid360SimulationAdapterConfig(ModuleConfig):
    """Configuration for converting simulated Go2 sensors into Point-LIO data."""

    world_frame: str = "world"
    base_frame: str = "base_link"
    pointlio_body_frame: str = "mid360_imu_link"
    odom_history_sec: float = Field(default=2.0, gt=0.0)
    max_odom_bracket_sec: float = Field(default=0.1, gt=0.0)


class Go2Mid360SimulationAdapter(Module):
    """Publish Point-LIO-compatible streams from MuJoCo lidar and odometry."""

    config: Go2Mid360SimulationAdapterConfig

    simulation_world_lidar: In[PointCloud2]
    simulation_base_odom: In[PoseStamped]

    pointlio_lidar: Out[PointCloud2]
    pointlio_odometry: Out[Odometry]

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._lock = threading.Lock()
        self._odom_history: deque[PoseStamped] = deque()
        self._pending_cloud: PointCloud2 | None = None
        self._base_to_pointlio_body = orin_navigation_base_from_pointlio_body()
        self._pointlio_world_from_simulation_world: np.ndarray[Any, Any] | None = None

    @rpc
    def start(self) -> None:
        """Subscribe to simulated sensors and begin publishing adapted messages."""
        super().start()
        if self.config.g.simulation != "mujoco":
            raise RuntimeError(
                "Go2Mid360SimulationAdapter requires --simulation mujoco; "
                "it must never replace the native Point-LIO owner on hardware"
            )
        self.register_disposable(
            Disposable(self.simulation_base_odom.subscribe(self._on_simulation_odom))
        )
        self.register_disposable(
            Disposable(self.simulation_world_lidar.subscribe(self._on_simulation_lidar))
        )

    def _on_simulation_odom(self, msg: PoseStamped) -> None:
        """Store one base pose and publish its equivalent Point-LIO body pose."""
        if msg.frame_id != self.config.world_frame:
            logger.warning(
                "Dropping simulated odom with frame %r; expected %r",
                msg.frame_id,
                self.config.world_frame,
            )
            return

        simulation_world_to_body = self._world_to_pointlio_body(msg)
        with self._lock:
            if self._odom_history and msg.ts <= self._odom_history[-1].ts:
                return
            if self._pointlio_world_from_simulation_world is None:
                # Point-LIO initializes its gravity-aligned world frame at the
                # first IMU/body pose. Rebase MuJoCo the same way so the floor
                # lies below z=0 and downstream costmap height bands exercise
                # the same geometry as the production Mid360 source.
                self._pointlio_world_from_simulation_world = np.linalg.inv(
                    simulation_world_to_body.to_matrix()
                )
            self._odom_history.append(msg)
            cutoff = msg.ts - self.config.odom_history_sec
            while self._odom_history and self._odom_history[0].ts < cutoff:
                self._odom_history.popleft()
            pending = self._take_ready_cloud_locked()

            pointlio_world_from_simulation_world = self._pointlio_world_from_simulation_world.copy()

        pointlio_world_to_body = Transform.from_matrix(
            pointlio_world_from_simulation_world @ simulation_world_to_body.to_matrix(),
            ts=msg.ts,
            frame_id=self.config.world_frame,
            child_frame_id=self.config.pointlio_body_frame,
        )
        self.pointlio_odometry.publish(
            Odometry(
                ts=msg.ts,
                frame_id=self.config.world_frame,
                child_frame_id=self.config.pointlio_body_frame,
                pose=PoseWithCovariance(
                    Pose(pointlio_world_to_body.translation, pointlio_world_to_body.rotation)
                ),
            )
        )
        if pending is not None:
            cloud, world_to_body_at_cloud = pending
            self.pointlio_lidar.publish(
                self._world_cloud_to_pointlio_body(cloud, world_to_body_at_cloud)
            )

    def _on_simulation_lidar(self, msg: PointCloud2) -> None:
        """Queue a world-frame cloud until its simulated pose is bracketed."""
        if msg.frame_id != self.config.world_frame:
            logger.warning(
                "Dropping simulated lidar with frame %r; expected %r",
                msg.frame_id,
                self.config.world_frame,
            )
            return

        with self._lock:
            self._pending_cloud = msg
            ready = self._take_ready_cloud_locked()
        if ready is not None:
            cloud, world_to_body = ready
            self.pointlio_lidar.publish(self._world_cloud_to_pointlio_body(cloud, world_to_body))

    def _take_ready_cloud_locked(self) -> tuple[PointCloud2, Transform] | None:
        cloud = self._pending_cloud
        if cloud is None:
            return None
        world_to_base = self._interpolate_world_to_base_locked(cloud.ts)
        if world_to_base is None:
            return None
        self._pending_cloud = None
        return cloud, world_to_base + self._base_to_pointlio_body

    def _interpolate_world_to_base_locked(self, ts: float) -> Transform | None:
        history = list(self._odom_history)
        if not history:
            return None
        timestamps = [pose.ts for pose in history]
        index = bisect_left(timestamps, ts)

        if index < len(history) and math.isclose(history[index].ts, ts, abs_tol=1e-6):
            return Transform.from_pose(self.config.base_frame, history[index])
        if index == 0 or index == len(history):
            return None

        before = history[index - 1]
        after = history[index]
        if (
            ts - before.ts > self.config.max_odom_bracket_sec
            or after.ts - ts > self.config.max_odom_bracket_sec
        ):
            return None

        alpha = (ts - before.ts) / (after.ts - before.ts)
        return Transform(
            translation=Vector3(before.position * (1.0 - alpha) + after.position * alpha),
            rotation=_slerp(before.orientation, after.orientation, alpha),
            frame_id=self.config.world_frame,
            child_frame_id=self.config.base_frame,
            ts=ts,
        )

    def _world_to_pointlio_body(self, msg: PoseStamped) -> Transform:
        return Transform.from_pose(self.config.base_frame, msg) + self._base_to_pointlio_body

    def _world_cloud_to_pointlio_body(
        self,
        cloud: PointCloud2,
        world_to_body: Transform,
    ) -> PointCloud2:
        points = cloud.points_f32()
        body_to_world = world_to_body.inverse().to_matrix()
        body_points = points @ body_to_world[:3, :3].T + body_to_world[:3, 3]
        return PointCloud2.from_numpy(
            body_points.astype(np.float32),
            frame_id=self.config.pointlio_body_frame,
            timestamp=cloud.ts,
            intensities=cloud.intensities_f32(),
        )
