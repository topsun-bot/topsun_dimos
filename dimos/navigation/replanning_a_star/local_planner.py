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

import os
from threading import Event, RLock, Thread
import time
import traceback
from typing import Literal, TypeAlias

import numpy as np
from reactivex import Subject

from dimos.core.global_config import GlobalConfig
from dimos.core.resource import Resource
from dimos.msgs.geometry_msgs.PoseStamped import PoseStamped
from dimos.msgs.geometry_msgs.Twist import Twist
from dimos.msgs.nav_msgs.OccupancyGrid import OccupancyGrid
from dimos.msgs.nav_msgs.Path import Path
from dimos.navigation.base import NavigationState
from dimos.navigation.replanning_a_star.controllers import Controller, PController
from dimos.navigation.replanning_a_star.navigation_map import NavigationMap
from dimos.navigation.replanning_a_star.path_clearance import PathClearance
from dimos.navigation.replanning_a_star.path_distancer import PathDistancer
from dimos.navigation.stairs.contracts import StairCorridor, StairPhase
from dimos.robot.unitree.go2.stair_locomotion.locomotion_policy import (
    Go2SportConnection,
    StairLocomotionPolicy,
)
from dimos.utils.logging_config import setup_logger
from dimos.utils.trigonometry import angle_diff

PlannerState: TypeAlias = Literal[
    "idle", "initial_rotation", "path_following", "final_rotation", "arrived"
]
StopMessage: TypeAlias = Literal["arrived", "obstacle_found", "error"]

logger = setup_logger()


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
    _stair_policy: StairLocomotionPolicy | None = None
    _use_stair_locomotion: bool = False

    _speed: float = 0.55
    _control_frequency: float = 10
    _orientation_tolerance: float = 0.35
    _navigation_costmap_interval: float = 1.0
    _navigation_costmap_last: float = 0.0

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

        self._controller = PController(
            self._global_config,
            speed,
            self._control_frequency,
        )

    def start(self) -> None:
        pass

    def stop(self) -> None:
        self.stop_planning()

    def handle_odom(self, msg: PoseStamped) -> None:
        with self._lock:
            self._current_odom = msg

    def set_following_stair(self, on_stair: bool) -> None:
        self._controller.set_on_stair(on_stair)

    def set_sport_connection(self, connection: Go2SportConnection | None) -> None:
        if connection is None:
            self._stair_policy = None
        else:
            self._stair_policy = StairLocomotionPolicy(
                connection,
                global_config=self._global_config,
            )

    def is_stair_climbing(self) -> bool:
        with self._lock:
            policy = self._stair_policy
            if policy is None or not self._use_stair_locomotion:
                return False
            return policy.phase in (
                StairPhase.APPROACH,
                StairPhase.ALIGN,
                StairPhase.ON_STAIR,
            )

    def start_planning(
        self,
        path: Path,
        *,
        stair_corridor: StairCorridor | None = None,
    ) -> None:
        use_stair = (
            stair_corridor is not None
            and self._stair_policy is not None
            and self._global_config.stair_navigation
        )

        with self._lock:
            climbing = self.is_stair_climbing()
            continuing_stair = (
                use_stair
                and self._stair_policy is not None
                and (
                    climbing
                    or (
                        self._use_stair_locomotion
                        and self._thread is not None
                        and not self._stop_planning_event.is_set()
                    )
                )
            )

        if continuing_stair:
            assert self._stair_policy is not None
            assert stair_corridor is not None
            with self._lock:
                self._path = path
                self._path_clearance = PathClearance(self._global_config, self._path)
                self._path_distancer = PathDistancer(self._path)
            self._stair_policy.update_path(path)
            logger.info("Stair path updated without resetting climb state")
            return

        self.stop_planning()

        self._stop_planning_event = Event()

        with self._lock:
            self._path = path
            self._path_clearance = PathClearance(self._global_config, self._path)
            self._path_distancer = PathDistancer(self._path)
            self._pose_index = 0
            self._use_stair_locomotion = use_stair
            if self._use_stair_locomotion:
                assert self._stair_policy is not None
                assert stair_corridor is not None
                self._stair_policy.start(stair_corridor, path)
                self._change_state("path_following")
            self._thread = Thread(target=self._thread_entrypoint, daemon=True)
            self._thread.start()

    def stop_planning(self) -> None:
        self.cmd_vel.on_next(Twist())
        self._stop_planning_event.set()

        with self._lock:
            self._thread = None
            if self._stair_policy is not None:
                self._stair_policy.reset()
            self._use_stair_locomotion = False

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
        self._state = new_state
        self._state_unique_id += 1
        logger.info("changed state", state=new_state)

    def _loop(self) -> None:
        stop_event = self._stop_planning_event

        with self._lock:
            path = self._path
            path_clearance = self._path_clearance
            current_odom = self._current_odom
            use_stair = self._use_stair_locomotion
            stair_policy = self._stair_policy

        if path is None or path_clearance is None:
            raise RuntimeError("No path set for local planner.")

        if use_stair and stair_policy is not None:
            self._stair_locomotion_loop(stop_event, path, path_clearance, stair_policy)
            return

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

            if path_clearance.is_obstacle_ahead():
                logger.info("Obstacle detected ahead, stopping local planner.")
                self.stopped_navigating.on_next("obstacle_found")
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
                self.cmd_vel.on_next(cmd_vel)

            elapsed = time.perf_counter() - start_time
            sleep_time = max(0.0, (1.0 / self._control_frequency) - elapsed)
            stop_event.wait(sleep_time)

        if stop_event.is_set():
            logger.info("Local planner loop exited due to stop event.")

    def _stair_locomotion_loop(
        self,
        stop_event: Event,
        path: Path,
        path_clearance: PathClearance,
        stair_policy: StairLocomotionPolicy,
    ) -> None:
        """Follow a stair centerline using sport gait settings and limited cmd_vel."""
        while not stop_event.is_set():
            start_time = time.perf_counter()

            self._send_navigation_costmap(path, path_clearance)

            with self._lock:
                current_odom = self._current_odom

            if current_odom is None:
                stop_event.wait(1.0 / self._control_frequency)
                continue

            cmd_vel = stair_policy.step(current_odom)
            self.cmd_vel.on_next(cmd_vel)

            if stair_policy.finished:
                if stair_policy.aborted:
                    logger.warning("Stair traversal aborted (tipped or safety stop).")
                    self.stopped_navigating.on_next("error")
                else:
                    logger.info("Stair traversal complete.")
                    self.stopped_navigating.on_next("arrived")
                break

            elapsed = time.perf_counter() - start_time
            sleep_time = max(0.0, (1.0 / self._control_frequency) - elapsed)
            stop_event.wait(sleep_time)

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

    def get_distance_to_path(self) -> float | None:
        with self._lock:
            path_distancer = self._path_distancer
            current_odom = self._current_odom
            use_stair = self._use_stair_locomotion

        if use_stair or path_distancer is None or current_odom is None:
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

    def _reset_state(self) -> None:
        with self._lock:
            self._change_state("idle")
            self._path = None
            self._path_clearance = None
            self._path_distancer = None
            self._pose_index = 0
            self._controller.reset_errors()

    def _send_navigation_costmap(self, path: Path, path_clearance: PathClearance) -> None:
        if "DEBUG_NAVIGATION" not in os.environ:
            return

        now = time.time()
        if now - self._navigation_costmap_last < self._navigation_costmap_interval:
            return

        self._navigation_costmap_last = now

        self.navigation_costmap.on_next(self._navigation_map.gradient_costmap)
