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

import math
import os
from threading import Event, RLock, Thread
import time
import traceback
from typing import Literal, TypeAlias

import numpy as np
from reactivex import Subject

from dimos.core.global_config import GlobalConfig
from dimos.core.resource import Resource
from dimos.experimental.navigation_obstacle_mvp.forward_obstacle_height import (
    MVP_SILLS,
    infer_forward_obstacle_height_cm,
    nearest_non_cross_sill_distance_m,
    resolve_obstacle_height_cm,
)
from dimos.experimental.navigation_obstacle_mvp.obstacle_decision import decide_obstacle_action
from dimos.msgs.geometry_msgs.PoseStamped import PoseStamped
from dimos.msgs.geometry_msgs.Twist import Twist
from dimos.msgs.geometry_msgs.Vector3 import Vector3
from dimos.msgs.nav_msgs.OccupancyGrid import OccupancyGrid
from dimos.msgs.nav_msgs.Path import Path
from dimos.navigation.base import NavigationState
from dimos.navigation.replanning_a_star.controllers import Controller, PController
from dimos.navigation.replanning_a_star.navigation_map import NavigationMap
from dimos.navigation.replanning_a_star.path_clearance import PathClearance
from dimos.navigation.replanning_a_star.path_distancer import PathDistancer
from dimos.utils.logging_config import setup_logger
from dimos.utils.trigonometry import angle_diff

PlannerState: TypeAlias = Literal[
    "idle", "initial_rotation", "path_following", "final_rotation", "arrived"
]
StopMessage: TypeAlias = Literal["arrived", "obstacle_found", "obstacle_abort", "error"]

logger = setup_logger()

# Brief linear slowdown only when very close to the low sill (cross height ≤10 cm).
_MVP_CROSS_CREEP_LINEAR_SCALE = 0.35
_MVP_CROSS_CREEP_DISTANCE_M = 0.15
# Stop and replan before wedging on medium/high sills when already this close.
_MVP_DETOUR_PREEMPT_DISTANCE_M = 0.6
_MVP_DETOUR_PREEMPT_MIN_HEIGHT_CM = 18.0


class LocalPlanner(Resource):
    cmd_vel: Subject[Twist]
    stopped_navigating: Subject[StopMessage]
    navigation_costmap: Subject[OccupancyGrid]

    _thread: Thread | None = None
    _path: Path | None = None
    _path_clearance: PathClearance | None = None
    _path_distancer: PathDistancer | None = None
    _current_odom: PoseStamped | None = None

    _pose_index: int
    _lock: RLock
    _stop_planning_event: Event
    _state: PlannerState
    _state_unique_id: int
    _global_config: GlobalConfig
    _navigation_map: NavigationMap
    _goal_tolerance: float
    _controller: Controller

    _speed: float = 0.55
    _control_frequency: float = 10
    _orientation_tolerance: float = 0.35
    _navigation_costmap_interval: float = 1.0
    _navigation_costmap_last: float = 0.0
    _mvp_cross_creep: bool = False
    _mvp_detour_hold_active: bool = False
    _nav_cmd_vel_log_last: float = 0.0

    def __init__(
        self, global_config: GlobalConfig, navigation_map: NavigationMap, goal_tolerance: float
    ) -> None:
        self.cmd_vel = Subject()
        self.stopped_navigating = Subject()
        self.navigation_costmap = Subject()

        self._pose_index = 0
        self._lock = RLock()
        self._stop_planning_event = Event()
        self._state = "idle"
        self._state_unique_id = 0
        self._global_config = global_config
        self._navigation_map = navigation_map
        self._goal_tolerance = goal_tolerance

        speed = self._speed
        if global_config.nerf_speed < 1.0:
            speed *= global_config.nerf_speed

        control_frequency = self._control_frequency
        orientation_tolerance = self._orientation_tolerance
        if global_config.simulation:
            control_frequency = 15.0
            orientation_tolerance = 0.45

        self._control_frequency = control_frequency
        self._orientation_tolerance = orientation_tolerance
        self._controller = PController(
            self._global_config,
            speed,
            control_frequency,
        )

    def start(self) -> None:
        pass

    def stop(self) -> None:
        self.stop_planning()

    def handle_odom(self, msg: PoseStamped) -> None:
        with self._lock:
            self._current_odom = msg

    def start_planning(self, path: Path) -> None:
        self.stop_planning()

        self._stop_planning_event = Event()

        with self._lock:
            self._path = path
            self._path_clearance = PathClearance(self._global_config, self._path)
            self._path_distancer = PathDistancer(self._path)
            self._pose_index = 0
            self._mvp_detour_hold_active = False
            self._thread = Thread(target=self._thread_entrypoint, daemon=True)
            self._thread.start()

    def stop_planning(self) -> None:
        self.cmd_vel.on_next(Twist())
        self._stop_planning_event.set()

        with self._lock:
            self._thread = None

        self._reset_state()

    def get_state(self) -> NavigationState:
        with self._lock:
            state = self._state

        match state:
            case "idle" | "arrived":
                return NavigationState.IDLE
            case "initial_rotation" | "path_following" | "final_rotation":
                return NavigationState.FOLLOWING_PATH
            case _:
                raise ValueError(f"Unknown planner state: {state}")

    def get_unique_state(self) -> tuple[PlannerState, int]:
        with self._lock:
            return (self._state, self._state_unique_id)

    def _thread_entrypoint(self) -> None:
        try:
            self._loop()
        except Exception as e:
            traceback.print_exc()
            logger.exception("Error in local planning", exc_info=e)
            self.stopped_navigating.on_next("error")
        finally:
            self._reset_state()
            self.cmd_vel.on_next(Twist())

    def _change_state(self, new_state: PlannerState) -> None:
        if new_state == self._state:
            return
        self._state = new_state
        self._state_unique_id += 1
        logger.info("changed state", state=new_state)

    def _loop(self) -> None:
        stop_event = self._stop_planning_event

        with self._lock:
            path = self._path
            path_clearance = self._path_clearance
            current_odom = self._current_odom

        if path is None or path_clearance is None:
            raise RuntimeError("No path set for local planner.")

        # Determine initial state: skip initial_rotation if already aligned.
        new_state: PlannerState = "initial_rotation"
        if current_odom is not None and len(path.poses) > 0:
            first_yaw = path.poses[0].orientation.euler[2]
            robot_yaw = current_odom.orientation.euler[2]
            initial_yaw_error = angle_diff(first_yaw, robot_yaw)
            self._controller.reset_yaw_error(initial_yaw_error)
            angle_in_tolerance = abs(initial_yaw_error) < self._orientation_tolerance
            if angle_in_tolerance:
                position_in_tolerance = (
                    path.poses[0].position.distance(current_odom.position) < 0.01
                )
                if position_in_tolerance:
                    new_state = "final_rotation"
                else:
                    new_state = "path_following"

        with self._lock:
            self._change_state(new_state)

        while not stop_event.is_set():
            start_time = time.perf_counter()

            with self._lock:
                path_clearance.update_costmap(self._navigation_map.binary_costmap)
                path_clearance.update_pose_index(self._pose_index)

            self._send_navigation_costmap(path, path_clearance)

            with self._lock:
                current_odom = self._current_odom

            self._update_mvp_cross_motion_limits(current_odom)

            if self._try_mvp_detour_preempt(current_odom):
                self.cmd_vel.on_next(Twist())
                elapsed = time.perf_counter() - start_time
                sleep_time = max(0.0, (1.0 / self._control_frequency) - elapsed)
                stop_event.wait(sleep_time)
                continue

            if path_clearance.is_obstacle_ahead():
                if self._handle_obstacle_ahead(current_odom):
                    break

            with self._lock:
                state: PlannerState = self._state

            if state == "initial_rotation":
                cmd_vel = self._compute_initial_rotation()
            elif state == "path_following":
                cmd_vel = self._compute_path_following()
            elif state == "final_rotation":
                cmd_vel = self._compute_final_rotation()
            elif state == "arrived":
                self.stopped_navigating.on_next("arrived")
                break
            elif state == "idle":
                cmd_vel = None

            if cmd_vel is not None:
                if self._mvp_cross_creep and state == "path_following":
                    cmd_vel = self._scale_twist_for_mvp_cross(cmd_vel)
                self._log_nav_cmd_vel(cmd_vel)
                self.cmd_vel.on_next(cmd_vel)

            elapsed = time.perf_counter() - start_time
            sleep_time = max(0.0, (1.0 / self._control_frequency) - elapsed)
            stop_event.wait(sleep_time)

        if stop_event.is_set():
            logger.info("Local planner loop exited due to stop event.")

    def _try_mvp_detour_preempt(self, current_odom: PoseStamped | None) -> bool:
        """Trigger replan before wedging on medium/high sills at close range."""
        if self._mvp_detour_hold_active:
            return True

        if not self._global_config.obstacle_crossing_mvp or current_odom is None:
            return False

        with self._lock:
            if self._state != "path_following":
                return False

        nearest = nearest_non_cross_sill_distance_m(
            current_odom.position.x,
            current_odom.position.y,
        )
        if nearest is None:
            return False

        dist_m, height_cm = nearest
        if dist_m >= _MVP_DETOUR_PREEMPT_DISTANCE_M:
            return False
        if height_cm < _MVP_DETOUR_PREEMPT_MIN_HEIGHT_CM:
            return False
        if decide_obstacle_action(obstacle_height_cm=height_cm) == "cross":
            return False

        action = decide_obstacle_action(obstacle_height_cm=height_cm)
        logger.info(
            "MVP detour preempt at non-cross sill.",
            distance_m=round(dist_m, 3),
            obstacle_height_cm=height_cm,
            action=action,
        )
        self.cmd_vel.on_next(Twist())
        self._mvp_detour_hold_active = True
        if action == "stop":
            self.stopped_navigating.on_next("obstacle_abort")
        else:
            self.stopped_navigating.on_next("obstacle_found")
        return True

    def _handle_obstacle_ahead(self, current_odom: PoseStamped | None) -> bool:
        """Return True when local planning should stop for the detected obstacle."""
        if not self._global_config.obstacle_crossing_mvp:
            logger.info("Obstacle detected ahead, stopping local planner.")
            self.stopped_navigating.on_next("obstacle_found")
            return True

        obstacle_height_cm = resolve_obstacle_height_cm(
            configured_height_cm=self._global_config.obstacle_crossing_mvp_height_cm,
            current_odom=current_odom,
        )
        action = decide_obstacle_action(obstacle_height_cm=obstacle_height_cm)
        logger.info(
            "Obstacle detected ahead, applying MVP decision.",
            obstacle_height_cm=obstacle_height_cm,
            action=action,
        )

        if action == "cross":
            with self._lock:
                in_path_following = self._state == "path_following"
            self._mvp_cross_creep = in_path_following and self._should_creep_for_mvp_cross(
                current_odom
            )
            return False

        self._mvp_cross_creep = False
        if action == "detour":
            self.cmd_vel.on_next(Twist())
            self.stopped_navigating.on_next("obstacle_found")
            return True

        self.stopped_navigating.on_next("obstacle_abort")
        return True

    def _compute_initial_rotation(self) -> Twist:
        with self._lock:
            path = self._path
            current_odom = self._current_odom

        assert path is not None
        assert current_odom is not None

        first_pose = path.poses[0]
        first_yaw = first_pose.orientation.euler[2]
        robot_yaw = current_odom.orientation.euler[2]
        yaw_error = angle_diff(first_yaw, robot_yaw)

        if abs(yaw_error) < self._orientation_tolerance:
            with self._lock:
                self._change_state("path_following")
            return self._compute_path_following()

        return self._controller.rotate(yaw_error)

    def _update_mvp_cross_motion_limits(self, current_odom: PoseStamped | None) -> None:
        """Apply brief creep only when very close to the low cross sill (≤10 cm)."""
        self._mvp_cross_creep = False
        if not self._global_config.obstacle_crossing_mvp or current_odom is None:
            return

        with self._lock:
            if self._state != "path_following":
                return

        inferred_height_cm = infer_forward_obstacle_height_cm(
            current_odom.position.x,
            current_odom.position.y,
            forward_yaw=current_odom.orientation.euler[2],
        )
        if inferred_height_cm is None:
            return

        if decide_obstacle_action(obstacle_height_cm=inferred_height_cm) != "cross":
            return

        self._mvp_cross_creep = self._should_creep_for_mvp_cross(current_odom)

    @staticmethod
    def _should_creep_for_mvp_cross(current_odom: PoseStamped | None) -> bool:
        if current_odom is None:
            return False
        low = MVP_SILLS[0]
        dist = math.hypot(
            current_odom.position.x - low.x,
            current_odom.position.y - low.y,
        )
        return dist <= _MVP_CROSS_CREEP_DISTANCE_M

    def get_distance_to_path(self) -> float | None:
        with self._lock:
            path_distancer = self._path_distancer
            current_odom = self._current_odom

        if path_distancer is None or current_odom is None:
            return None

        current_pos = np.array([current_odom.position.x, current_odom.position.y])

        return path_distancer.get_distance_to_path(current_pos)

    def _compute_path_following(self) -> Twist:
        with self._lock:
            path_distancer = self._path_distancer
            current_odom = self._current_odom

        assert path_distancer is not None
        assert current_odom is not None

        current_pos = np.array([current_odom.position.x, current_odom.position.y])

        if path_distancer.distance_to_goal(current_pos) < self._goal_tolerance:
            logger.info("Reached goal position, starting final rotation")
            with self._lock:
                self._change_state("final_rotation")
            return self._compute_final_rotation()

        closest_index = path_distancer.find_closest_point_index(current_pos)

        with self._lock:
            self._pose_index = closest_index

        lookahead_point = path_distancer.find_lookahead_point(closest_index)

        return self._controller.advance(lookahead_point, current_odom)

    def _compute_final_rotation(self) -> Twist:
        with self._lock:
            path = self._path
            current_odom = self._current_odom

        assert path is not None
        assert current_odom is not None

        goal_yaw = path.poses[-1].orientation.euler[2]
        robot_yaw = current_odom.orientation.euler[2]
        yaw_error = angle_diff(goal_yaw, robot_yaw)

        if abs(yaw_error) < self._orientation_tolerance:
            logger.info("Final rotation complete, goal reached")
            with self._lock:
                self._change_state("arrived")
            return Twist()

        return self._controller.rotate(yaw_error)

    def _log_nav_cmd_vel(self, cmd_vel: Twist) -> None:
        if cmd_vel.is_zero():
            return
        now = time.perf_counter()
        if now - self._nav_cmd_vel_log_last < 2.0:
            return
        self._nav_cmd_vel_log_last = now
        logger.info(
            "Publishing nav_cmd_vel",
            linear_x=round(cmd_vel.linear.x, 3),
            linear_y=round(cmd_vel.linear.y, 3),
            angular_z=round(cmd_vel.angular.z, 3),
        )

    @staticmethod
    def _scale_twist_for_mvp_cross(cmd_vel: Twist) -> Twist:
        return Twist(
            linear=Vector3(
                cmd_vel.linear.x * _MVP_CROSS_CREEP_LINEAR_SCALE,
                cmd_vel.linear.y * _MVP_CROSS_CREEP_LINEAR_SCALE,
                cmd_vel.linear.z,
            ),
            angular=cmd_vel.angular,
        )

    def _reset_state(self) -> None:
        with self._lock:
            self._change_state("idle")
            self._path = None
            self._path_clearance = None
            self._path_distancer = None
            self._pose_index = 0
            self._mvp_cross_creep = False
            self._mvp_detour_hold_active = False
            self._controller.reset_errors()

    def _send_navigation_costmap(self, path: Path, path_clearance: PathClearance) -> None:
        if "DEBUG_NAVIGATION" not in os.environ:
            return

        now = time.time()
        if now - self._navigation_costmap_last < self._navigation_costmap_interval:
            return

        self._navigation_costmap_last = now

        self.navigation_costmap.on_next(self._navigation_map.gradient_costmap)
