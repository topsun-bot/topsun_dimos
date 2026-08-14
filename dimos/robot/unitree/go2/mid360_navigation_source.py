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

"""Adapt native Point-LIO output to the Go2 2D navigation contracts.

Point-LIO publishes an IMU/body-frame cloud and ``world -> IMU`` odometry.
The existing Go2 mapping stack expects a world-frame cloud plus a
``world -> base_link`` pose. This module owns that conversion and the health
gate for the external navigation source.
"""

from __future__ import annotations

from bisect import bisect_left
from collections import deque
import math
import threading
import time
from typing import Any

from dimos_lcm.std_msgs import Bool, String  # type: ignore[import-untyped]
import numpy as np
from pydantic import Field
from reactivex.disposable import Disposable

from dimos.constants import DEFAULT_THREAD_JOIN_TIMEOUT
from dimos.core.core import rpc
from dimos.core.module import Module, ModuleConfig
from dimos.core.stream import In, Out
from dimos.msgs.geometry_msgs.Pose import Pose
from dimos.msgs.geometry_msgs.PoseStamped import PoseStamped
from dimos.msgs.geometry_msgs.PoseWithCovariance import PoseWithCovariance
from dimos.msgs.geometry_msgs.Quaternion import Quaternion
from dimos.msgs.geometry_msgs.Transform import Transform
from dimos.msgs.geometry_msgs.TwistWithCovariance import TwistWithCovariance
from dimos.msgs.geometry_msgs.Vector3 import Vector3
from dimos.msgs.nav_msgs.Odometry import Odometry
from dimos.msgs.sensor_msgs.PointCloud2 import PointCloud2
from dimos.msgs.tf2_msgs.TFMessage import TFMessage
from dimos.robot.unitree.go2.go2_mid360_static_transforms import (
    orin_navigation_base_from_pointlio_body,
)
from dimos.utils.logging_config import setup_logger

logger = setup_logger()


class Go2Mid360NavigationSourceConfig(ModuleConfig):
    world_frame: str = "world"
    pointlio_body_frame: str = "mid360_imu_link"
    base_frame: str = "base_link"

    odom_history_sec: float = Field(default=3.0, gt=0.0)
    max_odom_bracket_sec: float = Field(default=0.1, gt=0.0)
    cloud_wait_timeout_sec: float = Field(default=0.15, gt=0.0)

    min_range_m: float = Field(default=0.5, ge=0.0)
    max_range_m: float = Field(default=30.0, gt=0.0)
    voxel_size_m: float = Field(default=0.05, ge=0.0)

    # Robot-body exclusion box in base_link. It removes returns from the Go2
    # body while deliberately avoiding a global z cutoff that would erase floor.
    self_min_xyz: tuple[float, float, float] = (-0.45, -0.28, -0.35)
    self_max_xyz: tuple[float, float, float] = (0.45, 0.28, 0.35)

    # Native Point-LIO may need several seconds for driver startup and IMU
    # initialization. Autonomous motion remains gated throughout this window.
    startup_timeout_sec: float = Field(default=30.0, gt=0.0)
    lidar_stale_timeout_sec: float = Field(default=1.0, gt=0.0)
    odom_stale_timeout_sec: float = Field(default=0.5, gt=0.0)

    # A source-clock discontinuity makes cloud/odom interpolation unsafe even
    # when packets are still arriving promptly at the host.
    timestamp_regression_tolerance_sec: float = Field(default=1e-6, ge=0.0)
    max_lidar_timestamp_step_sec: float = Field(default=2.0, gt=0.0)
    max_odom_timestamp_step_sec: float = Field(default=0.5, gt=0.0)

    # These are reset detectors, not normal motion limits. The thresholds are
    # deliberately far above one Point-LIO odometry step for a Go2.
    max_odom_position_step_m: float = Field(default=0.75, gt=0.0)
    max_odom_rotation_step_rad: float = Field(default=math.radians(45.0), gt=0.0)

    # Processing metrics are accumulated in memory and summarized sparsely so
    # diagnostics cannot turn a 5-10 Hz point-cloud path into an I/O workload.
    diagnostics_interval_sec: float = Field(default=5.0, gt=0.0)
    diagnostics_window_size: int = Field(default=256, gt=0)


class Go2Mid360NavigationSource(Module):
    """Convert Point-LIO data and fail closed when the source becomes stale."""

    config: Go2Mid360NavigationSourceConfig

    pointlio_lidar: In[PointCloud2]
    pointlio_odometry: In[Odometry]

    lidar: Out[PointCloud2]
    odom: Out[PoseStamped]
    navigation_odometry: Out[Odometry]
    mapping_lidar: Out[PointCloud2]
    mapping_odometry: Out[Odometry]
    tf: Out[TFMessage]
    navigation_source_healthy: Out[Bool]
    navigation_source_status: Out[String]

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._condition = threading.Condition()
        self._odom_history: deque[Odometry] = deque()
        self._pending_cloud: tuple[PointCloud2, float] | None = None
        self._worker: threading.Thread | None = None
        self._running = False
        self._started_monotonic = 0.0
        self._last_lidar_rx_monotonic: float | None = None
        self._last_odom_rx_monotonic: float | None = None
        self._last_lidar_source_ts: float | None = None
        self._last_odom_source_ts: float | None = None
        self._last_accepted_odom: Odometry | None = None
        self._reported_invalid_startup_odom = False
        self._ready = False
        self._fault_latched = False
        self._status = "CREATED"
        self._pending_cloud_replacements = 0
        self._clouds_dropped_no_odom = 0
        self._processing_samples: deque[tuple[float, float, float, float, int, int]] = deque(
            maxlen=self.config.diagnostics_window_size
        )
        self._last_diagnostics_monotonic = time.monotonic()
        self._base_to_pointlio_body = orin_navigation_base_from_pointlio_body()

    @rpc
    def start(self) -> None:
        super().start()
        self._started_monotonic = time.monotonic()
        self._running = True
        self.register_disposable(Disposable(self.pointlio_lidar.subscribe(self._on_pointlio_lidar)))
        self.register_disposable(
            Disposable(self.pointlio_odometry.subscribe(self._on_pointlio_odometry))
        )
        self._worker = threading.Thread(
            target=self._worker_loop,
            name="go2-mid360-navigation-source",
            daemon=True,
        )
        self._worker.start()
        self._publish_status("STARTING")

    @rpc
    def stop(self) -> None:
        with self._condition:
            self._running = False
            self._condition.notify_all()
        if self._worker and self._worker.is_alive():
            self._worker.join(timeout=DEFAULT_THREAD_JOIN_TIMEOUT)
            if self._worker.is_alive():
                logger.warning("Mid360 navigation-source worker did not stop in time")
        super().stop()

    @rpc
    def is_navigation_source_healthy(self) -> bool:
        """Return whether Mid360 data is ready and no fault has been latched."""
        with self._condition:
            return self._ready and not self._fault_latched

    @rpc
    def get_navigation_source_status(self) -> str:
        """Return the latest startup, running, or latched-fault status."""
        with self._condition:
            return self._status

    def _on_pointlio_lidar(self, msg: PointCloud2) -> None:
        now = time.monotonic()
        with self._condition:
            if self._fault_latched:
                return
            if not self._accept_source_timestamp_locked(
                source="LIDAR",
                timestamp=msg.ts,
                max_step_sec=self.config.max_lidar_timestamp_step_sec,
            ):
                return
            if self._pending_cloud is not None:
                self._pending_cloud_replacements += 1
                logger.debug("Mid360 latest-wins queue replaced one pending cloud")
            self._pending_cloud = (msg, now)
            self._last_lidar_rx_monotonic = now
            self._condition.notify_all()

    def _on_pointlio_odometry(self, msg: Odometry) -> None:
        if (
            msg.frame_id != self.config.world_frame
            or msg.child_frame_id != self.config.pointlio_body_frame
        ):
            self._latch_fault(
                "FRAME_MISMATCH: "
                f"expected {self.config.world_frame}->{self.config.pointlio_body_frame}, "
                f"got {msg.frame_id}->{msg.child_frame_id}"
            )
            return

        now = time.monotonic()
        with self._condition:
            if self._fault_latched:
                return
            # Point-LIO can emit a short run of zero-quaternion placeholders
            # while its state initializes. Do not let those frames establish a
            # source-time or pose baseline; after the first valid pose, the
            # same data is a real runtime fault.
            if not self._accept_odom_pose_locked(msg):
                return
            if not self._accept_source_timestamp_locked(
                source="ODOM",
                timestamp=msg.ts,
                max_step_sec=self.config.max_odom_timestamp_step_sec,
            ):
                return
            self._insert_odom_locked(msg)
            self._last_accepted_odom = msg
            self._last_odom_rx_monotonic = now
            self._condition.notify_all()

        world_to_base = self._world_to_base(msg)
        self._publish_base_state(msg, world_to_base)

    def _insert_odom_locked(self, msg: Odometry) -> None:
        self._odom_history.append(msg)

        newest_ts = self._odom_history[-1].ts
        cutoff = newest_ts - self.config.odom_history_sec
        while self._odom_history and self._odom_history[0].ts < cutoff:
            self._odom_history.popleft()

    def _worker_loop(self) -> None:
        while True:
            work: tuple[PointCloud2, Transform] | None = None
            with self._condition:
                self._condition.wait(timeout=0.02)
                if not self._running:
                    return

                self._check_stale_locked()
                pending = self._pending_cloud
                if pending is not None and not self._fault_latched:
                    cloud, queued_at = pending
                    world_to_body = self._interpolate_world_to_pointlio_body_locked(cloud.ts)
                    if world_to_body is not None:
                        self._pending_cloud = None
                        work = (cloud, world_to_body)
                    elif time.monotonic() - queued_at >= self.config.cloud_wait_timeout_sec:
                        self._pending_cloud = None
                        self._clouds_dropped_no_odom += 1
                        self._publish_status(
                            f"CLOUD_DROPPED_NO_ODOM: ts={cloud.ts:.6f} "
                            f"wait={self.config.cloud_wait_timeout_sec:.3f}s"
                        )

            if work is not None:
                cloud, world_to_body = work
                try:
                    output = self._prepare_world_cloud(cloud, world_to_body)
                except Exception:
                    logger.exception("Failed to prepare Mid360 cloud for navigation")
                    self._latch_fault("POINTCLOUD_PROCESSING_ERROR")
                    continue
                # Send the exact interpolated pose used for this cloud before
                # the cloud itself. Publishing the independent raw odom stream
                # here would force the asynchronous mapper to repeat timestamp
                # association and can drop an already-valid cloud at the 0.1 s
                # boundary. Navigation still receives the continuous odom
                # stream from _publish_base_state().
                self.mapping_odometry.publish(
                    self._mapping_odometry_for_cloud(cloud, world_to_body)
                )
                self.mapping_lidar.publish(cloud)
                self.lidar.publish(output)
                self._mark_ready()

    def _check_stale_locked(self) -> None:
        if self._fault_latched:
            return
        now = time.monotonic()
        if not self._ready:
            if now - self._started_monotonic > self.config.startup_timeout_sec:
                self._latch_fault_locked("STARTUP_TIMEOUT")
            return

        assert self._last_lidar_rx_monotonic is not None
        assert self._last_odom_rx_monotonic is not None
        lidar_age = now - self._last_lidar_rx_monotonic
        odom_age = now - self._last_odom_rx_monotonic
        if lidar_age > self.config.lidar_stale_timeout_sec:
            self._latch_fault_locked(f"LIDAR_STALE: age={lidar_age:.3f}s")
        elif odom_age > self.config.odom_stale_timeout_sec:
            self._latch_fault_locked(f"ODOM_STALE: age={odom_age:.3f}s")

    def _interpolate_world_to_pointlio_body_locked(self, ts: float) -> Transform | None:
        history = list(self._odom_history)
        if not history:
            return None
        timestamps = [odom.ts for odom in history]
        index = bisect_left(timestamps, ts)

        if index < len(history) and math.isclose(history[index].ts, ts, abs_tol=1e-6):
            return self._world_to_pointlio_body(history[index])
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
        position = before.position * (1.0 - alpha) + after.position * alpha
        orientation = _slerp(before.orientation, after.orientation, alpha)
        return Transform(
            translation=Vector3(position),
            rotation=orientation,
            frame_id=self.config.world_frame,
            child_frame_id=self.config.pointlio_body_frame,
            ts=ts,
        )

    def _world_to_pointlio_body(self, msg: Odometry) -> Transform:
        return Transform(
            translation=Vector3(msg.position),
            rotation=Quaternion(msg.orientation),
            frame_id=self.config.world_frame,
            child_frame_id=self.config.pointlio_body_frame,
            ts=msg.ts,
        )

    def _world_to_base(self, msg: Odometry) -> Transform:
        world_to_body = self._world_to_pointlio_body(msg)
        pointlio_body_to_base = self._base_to_pointlio_body.inverse()
        result = world_to_body + pointlio_body_to_base
        result.frame_id = self.config.world_frame
        result.child_frame_id = self.config.base_frame
        result.ts = msg.ts
        return result

    def _mapping_odometry_for_cloud(
        self,
        cloud: PointCloud2,
        world_to_pointlio_body: Transform,
    ) -> Odometry:
        """Build the mapper pose paired exactly with one accepted cloud."""
        return Odometry(
            ts=cloud.ts,
            frame_id=self.config.world_frame,
            child_frame_id=self.config.pointlio_body_frame,
            pose=PoseWithCovariance(
                Pose(
                    world_to_pointlio_body.translation,
                    world_to_pointlio_body.rotation,
                )
            ),
        )

    def _publish_base_state(self, source: Odometry, world_to_base: Transform) -> None:
        pose = world_to_base.to_pose(ts=source.ts)
        pose.frame_id = self.config.world_frame
        self.odom.publish(pose)
        self.navigation_odometry.publish(
            Odometry(
                ts=source.ts,
                frame_id=self.config.world_frame,
                child_frame_id=self.config.base_frame,
                pose=PoseWithCovariance(
                    Pose(world_to_base.translation, world_to_base.rotation),
                    source.pose.covariance,
                ),
                twist=TwistWithCovariance(source.twist),
            )
        )
        self.tf.publish(TFMessage(world_to_base))

    def _prepare_world_cloud(
        self,
        cloud: PointCloud2,
        world_to_pointlio_body: Transform,
    ) -> PointCloud2:
        started = time.perf_counter()
        if cloud.frame_id != self.config.pointlio_body_frame:
            raise ValueError(
                f"expected cloud frame {self.config.pointlio_body_frame!r}, got {cloud.frame_id!r}"
            )

        points = cloud.points_f32()
        intensities = cloud.intensities_f32()
        if points.ndim != 2 or points.shape[1] != 3:
            raise ValueError(f"expected Nx3 points, got shape={points.shape}")

        finite = np.isfinite(points).all(axis=1)
        ranges = np.linalg.norm(points, axis=1)
        keep = finite & (ranges >= self.config.min_range_m) & (ranges <= self.config.max_range_m)

        base_matrix = self._base_to_pointlio_body.to_matrix()
        points_base = points @ base_matrix[:3, :3].T + base_matrix[:3, 3]
        self_min = np.asarray(self.config.self_min_xyz, dtype=np.float32)
        self_max = np.asarray(self.config.self_max_xyz, dtype=np.float32)
        inside_robot = ((points_base >= self_min) & (points_base <= self_max)).all(axis=1)
        keep &= ~inside_robot
        filtered_at = time.perf_counter()

        indexes = np.flatnonzero(keep)
        filtered = points[indexes]
        if self.config.voxel_size_m > 0.0 and len(filtered) > 0:
            voxel_keys = np.floor(filtered / self.config.voxel_size_m).astype(np.int64)
            _, unique = np.unique(voxel_keys, axis=0, return_index=True)
            unique = np.sort(unique)
            indexes = indexes[unique]
            filtered = points[indexes]
        voxelized_at = time.perf_counter()

        world_matrix = world_to_pointlio_body.to_matrix()
        world_points = filtered @ world_matrix[:3, :3].T + world_matrix[:3, 3]
        output_intensities = intensities[indexes] if intensities is not None else None
        output = PointCloud2.from_numpy(
            world_points.astype(np.float32),
            frame_id=self.config.world_frame,
            timestamp=cloud.ts,
            intensities=output_intensities,
        )
        finished = time.perf_counter()
        self._record_processing_sample(
            filter_ms=(filtered_at - started) * 1000.0,
            voxel_ms=(voxelized_at - filtered_at) * 1000.0,
            transform_publish_ms=(finished - voxelized_at) * 1000.0,
            total_ms=(finished - started) * 1000.0,
            input_points=len(points),
            output_points=len(world_points),
        )
        return output

    def _accept_source_timestamp_locked(
        self,
        *,
        source: str,
        timestamp: float,
        max_step_sec: float,
    ) -> bool:
        if not math.isfinite(timestamp) or timestamp < 0.0:
            self._latch_fault_locked(f"{source}_TIMESTAMP_INVALID: ts={timestamp!r}")
            return False

        attr = f"_last_{source.lower()}_source_ts"
        previous = getattr(self, attr)
        if previous is not None:
            delta = timestamp - previous
            if delta < -self.config.timestamp_regression_tolerance_sec:
                self._latch_fault_locked(
                    f"{source}_TIMESTAMP_ROLLBACK: previous={previous:.6f} "
                    f"current={timestamp:.6f} delta={delta:.6f}s"
                )
                return False
            if delta <= 0.0:
                # A duplicate or sub-microsecond regression cannot extend the
                # interpolation history. Drop it without turning timestamp
                # quantization noise into a robot-wide fault.
                return False
            if delta > max_step_sec:
                self._latch_fault_locked(
                    f"{source}_TIMESTAMP_JUMP: previous={previous:.6f} "
                    f"current={timestamp:.6f} delta={delta:.6f}s "
                    f"limit={max_step_sec:.3f}s"
                )
                return False

        setattr(self, attr, timestamp)
        return True

    def _accept_odom_pose_locked(self, msg: Odometry) -> bool:
        position = np.asarray(msg.position.data, dtype=np.float64)
        quaternion = np.asarray(msg.orientation.to_tuple(), dtype=np.float64)
        if not np.isfinite(position).all() or not np.isfinite(quaternion).all():
            return self._reject_invalid_odom_pose_locked(
                "ODOM_POSE_INVALID: non-finite position or orientation"
            )
        quaternion_norm = float(np.linalg.norm(quaternion))
        if quaternion_norm < 1e-6:
            return self._reject_invalid_odom_pose_locked(
                "ODOM_POSE_INVALID: zero-length orientation"
            )

        previous = self._last_accepted_odom
        if previous is None:
            return True

        position_step = float(
            np.linalg.norm(position - np.asarray(previous.position.data, dtype=np.float64))
        )
        previous_orientation = Quaternion(previous.orientation).normalize()
        current_orientation = Quaternion(msg.orientation).normalize()
        rotation_step = previous_orientation.angle_to(current_orientation)
        if position_step > self.config.max_odom_position_step_m:
            self._latch_fault_locked(
                f"ODOM_POSE_JUMP: translation={position_step:.3f}m "
                f"limit={self.config.max_odom_position_step_m:.3f}m"
            )
            return False
        if rotation_step > self.config.max_odom_rotation_step_rad:
            self._latch_fault_locked(
                f"ODOM_POSE_JUMP: rotation={math.degrees(rotation_step):.1f}deg "
                f"limit={math.degrees(self.config.max_odom_rotation_step_rad):.1f}deg"
            )
            return False
        return True

    def _reject_invalid_odom_pose_locked(self, reason: str) -> bool:
        if self._last_accepted_odom is not None:
            self._latch_fault_locked(reason)
            return False
        if not self._reported_invalid_startup_odom:
            self._reported_invalid_startup_odom = True
            self._publish_status(f"WAITING_FOR_VALID_ODOM: {reason}")
            logger.warning("Ignoring Point-LIO startup odometry placeholder: %s", reason)
        return False

    def _record_processing_sample(
        self,
        *,
        filter_ms: float,
        voxel_ms: float,
        transform_publish_ms: float,
        total_ms: float,
        input_points: int,
        output_points: int,
    ) -> None:
        self._processing_samples.append(
            (
                filter_ms,
                voxel_ms,
                transform_publish_ms,
                total_ms,
                input_points,
                output_points,
            )
        )
        now = time.monotonic()
        if now - self._last_diagnostics_monotonic < self.config.diagnostics_interval_sec:
            return
        self._last_diagnostics_monotonic = now

        samples = np.asarray(self._processing_samples, dtype=np.float64)
        p95 = np.percentile(samples[:, :4], 95, axis=0)
        logger.info(
            "Mid360 preprocessing diagnostics: samples=%d input_pts_mean=%.0f "
            "output_pts_mean=%.0f filter_p95_ms=%.2f voxel_p95_ms=%.2f "
            "transform_publish_p95_ms=%.2f total_p95_ms=%.2f "
            "latest_wins_replaced=%d dropped_no_odom=%d",
            len(samples),
            float(np.mean(samples[:, 4])),
            float(np.mean(samples[:, 5])),
            p95[0],
            p95[1],
            p95[2],
            p95[3],
            self._pending_cloud_replacements,
            self._clouds_dropped_no_odom,
        )
        with self._condition:
            publish_healthy = self._ready and not self._fault_latched
        if publish_healthy:
            # LCM streams are not latched. Repeat the healthy state sparsely so
            # consumers that subscribe after startup cannot remain locked forever.
            self.navigation_source_healthy.publish(Bool(data=True))
            self._publish_status("RUNNING")

    def _mark_ready(self) -> None:
        with self._condition:
            if self._ready or self._fault_latched:
                return
            self._ready = True
        self.navigation_source_healthy.publish(Bool(data=True))
        self._publish_status("RUNNING")
        logger.info("Mid360 navigation source is RUNNING")

    def _latch_fault(self, reason: str) -> None:
        with self._condition:
            self._latch_fault_locked(reason)

    def _latch_fault_locked(self, reason: str) -> None:
        if self._fault_latched:
            return
        self._fault_latched = True
        self._pending_cloud = None
        self.navigation_source_healthy.publish(Bool(data=False))
        self._publish_status(f"ERROR: {reason}")
        logger.error("Mid360 navigation source fault latched: %s", reason)

    def _publish_status(self, status: str) -> None:
        with self._condition:
            self._status = status
        self.navigation_source_status.publish(String(data=status))


def _slerp(first: Quaternion, second: Quaternion, alpha: float) -> Quaternion:
    """Shortest-path unit-quaternion interpolation without a SciPy hot-path import."""
    q0 = np.asarray(first.to_tuple(), dtype=np.float64)
    q1 = np.asarray(second.to_tuple(), dtype=np.float64)
    q0 /= np.linalg.norm(q0)
    q1 /= np.linalg.norm(q1)

    dot = float(np.dot(q0, q1))
    if dot < 0.0:
        q1 = -q1
        dot = -dot
    dot = min(1.0, max(-1.0, dot))

    if dot > 0.9995:
        result = q0 + alpha * (q1 - q0)
        result /= np.linalg.norm(result)
    else:
        theta_0 = math.acos(dot)
        sin_theta_0 = math.sin(theta_0)
        result = (
            math.sin((1.0 - alpha) * theta_0) / sin_theta_0 * q0
            + math.sin(alpha * theta_0) / sin_theta_0 * q1
        )
    return Quaternion(result)
