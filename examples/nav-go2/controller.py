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

"""Follow ``local_waypoints`` (base_link Path) and publish ``cmd_vel`` for Go2."""

from __future__ import annotations

import math
import threading
import time
from typing import Any, Self

from engine.nomad.config import DEFAULT_NAV_CONFIG
import numpy as np
from numpy.typing import NDArray
from reactivex.disposable import Disposable
import yaml

from dimos.core.core import rpc
from dimos.core.global_config import global_config
from dimos.core.module import Module, ModuleConfig
from dimos.core.stream import In, Out
from dimos.msgs.geometry_msgs.PoseStamped import PoseStamped
from dimos.msgs.geometry_msgs.Twist import Twist
from dimos.msgs.nav_msgs.Path import Path
from dimos.navigation.replanning_a_star.controllers import PController
from dimos.utils.logging_config import setup_logger

logger = setup_logger()


class WaypointFollowerConfig(ModuleConfig):
    """Pure-pursuit follower for egocentric NoMaD paths in ``base_link``."""

    speed: float = 0.4
    lookahead_distance: float = 0.5
    control_hz: float = 10.0
    goal_tolerance: float = 0.15
    control_enabled: bool = True

    @classmethod
    def load_default(cls) -> Self:
        """Load follower settings from ``config/nomad_nav.yaml``."""
        raw: dict[str, Any] = yaml.safe_load(DEFAULT_NAV_CONFIG.read_text()) or {}
        return cls(
            speed=float(raw.get("control_speed", 0.4)),
            lookahead_distance=float(raw.get("lookahead_distance", 0.5)),
            control_hz=float(raw.get("control_hz", 10.0)),
            goal_tolerance=float(raw.get("goal_tolerance", 0.15)),
            control_enabled=bool(raw.get("control_enabled", True)),
        )


class WaypointFollowerModule(Module):
    """Track ``local_waypoints`` and command the robot via ``cmd_vel``.

    Paths are in ``base_link`` (egocentric). The latest path is always accepted;
    the follower dead-reckons the robot pose relative to the accepted path frame.
    """

    config: WaypointFollowerConfig
    local_waypoints: In[Path]
    cmd_vel: Out[Twist]

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._lock = threading.Lock()
        self._path: Path | None = None
        # Pose of the robot in the path frame (body pose when the path was accepted).
        self._path_frame_x: float = 0.0
        self._path_frame_y: float = 0.0
        self._path_frame_yaw: float = 0.0
        self._path_frame_id: str = "base_link"
        self._control_count = 0
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._controller = PController(
            global_config,
            self.config.speed,
            self.config.control_hz,
        )

    @rpc
    def start(self) -> None:
        super().start()
        self.register_disposable(Disposable(self.local_waypoints.subscribe(self._on_path)))
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._control_loop, daemon=True)
        self._thread.start()
        logger.info(
            "WaypointFollower started (speed=%.2f, lookahead=%.2f, hz=%.1f)",
            self.config.speed,
            self.config.lookahead_distance,
            self.config.control_hz,
        )

    @rpc
    def stop(self) -> None:
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None
        self._publish_stop()
        super().stop()

    def _on_path(self, path: Path) -> None:
        if not path.poses:
            return
        with self._lock:
            self._path = path
            self._path_frame_x = 0.0
            self._path_frame_y = 0.0
            self._path_frame_yaw = 0.0
            self._path_frame_id = path.frame_id
            self._controller.reset_errors()

    def _control_loop(self) -> None:
        interval = 1.0 / max(self.config.control_hz, 1.0)
        while not self._stop_event.wait(interval):
            if not self.config.control_enabled:
                continue
            with self._lock:
                self._control_count += 1
            twist = self._compute_cmd()
            with self._lock:
                self._integrate_path_frame_pose(twist, interval)
            self.cmd_vel.publish(twist)

    def _integrate_path_frame_pose(self, twist: Twist, dt: float) -> None:
        """Integrate cmd_vel into the path frame (body pose when the path was latched)."""
        vx = float(twist.linear.x)
        vy = float(twist.linear.y)
        wz = float(twist.angular.z)
        yaw = self._path_frame_yaw
        cos_y = math.cos(yaw)
        sin_y = math.sin(yaw)
        # Twist is in the current body frame; rotate into the latched path frame.
        dx = (cos_y * vx - sin_y * vy) * dt
        dy = (sin_y * vx + cos_y * vy) * dt
        self._path_frame_x += dx
        self._path_frame_y += dy
        self._path_frame_yaw += wz * dt

    def _compute_cmd(self) -> Twist:
        with self._lock:
            path = self._path
            current = np.array(
                [self._path_frame_x, self._path_frame_y],
                dtype=np.float64,
            )

        if path is None or not path.poses:
            return Twist()

        goal = np.array([path.poses[-1].x, path.poses[-1].y], dtype=np.float64)
        if float(np.linalg.norm(goal - current)) < self.config.goal_tolerance:
            return Twist()

        lookahead = self._lookahead_point(path, self.config.lookahead_distance, current)
        if lookahead is None:
            return Twist()

        twist = self._controller.advance(lookahead, self._path_frame_odom())
        if self._control_count % 10 == 0:
            logger.info(
                "WaypointFollower current=(%.2f, %.2f) lookahead=(%.2f, %.2f) "
                "cmd=(linear.x=%.2f, angular.z=%.2f)",
                current[0],
                current[1],
                lookahead[0],
                lookahead[1],
                twist.linear.x,
                twist.angular.z,
            )
        return twist

    def _lookahead_point(
        self,
        path: Path,
        distance: float,
        current: NDArray[np.float64],
    ) -> NDArray[np.float64] | None:
        points = [np.array([p.x, p.y], dtype=np.float64) for p in path.poses]
        if not points:
            return None

        closest, segment_idx = self._closest_point_on_polyline(current, points)
        remaining = distance
        prev = closest
        for idx in range(segment_idx + 1, len(points)):
            point = points[idx]
            segment = float(np.linalg.norm(point - prev))
            if segment < 1e-6:
                prev = point
                continue
            if remaining <= segment:
                return prev + (remaining / segment) * (point - prev)
            remaining -= segment
            prev = point
        return points[-1]

    @staticmethod
    def _closest_point_on_polyline(
        current: NDArray[np.float64],
        points: list[NDArray[np.float64]],
    ) -> tuple[NDArray[np.float64], int]:
        best = points[0]
        best_dist = float(np.linalg.norm(best - current))
        best_segment_idx = 0
        for i in range(len(points) - 1):
            a, b = points[i], points[i + 1]
            ab = b - a
            length_sq = float(np.dot(ab, ab))
            if length_sq < 1e-12:
                candidate = a
            else:
                t = float(np.clip(np.dot(current - a, ab) / length_sq, 0.0, 1.0))
                candidate = a + t * ab
            dist = float(np.linalg.norm(candidate - current))
            if dist < best_dist:
                best_dist = dist
                best = candidate
                best_segment_idx = i
        return best, best_segment_idx

    def _path_frame_odom(self) -> PoseStamped:
        """Robot pose in the latched path frame (same frame as ``local_waypoints``)."""
        half_yaw = self._path_frame_yaw / 2.0
        return PoseStamped(
            ts=time.time(),
            frame_id=self._path_frame_id,
            position=[self._path_frame_x, self._path_frame_y, 0.0],
            orientation=[0.0, 0.0, math.sin(half_yaw), math.cos(half_yaw)],
        )

    def _publish_stop(self) -> None:
        self.cmd_vel.publish(Twist())


waypoint_follower_module = WaypointFollowerModule.blueprint
