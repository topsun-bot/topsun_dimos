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

import numpy as np
import yaml
from numpy.typing import NDArray

from dimos.core.core import rpc
from dimos.core.global_config import global_config
from dimos.core.module import Module, ModuleConfig
from dimos.core.stream import In, Out
from dimos.msgs.geometry_msgs.PoseStamped import PoseStamped
from dimos.msgs.geometry_msgs.Twist import Twist
from dimos.msgs.nav_msgs.Path import Path
from dimos.navigation.replanning_a_star.controllers import PController
from dimos.utils.logging_config import setup_logger

from engine.nomad.config import DEFAULT_NAV_CONFIG

logger = setup_logger()


class WaypointFollowerConfig(ModuleConfig):
    """Pure-pursuit follower for egocentric NoMaD paths in ``base_link``."""

    speed: float = 0.4
    lookahead_distance: float = 0.5
    control_hz: float = 10.0
    goal_tolerance: float = 0.15
    control_enabled: bool = True
    # Ignore new local_waypoints until the current path is held long enough and/or
    # the robot has moved at least this far (dead-reckoned from published cmd_vel).
    min_path_hold_s: float = 0.25
    min_path_progress_m: float = 0.08
    # When true, always accept the latest published local_waypoints.
    accept_latest_path: bool = True

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
            min_path_hold_s=float(raw.get("min_path_hold_s", 0.25)),
            min_path_progress_m=float(raw.get("min_path_progress_m", 0.08)),
            accept_latest_path=bool(raw.get("accept_latest_path", True)),
        )


class WaypointFollowerModule(Module):
    """Track ``local_waypoints`` and command the robot via ``cmd_vel``.

    Paths are in ``base_link`` (egocentric). New paths can arrive faster than the
    robot moves; :meth:`_should_accept_path` gates updates so the current segment
    is executed for at least ``min_path_hold_s`` and ``min_path_progress_m``.
    """

    config: WaypointFollowerConfig
    local_waypoints: In[Path]
    cmd_vel: Out[Twist]

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._lock = threading.Lock()
        self._path: Path | None = None
        self._path_accepted_mono: float = 0.0
        self._progress_m: float = 0.0
        # Pose of the robot in the path frame (body pose when the path was accepted).
        self._path_frame_x: float = 0.0
        self._path_frame_y: float = 0.0
        self._path_frame_yaw: float = 0.0
        self._last_cmd: Twist = Twist()
        self._path_reject_count = 0
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
        self.local_waypoints.subscribe(self._on_path)
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
            if self._path is not None and not self.config.accept_latest_path:
                if not self._should_accept_path_locked():
                    self._path_reject_count += 1
                    if self._path_reject_count % 20 == 1:
                        logger.debug(
                            "Holding current path (hold=%.2fs, progress=%.2fm / %.2fm)",
                            time.monotonic() - self._path_accepted_mono,
                            self._progress_m,
                            self.config.min_path_progress_m,
                        )
                    return
            self._path = path
            self._path_accepted_mono = time.monotonic()
            self._progress_m = 0.0
            self._path_frame_x = 0.0
            self._path_frame_y = 0.0
            self._path_frame_yaw = 0.0
            self._path_reject_count = 0
            self._controller.reset_errors()

    def _should_accept_path_locked(self) -> bool:
        """True if the latched path may be replaced by a newer local_waypoints message."""
        hold_s = self.config.min_path_hold_s
        progress_m = self.config.min_path_progress_m
        if hold_s <= 0.0 and progress_m <= 0.0:
            return True
        elapsed = time.monotonic() - self._path_accepted_mono
        hold_ok = hold_s <= 0.0 or elapsed >= hold_s
        progress_ok = progress_m <= 0.0 or self._progress_m >= progress_m
        return hold_ok and progress_ok

    def _control_loop(self) -> None:
        interval = 1.0 / max(self.config.control_hz, 1.0)
        while not self._stop_event.wait(interval):
            if not self.config.control_enabled:
                continue
            twist = self._compute_cmd()
            with self._lock:
                self._last_cmd = twist
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
        self._progress_m += float(np.hypot(dx, dy))

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

        return self._controller.advance(lookahead, self._path_frame_odom())

    def _lookahead_point(
        self,
        path: Path,
        distance: float,
        current: NDArray[np.float64],
    ) -> NDArray[np.float64] | None:
        points = [np.array([p.x, p.y], dtype=np.float64) for p in path.poses]
        if not points:
            return None

        closest = self._closest_point_on_polyline(current, points)
        remaining = distance
        pos = closest
        start_idx = int(np.argmin([np.linalg.norm(p - closest) for p in points]))
        prev = pos
        for idx in range(start_idx, len(points)):
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
    ) -> NDArray[np.float64]:
        best = points[0]
        best_dist = float(np.linalg.norm(best - current))
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
        return best

    def _path_frame_odom(self) -> PoseStamped:
        """Robot pose in the latched path frame (same frame as ``local_waypoints``)."""
        half_yaw = self._path_frame_yaw / 2.0
        return PoseStamped(
            ts=time.time(),
            frame_id="base_link",
            position=[self._path_frame_x, self._path_frame_y, 0.0],
            orientation=[0.0, 0.0, math.sin(half_yaw), math.cos(half_yaw)],
        )

    def _publish_stop(self) -> None:
        self.cmd_vel.publish(Twist())


waypoint_follower_module = WaypointFollowerModule.blueprint
