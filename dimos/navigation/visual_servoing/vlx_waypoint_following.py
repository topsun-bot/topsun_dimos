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

"""VLX-style short-horizon waypoint planning for language-guided person following.

This is a deterministic engineering baseline, not the unpublished VLX-Go model.
It preserves the public TrackVLA/VLX-Go interface: a short target history is
converted into ten receding-horizon waypoints, then a pure-pursuit controller
turns the safe path into velocity commands.  A small elastic-band deformation
and a hard clearance gate keep learned-policy integration separate from safety.
"""

from collections import deque
from dataclasses import dataclass
import math
from typing import TypeAlias, cast

import numpy as np
from numpy.typing import NDArray

FloatArray: TypeAlias = NDArray[np.float64]


@dataclass(frozen=True)
class Pose2D:
    """Planar robot pose in a stable reference frame."""

    x: float
    y: float
    yaw: float


@dataclass(frozen=True)
class TargetObservation:
    """Tracked target position in the same reference frame as the robot pose."""

    timestamp_s: float
    x: float
    y: float
    confidence: float = 1.0


@dataclass(frozen=True)
class VlxWaypointConfig:
    """Geometry and safety limits for short-horizon following."""

    history_size: int = 32
    velocity_fit_size: int = 8
    velocity_fit_window_s: float = 1.5
    horizon_steps: int = 10
    horizon_dt_s: float = 0.2
    target_prediction_limit_s: float = 0.8
    follow_distance_m: float = 1.5
    distance_tolerance_m: float = 0.12
    max_target_speed_mps: float = 1.8
    max_linear_speed_mps: float = 0.5
    max_angular_speed_rps: float = 0.8
    linear_gain: float = 0.8
    angular_gain: float = 1.5
    lookahead_m: float = 0.55
    robot_radius_m: float = 0.35
    safety_margin_m: float = 0.15
    obstacle_influence_m: float = 0.9
    obstacle_weight: float = 0.16
    smoothness_weight: float = 0.28
    path_attraction_weight: float = 0.10
    max_path_deformation_m: float = 0.6
    elastic_band_iterations: int = 12
    collision_sample_step_m: float = 0.05
    slow_clearance_m: float = 1.0

    @property
    def hard_clearance_m(self) -> float:
        """Minimum obstacle clearance for any executable path sample."""

        return self.robot_radius_m + self.safety_margin_m


@dataclass(frozen=True)
class FollowPlan:
    """Short-horizon path and diagnostic state in the current robot frame."""

    waypoints_body: FloatArray
    target_body: FloatArray
    target_distance_m: float
    target_velocity_reference_mps: FloatArray
    minimum_path_clearance_m: float
    blocked: bool


@dataclass(frozen=True)
class FollowCommand:
    """Planar velocity command produced by the pure-pursuit controller."""

    linear_x_mps: float
    angular_z_rps: float
    forward_clearance_m: float
    safety_limited: bool


class VlxWaypointPlanner:
    """Convert target tracks into collision-checked short-horizon waypoints."""

    def __init__(self, config: VlxWaypointConfig | None = None) -> None:
        self.config = config or VlxWaypointConfig()
        self._history: deque[TargetObservation] = deque(maxlen=self.config.history_size)

    def reset(self) -> None:
        """Discard target history between follow sessions."""

        self._history.clear()

    def observe(self, observation: TargetObservation) -> None:
        """Add one reliable target position to the temporal window."""

        values = (
            observation.timestamp_s,
            observation.x,
            observation.y,
            observation.confidence,
        )
        if not all(math.isfinite(value) for value in values):
            raise ValueError("Target observation values must be finite")
        if not 0.0 <= observation.confidence <= 1.0:
            raise ValueError("Target observation confidence must be in [0, 1]")
        if self._history and observation.timestamp_s < self._history[-1].timestamp_s:
            raise ValueError("Target observations must be timestamp ordered")
        if self._history and observation.timestamp_s == self._history[-1].timestamp_s:
            self._history[-1] = observation
            return
        self._history.append(observation)

    def plan(
        self,
        robot_pose: Pose2D,
        obstacle_points_body: FloatArray | None = None,
    ) -> FollowPlan | None:
        """Plan ten body-frame waypoints and apply elastic-band safety shaping."""

        if not self._history:
            return None

        obstacles = _as_xy_points(obstacle_points_body)
        latest = self._history[-1]
        target_reference = np.array([latest.x, latest.y], dtype=np.float64)
        robot_reference = np.array([robot_pose.x, robot_pose.y], dtype=np.float64)
        target_body = reference_to_body(target_reference, robot_pose)
        target_distance = float(np.linalg.norm(target_reference - robot_reference))
        velocity = self._estimate_target_velocity()
        raw_waypoints = self._build_receding_horizon(
            robot_pose,
            target_reference,
            velocity,
        )
        safe_waypoints, minimum_clearance, blocked = self._make_path_safe(
            raw_waypoints,
            obstacles,
        )
        return FollowPlan(
            waypoints_body=safe_waypoints,
            target_body=target_body,
            target_distance_m=target_distance,
            target_velocity_reference_mps=velocity,
            minimum_path_clearance_m=minimum_clearance,
            blocked=blocked,
        )

    def _estimate_target_velocity(self) -> FloatArray:
        history = list(self._history)[-self.config.velocity_fit_size :]
        if len(history) < 2:
            return np.zeros(2, dtype=np.float64)

        newest_time = history[-1].timestamp_s
        history = [
            item
            for item in history
            if newest_time - item.timestamp_s <= self.config.velocity_fit_window_s
        ]
        if len(history) < 2:
            return np.zeros(2, dtype=np.float64)

        times = np.array([item.timestamp_s for item in history], dtype=np.float64)
        positions = np.array([[item.x, item.y] for item in history], dtype=np.float64)
        weights = np.array([max(item.confidence, 0.05) for item in history], dtype=np.float64)
        weighted_time = float(np.average(times, weights=weights))
        weighted_position = np.average(positions, axis=0, weights=weights)
        centered_time = times - weighted_time
        denominator = float(np.sum(weights * centered_time**2))
        if denominator < 1e-6:
            return np.zeros(2, dtype=np.float64)

        velocity = np.sum(
            weights[:, None] * centered_time[:, None] * (positions - weighted_position),
            axis=0,
        ) / denominator
        speed = float(np.linalg.norm(velocity))
        if speed > self.config.max_target_speed_mps:
            velocity *= self.config.max_target_speed_mps / speed
        return cast("FloatArray", velocity.astype(np.float64, copy=False))

    def _build_receding_horizon(
        self,
        robot_pose: Pose2D,
        target_reference: FloatArray,
        target_velocity: FloatArray,
    ) -> FloatArray:
        robot_reference = np.array([robot_pose.x, robot_pose.y], dtype=np.float64)
        waypoints: list[FloatArray] = []

        for step in range(1, self.config.horizon_steps + 1):
            horizon_time = step * self.config.horizon_dt_s
            prediction_time = min(horizon_time, self.config.target_prediction_limit_s)
            predicted_target = target_reference + target_velocity * prediction_time
            robot_to_target = predicted_target - robot_reference
            target_distance = float(np.linalg.norm(robot_to_target))

            if target_distance <= (
                self.config.follow_distance_m + self.config.distance_tolerance_m
            ):
                desired_reference = robot_reference
            else:
                direction = robot_to_target / target_distance
                desired_reference = predicted_target - direction * self.config.follow_distance_m

            waypoint_body = reference_to_body(desired_reference, robot_pose)
            reachable_distance = self.config.max_linear_speed_mps * horizon_time
            waypoint_distance = float(np.linalg.norm(waypoint_body))
            if waypoint_distance > reachable_distance:
                waypoint_body *= reachable_distance / waypoint_distance
            waypoints.append(waypoint_body)

        return np.asarray(waypoints, dtype=np.float64)

    def _make_path_safe(
        self,
        raw_waypoints: FloatArray,
        obstacles: FloatArray,
    ) -> tuple[FloatArray, float, bool]:
        if len(obstacles) == 0:
            return raw_waypoints, math.inf, False

        deformed = self._elastic_band(raw_waypoints, obstacles)
        safe = deformed.copy()
        start = np.zeros(2, dtype=np.float64)
        minimum_clearance = math.inf
        blocked = False

        for index, endpoint in enumerate(deformed):
            segment_clearance = _segment_minimum_clearance(
                start,
                endpoint,
                obstacles,
                self.config.collision_sample_step_m,
            )
            minimum_clearance = min(minimum_clearance, segment_clearance)
            if segment_clearance < self.config.hard_clearance_m:
                safe_point = start.copy()
                safe[index:] = safe_point
                blocked = True
                break
            start = endpoint

        return safe, minimum_clearance, blocked

    def _elastic_band(self, raw_waypoints: FloatArray, obstacles: FloatArray) -> FloatArray:
        deformed = raw_waypoints.copy()
        origin = np.zeros(2, dtype=np.float64)

        for _ in range(self.config.elastic_band_iterations):
            previous_pass = deformed.copy()
            for index, current in enumerate(previous_pass):
                previous_point = origin if index == 0 else previous_pass[index - 1]
                next_point = (
                    raw_waypoints[index]
                    if index == len(previous_pass) - 1
                    else previous_pass[index + 1]
                )
                smooth_force = previous_point + next_point - 2.0 * current
                attraction_force = raw_waypoints[index] - current

                deltas = current[None, :] - obstacles
                distances = np.linalg.norm(deltas, axis=1)
                nearby = (distances > 1e-6) & (
                    distances < self.config.obstacle_influence_m
                )
                obstacle_force = np.zeros(2, dtype=np.float64)
                if np.any(nearby):
                    nearby_distances = distances[nearby]
                    strength = (
                        self.config.obstacle_influence_m - nearby_distances
                    ) / self.config.obstacle_influence_m
                    obstacle_force = np.sum(
                        strength[:, None]
                        * deltas[nearby]
                        / nearby_distances[:, None],
                        axis=0,
                    )
                    obstacle_norm = float(np.linalg.norm(obstacle_force))
                    if obstacle_norm > 1.0:
                        obstacle_force /= obstacle_norm

                candidate = (
                    current
                    + self.config.smoothness_weight * smooth_force
                    + self.config.path_attraction_weight * attraction_force
                    + self.config.obstacle_weight * obstacle_force
                )
                deformation = candidate - raw_waypoints[index]
                deformation_norm = float(np.linalg.norm(deformation))
                if deformation_norm > self.config.max_path_deformation_m:
                    candidate = raw_waypoints[index] + (
                        deformation * self.config.max_path_deformation_m / deformation_norm
                    )
                deformed[index] = candidate

        return deformed


class VlxPurePursuitController:
    """Turn a safe short-horizon path into bounded robot velocity commands."""

    def __init__(self, config: VlxWaypointConfig | None = None) -> None:
        self.config = config or VlxWaypointConfig()

    def compute(
        self,
        plan: FollowPlan,
        obstacle_points_body: FloatArray | None = None,
    ) -> FollowCommand:
        """Compute a forward-only command with a final independent safety gate."""

        obstacles = _as_xy_points(obstacle_points_body)
        waypoint = self._lookahead_waypoint(plan.waypoints_body)
        waypoint_distance = float(np.linalg.norm(waypoint))

        if waypoint_distance < 1e-6:
            heading_error = math.atan2(plan.target_body[1], plan.target_body[0])
        else:
            heading_error = math.atan2(waypoint[1], waypoint[0])

        angular_z = float(
            np.clip(
                self.config.angular_gain * heading_error,
                -self.config.max_angular_speed_rps,
                self.config.max_angular_speed_rps,
            )
        )
        distance_error = plan.target_distance_m - self.config.follow_distance_m
        if distance_error <= self.config.distance_tolerance_m or waypoint_distance < 1e-6:
            linear_x = 0.0
        else:
            turn_factor = max(0.0, math.cos(heading_error))
            linear_x = min(
                self.config.max_linear_speed_mps,
                self.config.linear_gain * distance_error,
            ) * turn_factor

        forward_clearance = _forward_clearance(
            obstacles,
            heading_error,
            self.config.hard_clearance_m,
            self.config.slow_clearance_m,
        )
        safety_limited = False
        if forward_clearance <= self.config.hard_clearance_m:
            linear_x = 0.0
            safety_limited = True
        elif forward_clearance < self.config.slow_clearance_m:
            scale = (forward_clearance - self.config.hard_clearance_m) / (
                self.config.slow_clearance_m - self.config.hard_clearance_m
            )
            linear_x *= max(0.0, min(1.0, scale))
            safety_limited = True

        return FollowCommand(
            linear_x_mps=float(linear_x),
            angular_z_rps=angular_z,
            forward_clearance_m=forward_clearance,
            safety_limited=safety_limited,
        )

    def _lookahead_waypoint(self, waypoints: FloatArray) -> FloatArray:
        for waypoint in waypoints:
            if float(np.linalg.norm(waypoint)) >= self.config.lookahead_m:
                return cast("FloatArray", waypoint)
        return cast("FloatArray", waypoints[-1])


def reference_to_body(point_reference: FloatArray, robot_pose: Pose2D) -> FloatArray:
    """Transform one XY point from a stable reference frame into base_link XY."""

    dx = float(point_reference[0]) - robot_pose.x
    dy = float(point_reference[1]) - robot_pose.y
    cosine = math.cos(robot_pose.yaw)
    sine = math.sin(robot_pose.yaw)
    return np.array(
        [cosine * dx + sine * dy, -sine * dx + cosine * dy],
        dtype=np.float64,
    )


def reference_points_to_body(points_reference: FloatArray, robot_pose: Pose2D) -> FloatArray:
    """Vectorized reference-frame to base_link transform for obstacle points."""

    points = _as_xy_points(points_reference)
    if len(points) == 0:
        return points
    translated = points - np.array([robot_pose.x, robot_pose.y], dtype=np.float64)
    cosine = math.cos(robot_pose.yaw)
    sine = math.sin(robot_pose.yaw)
    rotation = np.array([[cosine, sine], [-sine, cosine]], dtype=np.float64)
    return translated @ rotation.T


def _as_xy_points(points: FloatArray | None) -> FloatArray:
    if points is None:
        return np.zeros((0, 2), dtype=np.float64)
    array = np.asarray(points, dtype=np.float64)
    if array.size == 0:
        return np.zeros((0, 2), dtype=np.float64)
    if array.ndim != 2 or array.shape[1] != 2:
        raise ValueError(f"Expected obstacle points shaped (N, 2), got {array.shape}")
    finite = np.all(np.isfinite(array), axis=1)
    return array[finite]


def _segment_minimum_clearance(
    start: FloatArray,
    end: FloatArray,
    obstacles: FloatArray,
    sample_step_m: float,
) -> float:
    segment_length = float(np.linalg.norm(end - start))
    sample_count = max(2, math.ceil(segment_length / sample_step_m) + 1)
    samples = np.linspace(start, end, sample_count)
    distances = np.linalg.norm(samples[:, None, :] - obstacles[None, :, :], axis=2)
    return float(np.min(distances))


def _forward_clearance(
    obstacles: FloatArray,
    heading_error: float,
    corridor_half_width_m: float,
    maximum_distance_m: float,
) -> float:
    if len(obstacles) == 0:
        return math.inf
    cosine = math.cos(heading_error)
    sine = math.sin(heading_error)
    forward = cosine * obstacles[:, 0] + sine * obstacles[:, 1]
    lateral = -sine * obstacles[:, 0] + cosine * obstacles[:, 1]
    in_corridor = (
        (forward > 0.0)
        & (forward <= maximum_distance_m)
        & (np.abs(lateral) <= corridor_half_width_m)
    )
    if not np.any(in_corridor):
        return math.inf
    return float(np.min(forward[in_corridor]))
