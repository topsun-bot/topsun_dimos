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

"""Orbit around an object edge detected by LiDAR costmap.

Uses wall-following on the OccupancyGrid to maintain a fixed distance from the
nearest obstacle edge while moving tangentially along it.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
from threading import Event, Thread
import time
from typing import Any

from dimos_lcm.std_msgs import Bool
import numpy as np
from numpy.typing import NDArray
from reactivex.disposable import Disposable

from dimos.agents.annotation import skill
from dimos.constants import DEFAULT_THREAD_JOIN_TIMEOUT
from dimos.core.core import rpc
from dimos.core.module import Module
from dimos.core.stream import In, Out
from dimos.msgs.geometry_msgs.PoseStamped import PoseStamped
from dimos.msgs.geometry_msgs.Twist import Twist
from dimos.msgs.geometry_msgs.Vector3 import Vector3
from dimos.msgs.nav_msgs.OccupancyGrid import OccupancyGrid
from dimos.utils.logging_config import setup_logger

logger = setup_logger()

OCCUPIED_THRESHOLD = 80
_UNSET: float = -1e9


def _angle_diff(a: float, b: float) -> float:
    """Signed shortest angular difference *a − b*, wrapped to (−π, π]."""
    d = a - b
    return (d + math.pi) % (2 * math.pi) - math.pi


@dataclass
class EdgeResult:
    """Nearest occupied edge relative to the robot."""

    point: np.ndarray  # (2,) world coords of nearest edge
    normal: np.ndarray  # (2,) unit normal pointing toward free space
    distance: float  # metres from robot to edge point


def extract_edge(
    costmap: OccupancyGrid,
    robot_xy: np.ndarray,
    robot_yaw: float,
    search_radius: float = 3.0,
    *,
    target_xy: np.ndarray | None = None,
    bearing_deg: float | None = None,
    k_neighbours: int = 8,
) -> EdgeResult | None:
    """Find the nearest occupied edge in *costmap* and compute its outward normal.

    Target selection priority:
      1. *target_xy* — search only around that world coordinate.
      2. *bearing_deg* — search in a ±45° sector relative to *robot_yaw*.
      3. Otherwise — search all around the robot.

    Returns ``None`` when no occupied cell is found within *search_radius*.
    """

    grid: NDArray[np.int8] = costmap.grid
    if grid.size == 0:
        return None

    ys, xs = np.where(grid >= OCCUPIED_THRESHOLD)
    if len(xs) == 0:
        return None

    ox = costmap.origin.position.x
    oy = costmap.origin.position.y
    res = costmap.resolution

    world_xs = ox + xs.astype(np.float64) * res
    world_ys = oy + ys.astype(np.float64) * res

    if target_xy is not None:
        dx = world_xs - target_xy[0]
        dy = world_ys - target_xy[1]
        dists_sq = dx * dx + dy * dy
        mask = dists_sq <= search_radius * search_radius
    elif bearing_deg is not None:
        dx = world_xs - robot_xy[0]
        dy = world_ys - robot_xy[1]
        dists_sq = dx * dx + dy * dy
        mask = dists_sq <= search_radius * search_radius
        angles = np.arctan2(dy, dx)
        target_angle = robot_yaw + math.radians(bearing_deg)
        angle_diffs = np.abs(
            np.arctan2(np.sin(angles - target_angle), np.cos(angles - target_angle))
        )
        mask &= angle_diffs <= math.radians(45)
    else:
        dx = world_xs - robot_xy[0]
        dy = world_ys - robot_xy[1]
        dists_sq = dx * dx + dy * dy
        mask = dists_sq <= search_radius * search_radius

    if not np.any(mask):
        return None

    masked_xs = world_xs[mask]
    masked_ys = world_ys[mask]

    if target_xy is not None:
        ref_x, ref_y = target_xy[0], target_xy[1]
    else:
        ref_x, ref_y = robot_xy[0], robot_xy[1]

    dx_ref = masked_xs - ref_x
    dy_ref = masked_ys - ref_y
    dists_sq_ref = dx_ref * dx_ref + dy_ref * dy_ref
    nearest_idx = int(np.argmin(dists_sq_ref))
    p_nearest = np.array([masked_xs[nearest_idx], masked_ys[nearest_idx]])

    dx_robot = p_nearest[0] - robot_xy[0]
    dy_robot = p_nearest[1] - robot_xy[1]
    d = float(math.sqrt(dx_robot * dx_robot + dy_robot * dy_robot))

    dx_masked = masked_xs - robot_xy[0]
    dy_masked = masked_ys - robot_xy[1]
    dists_sq_masked = dx_masked * dx_masked + dy_masked * dy_masked

    # Compute edge normal via local PCA on K nearest occupied cells
    if np.sum(mask) <= 1:
        diff = robot_xy - p_nearest
        norm = np.linalg.norm(diff)
        normal = diff / norm if norm > 1e-6 else np.array([1.0, 0.0])
        return EdgeResult(point=p_nearest, normal=normal, distance=d)

    n_masked = int(np.sum(mask))
    k = min(k_neighbours, n_masked)
    top_k_indices = np.argpartition(dists_sq_masked, min(k, n_masked - 1))[:k]

    pts = np.column_stack(
        [
            world_xs[mask][top_k_indices],
            world_ys[mask][top_k_indices],
        ]
    )

    centroid = pts.mean(axis=0)
    centered = pts - centroid
    cov = centered.T @ centered
    eigvals, eigvecs = np.linalg.eigh(cov)

    # Normal = eigenvector with smallest eigenvalue (perpendicular to edge)
    normal = eigvecs[:, 0].copy()

    # Orient normal toward the robot (free space side)
    if np.dot(normal, robot_xy - p_nearest) < 0:
        normal = -normal

    return EdgeResult(point=p_nearest, normal=normal, distance=d)


def estimate_object_center(
    costmap: OccupancyGrid,
    seed_xy: np.ndarray,
    cluster_radius: float = 1.5,
) -> np.ndarray:
    """Estimate the centroid of occupied cells near *seed_xy* as the object center."""
    grid: NDArray[np.int8] = costmap.grid
    if grid.size == 0:
        return seed_xy.copy()

    ys, xs = np.where(grid >= OCCUPIED_THRESHOLD)
    if len(xs) == 0:
        return seed_xy.copy()

    ox = costmap.origin.position.x
    oy = costmap.origin.position.y
    res = costmap.resolution

    world_xs = ox + xs.astype(np.float64) * res
    world_ys = oy + ys.astype(np.float64) * res

    dx = world_xs - seed_xy[0]
    dy = world_ys - seed_xy[1]
    dists_sq = dx * dx + dy * dy
    mask = dists_sq <= cluster_radius * cluster_radius

    if not np.any(mask):
        return seed_xy.copy()

    return np.array([world_xs[mask].mean(), world_ys[mask].mean()])


class LapTracker:
    """Tracks cumulative angle to determine when N laps are complete."""

    def __init__(self, robot_xy: np.ndarray, object_center: np.ndarray, target_laps: float) -> None:
        self._center = object_center.copy()
        self._target_angle = abs(target_laps) * 2 * math.pi
        diff = robot_xy - object_center
        self._last_angle = math.atan2(diff[1], diff[0])
        self._accumulated = 0.0

    @property
    def accumulated_angle(self) -> float:
        return self._accumulated

    @property
    def progress(self) -> float:
        if self._target_angle == 0:
            return 1.0
        return min(abs(self._accumulated) / self._target_angle, 1.0)

    def update(self, robot_xy: np.ndarray) -> bool:
        """Update with current robot position.  Returns True when laps complete."""
        diff = robot_xy - self._center
        angle = math.atan2(diff[1], diff[0])
        delta = _angle_diff(angle, self._last_angle)
        self._accumulated += delta
        self._last_angle = angle
        return abs(self._accumulated) >= self._target_angle


class OrbitObjectSkillContainer(Module):
    """Orbit around an object using LiDAR costmap edge following."""

    odom: In[PoseStamped]
    global_costmap: In[OccupancyGrid]
    cmd_vel: Out[Twist]
    stop_movement: Out[Bool]

    _KP_DISTANCE: float = 0.5
    _KP_YAW: float = 0.15
    _MAX_ANGULAR_Z: float = 0.15
    _FACE_THRESHOLD: float = 0.524  # ~30 degrees
    _CONTROL_HZ: float = 10.0
    _SEARCH_RADIUS: float = 3.0
    _MIN_DISTANCE: float = 0.2
    _MAX_DISTANCE_FACTOR: float = 3.0

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._latest_odom: PoseStamped | None = None
        self._latest_costmap: OccupancyGrid | None = None
        self._thread: Thread | None = None
        self._should_stop = Event()

    @rpc
    def start(self) -> None:
        super().start()
        self.register_disposable(Disposable(self.odom.subscribe(self._on_odom)))
        self.register_disposable(Disposable(self.global_costmap.subscribe(self._on_costmap)))

    @rpc
    def stop(self) -> None:
        self._request_stop()
        thread = self._thread
        if thread is not None:
            thread.join(timeout=DEFAULT_THREAD_JOIN_TIMEOUT)
            self._thread = None
        super().stop()

    def _on_odom(self, msg: PoseStamped) -> None:
        self._latest_odom = msg

    def _on_costmap(self, msg: OccupancyGrid) -> None:
        self._latest_costmap = msg

    @skill
    def orbit_object(
        self,
        x: float = _UNSET,
        y: float = _UNSET,
        bearing: float = _UNSET,
        distance: float = 0.8,
        laps: float = 1.0,
        speed: float = 0.3,
        clockwise: bool = False,
    ) -> str:
        """Orbit around an object edge detected by LiDAR.

        The robot follows the obstacle's edge in the costmap, maintaining a
        fixed distance while circling it.

        Target selection (by priority):
        1. If x and y are both provided, orbit the obstacle nearest to that world coordinate.
        2. If bearing is provided, orbit the nearest obstacle in that direction.
        3. Otherwise, orbit the nearest obstacle to the robot.

        Args:
            x: Target world X coordinate in meters. Must provide both x and y together.
            y: Target world Y coordinate in meters. Must provide both x and y together.
            bearing: Direction to search for target relative to robot front, in degrees (CCW-positive). 0 = front, 90 = left, -90 = right.
            distance: Distance to maintain from the object edge in meters.
            laps: Number of laps to complete around the object.
            speed: Forward speed along the edge in m/s.
            clockwise: True for clockwise, False for counter-clockwise.
        """

        self._request_stop()
        if self._thread is not None:
            self._thread.join(timeout=DEFAULT_THREAD_JOIN_TIMEOUT)
            self._thread = None

        if self._latest_odom is None:
            return "No odometry data available. Cannot start orbit."
        if self._latest_costmap is None:
            return "No costmap data available. Cannot start orbit."

        x_set = x != _UNSET
        y_set = y != _UNSET
        if x_set != y_set:
            return "Both x and y must be provided together for coordinate targeting."
        target_xy = np.array([x, y]) if (x_set and y_set) else None
        bearing_val = bearing if bearing != _UNSET else None

        robot_xy = np.array([self._latest_odom.x, self._latest_odom.y])
        robot_yaw = self._latest_odom.yaw

        edge = extract_edge(
            self._latest_costmap,
            robot_xy,
            robot_yaw,
            self._SEARCH_RADIUS,
            target_xy=target_xy,
            bearing_deg=bearing_val,
        )
        if edge is None:
            return "No obstacle found within search radius. Move closer and try again."

        self._should_stop.clear()
        self.stop_movement.publish(Bool(data=True))
        self.start_tool("orbit_object")

        self._thread = Thread(
            target=self._orbit_loop,
            args=(distance, laps, speed, clockwise, target_xy, bearing_val),
            daemon=True,
        )
        self._thread.start()

        direction = "clockwise" if clockwise else "counter-clockwise"
        return (
            f"Started orbiting obstacle at ({edge.point[0]:.1f}, {edge.point[1]:.1f}), "
            f"distance={distance:.1f}m, {laps} lap(s), {direction}, speed={speed:.1f}m/s. "
            f"Call stop_orbit to cancel."
        )

    @skill
    def stop_orbit(self) -> str:
        """Stop the current orbit operation immediately.

        Returns:
            Confirmation message.
        """
        self._request_stop()
        self.cmd_vel.publish(Twist.zero())
        self.stop_movement.publish(Bool(data=False))
        if self._thread is not None:
            self._thread.join(timeout=DEFAULT_THREAD_JOIN_TIMEOUT)
            self._thread = None
        return "Orbit stopped."

    def _request_stop(self) -> None:
        self._should_stop.set()

    def _orbit_loop(
        self,
        distance: float,
        laps: float,
        speed: float,
        clockwise: bool,
        target_xy: np.ndarray | None,
        bearing_deg: float | None,
    ) -> None:
        period = 1.0 / self._CONTROL_HZ
        next_time = time.monotonic()

        odom = self._latest_odom
        costmap = self._latest_costmap
        if odom is None or costmap is None:
            self._finish_orbit("Missing sensor data at loop start.")
            return

        robot_xy = np.array([odom.x, odom.y])

        edge = extract_edge(
            costmap,
            robot_xy,
            odom.yaw,
            self._SEARCH_RADIUS,
            target_xy=target_xy,
            bearing_deg=bearing_deg,
        )
        if edge is None:
            self._finish_orbit("Lost obstacle before loop started.")
            return

        object_center = estimate_object_center(
            costmap,
            edge.point,
            cluster_radius=max(distance, 0.8),
        )
        lap_tracker = LapTracker(robot_xy, object_center, laps)
        lock_radius = max(distance * 2.5, 1.0)
        logger.info(
            "Orbit center estimated",
            center=f"({object_center[0]:.2f}, {object_center[1]:.2f})",
            edge=f"({edge.point[0]:.2f}, {edge.point[1]:.2f})",
            lock_radius=f"{lock_radius:.2f}",
        )

        while not self._should_stop.is_set():
            next_time += period

            odom = self._latest_odom
            costmap = self._latest_costmap
            if odom is None or costmap is None:
                now = time.monotonic()
                sleep_dur = next_time - now
                if sleep_dur > 0:
                    time.sleep(sleep_dur)
                continue

            robot_xy = np.array([odom.x, odom.y])
            robot_yaw = odom.yaw

            edge = extract_edge(
                costmap,
                robot_xy,
                robot_yaw,
                lock_radius,
                target_xy=object_center,
                bearing_deg=None,
            )

            if edge is None:
                self.cmd_vel.publish(Twist.zero())
                self._finish_orbit("Obstacle lost during orbit.")
                return

            d = edge.distance

            if d < self._MIN_DISTANCE:
                self.cmd_vel.publish(Twist.zero())
                self._finish_orbit("Too close to obstacle — safety stop.")
                return
            if d > self._MAX_DISTANCE_FACTOR * distance:
                self.cmd_vel.publish(Twist.zero())
                self._finish_orbit("Too far from obstacle — edge lost.")
                return

            # 面向物品中心（稳定），距离基于边缘（精确）
            theta = math.atan2(
                object_center[1] - robot_xy[1],
                object_center[0] - robot_xy[0],
            )
            face_err_rad = _angle_diff(theta, robot_yaw)
            angular_z = self._KP_YAW * face_err_rad
            angular_z = max(-self._MAX_ANGULAR_Z, min(self._MAX_ANGULAR_Z, angular_z))

            if abs(face_err_rad) < self._FACE_THRESHOLD:
                vx_body = max(-0.3, min(0.3, self._KP_DISTANCE * (d - distance)))
                vy_body = speed * (1 if not clockwise else -1)
            else:
                vx_body = 0.0
                vy_body = 0.0

            twist = Twist(
                linear=Vector3(vx_body, vy_body, 0.0),
                angular=Vector3(0.0, 0.0, angular_z),
            )
            self.cmd_vel.publish(twist)

            if lap_tracker.update(robot_xy):
                self.cmd_vel.publish(Twist.zero())
                pct = lap_tracker.progress * 100
                self._finish_orbit(f"Completed {laps} lap(s) ({pct:.0f}%).")
                return

            now = time.monotonic()
            sleep_dur = next_time - now
            if sleep_dur > 0:
                time.sleep(sleep_dur)

        self.cmd_vel.publish(Twist.zero())
        self._finish_orbit("Orbit cancelled by user.")

    def _finish_orbit(self, reason: str) -> None:
        self.stop_movement.publish(Bool(data=False))
        self.tool_update("orbit_object", reason)
        self.stop_tool("orbit_object")
        logger.info("Orbit finished", reason=reason)
