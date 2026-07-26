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

from collections.abc import Callable
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
from dimos.navigation.diagnostics.schema import PlanContext
from dimos.navigation.diagnostics.sink import TraceSink, isolate_trace_failure
from dimos.navigation.replanning_a_star.controllers import Controller, PController
from dimos.navigation.replanning_a_star.navigation_map import NavigationMap
from dimos.navigation.replanning_a_star.path_clearance import PathClearance
from dimos.navigation.replanning_a_star.path_distancer import PathDistancer
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
    _trace: TraceSink | None
    _plan_context: Callable[[], PlanContext | None] | None
    _trace_last_control_ns: int
    _trace_capture_cycle: bool
    _trace_control_details: dict[str, object] | None
    _trace_obstacle_clearance: PathClearance | None
    _trace_last_costmap_blob_ns: int

    _speed: float = 0.55
    _control_frequency: float = 10
    _orientation_tolerance: float = 0.35
    _navigation_costmap_interval: float = 1.0
    _navigation_costmap_last: float = 0.0

    def __init__(
        self,
        global_config: GlobalConfig,
        navigation_map: NavigationMap,
        goal_tolerance: float,
        *,
        trace_sink: TraceSink | None = None,
        plan_context: Callable[[], PlanContext | None] | None = None,
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
        self._trace = trace_sink
        self._plan_context = plan_context
        self._trace_last_control_ns = 0
        self._trace_capture_cycle = False
        self._trace_control_details = None
        self._trace_obstacle_clearance = None
        self._trace_last_costmap_blob_ns = 0

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

    def start_planning(self, path: Path) -> None:
        self.stop_planning()

        self._stop_planning_event = Event()

        with self._lock:
            self._path = path
            self._path_clearance = PathClearance(self._global_config, self._path)
            self._path_distancer = PathDistancer(self._path)
            self._pose_index = 0
            self._thread = Thread(target=self._thread_entrypoint, daemon=True)
            self._thread.start()

    def stop_planning(self) -> None:
        self.cmd_vel.on_next(Twist())
        self._trace_stop_command("stop_planning")
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
            self._trace_stop_command("local_planner_thread_finally")
            self._trace_obstacle_after_stop()

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
            self._trace_capture_cycle = self._trace_control_due()
            if self._trace_capture_cycle:
                self._trace_control_details = None

            with self._lock:
                path_clearance.update_costmap(self._navigation_map.binary_costmap)
                path_clearance.update_pose_index(self._pose_index)

            self._send_navigation_costmap(path, path_clearance)

            obstacle_ahead = path_clearance.is_obstacle_ahead()
            if obstacle_ahead:
                logger.info("Obstacle detected ahead, stopping local planner.")
                self.stopped_navigating.on_next("obstacle_found")
                trace = self._trace
                if trace is not None and trace.accepts("summary"):
                    self._trace_obstacle_clearance = path_clearance
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

            if self._trace_capture_cycle:
                costmap = path_clearance.current_costmap
                details = self._trace_control_details or {}
                details.update(
                    {
                        "path_clearance_obstacle_ahead": obstacle_ahead,
                        "path_clearance_costmap_source_ts": (
                            float(costmap.ts) if costmap is not None else None
                        ),
                    }
                )
                self._trace_control_details = details

            if cmd_vel is not None:
                generated_wall_ts = time.time() if self._trace_capture_cycle else None
                generated_monotonic_ns = time.monotonic_ns() if self._trace_capture_cycle else None
                self.cmd_vel.on_next(cmd_vel)
                if self._trace_capture_cycle:
                    business_elapsed_sec = time.perf_counter() - start_time
                    self._trace_published_control(
                        cmd_vel,
                        generated_wall_ts,
                        generated_monotonic_ns,
                        business_elapsed_sec,
                    )

            elapsed = time.perf_counter() - start_time
            sleep_time = max(0.0, (1.0 / self._control_frequency) - elapsed)
            stop_event.wait(sleep_time)

        if stop_event.is_set():
            logger.info("Local planner loop exited due to stop event.")

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

        cmd_vel = self._controller.rotate(yaw_error)
        if self._trace_capture_cycle:
            self._trace_control_details = {
                "control_mode": "initial_rotation",
                "yaw_error": float(yaw_error),
                "target_yaw": float(first_yaw),
                "robot_yaw": float(robot_yaw),
            }
        return cmd_vel

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

        cmd_vel = self._controller.advance(lookahead_point, current_odom)
        if self._trace_capture_cycle:
            self._trace_control_details = {
                "control_mode": "path_following",
                "closest_path_index": closest_index,
                "lookahead_x": float(lookahead_point[0]),
                "lookahead_y": float(lookahead_point[1]),
            }
        return cmd_vel

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

        cmd_vel = self._controller.rotate(yaw_error)
        if self._trace_capture_cycle:
            self._trace_control_details = {
                "control_mode": "final_rotation",
                "yaw_error": float(yaw_error),
                "target_yaw": float(goal_yaw),
                "robot_yaw": float(robot_yaw),
            }
        return cmd_vel

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

    def _trace_control_due(self) -> bool:
        trace = self._trace
        if trace is None or not trace.accepts("summary"):
            return False
        now_ns = time.monotonic_ns()
        if (
            trace.effective_level == "summary"
            and now_ns - self._trace_last_control_ns < 1_000_000_000
        ):
            return False
        self._trace_last_control_ns = now_ns
        return True

    def _trace_published_control(
        self,
        cmd_vel: Twist,
        generated_wall_ts: float | None,
        generated_monotonic_ns: int | None,
        business_elapsed_sec: float,
    ) -> None:
        trace = self._trace
        if trace is None or not trace.enabled:
            return
        try:
            with self._lock:
                state = self._state
                pose_index = self._pose_index
                current_odom = self._current_odom
            fields: dict[str, object] = {
                "state": state,
                "pose_index": pose_index,
                "command_generated_ts": generated_wall_ts,
                "command_generated_monotonic_ns": generated_monotonic_ns,
                "publish_completed": True,
                "business_cycle_elapsed_sec": business_elapsed_sec,
                "twist": _twist_fields(cmd_vel),
            }
            if current_odom is not None:
                fields["odom_source_ts"] = float(current_odom.ts)
                fields["current_pose"] = _pose_trace_fields(current_odom)
            if self._trace_control_details is not None:
                fields.update(self._trace_control_details)
            context = self._plan_context() if self._plan_context is not None else None
            if context is not None:
                fields.update(_plan_trace_fields(context))
            trace.record("planner_control_published", fields, estimated_bytes=1280)
        except Exception as exc:
            isolate_trace_failure(trace, exc)

    def _trace_stop_command(self, reason: str) -> None:
        trace = self._trace
        if trace is None or not trace.accepts("summary"):
            return
        try:
            fields: dict[str, object] = {
                "reason": reason,
                "publish_completed": True,
                "twist": _twist_fields(Twist()),
            }
            context = self._plan_context() if self._plan_context is not None else None
            if context is not None:
                fields.update(_plan_trace_fields(context))
            trace.record("planner_stop_command_published", fields, estimated_bytes=640)
        except Exception as exc:
            isolate_trace_failure(trace, exc)

    def _trace_obstacle_after_stop(self) -> None:
        clearance = self._trace_obstacle_clearance
        self._trace_obstacle_clearance = None
        trace = self._trace
        if clearance is None or trace is None or not trace.accepts("summary"):
            return
        try:
            diagnostic = clearance.diagnostic()
            context = self._plan_context() if self._plan_context is not None else None
            observed_wall_ts = time.time()
            costmap_age_sec: float | None = None
            if (
                diagnostic.costmap_source_ts is not None
                and diagnostic.costmap_source_ts > 1_000_000_000
            ):
                costmap_age_sec = max(
                    0.0,
                    observed_wall_ts - diagnostic.costmap_source_ts,
                )
            fields: dict[str, object] = {
                "decision": {
                    "obstacle_ahead": diagnostic.obstacle_ahead,
                    "reason": diagnostic.reason,
                    "diagnostic_observed_wall_ts": observed_wall_ts,
                    "costmap_source_ts": diagnostic.costmap_source_ts,
                    "costmap_age_sec": costmap_age_sec,
                    "costmap_age_clock_domain": (
                        "host_wall_epoch_assumed" if costmap_age_sec is not None else "UNKNOWN"
                    ),
                    "costmap_id": "UNKNOWN",
                    "costmap_id_association": "not_carried_in_OccupancyGrid",
                    "path_lookup_distance_m": diagnostic.path_lookup_distance_m,
                    "pose_index": diagnostic.pose_index,
                    "mask_reused_for_decision": diagnostic.mask_reused_for_decision,
                    "mask_source_pose_index": diagnostic.mask_source_pose_index,
                    "path_start_index": diagnostic.path_start_index,
                    "path_end_index": diagnostic.path_end_index,
                    "effective_lookahead_m": diagnostic.effective_lookahead_m,
                    "mask_cell_count": diagnostic.mask_cell_count,
                    "free_cell_count": diagnostic.free_cell_count,
                    "unknown_cell_count": diagnostic.unknown_cell_count,
                    "occupied_hit_count": diagnostic.occupied_hit_count,
                    "first_occupied_cells": diagnostic.first_occupied_cells,
                    "nearest_occupied_path_progress_m": (
                        diagnostic.nearest_occupied_path_progress_m
                    ),
                    "hits_truncated": diagnostic.hits_truncated,
                },
                "zero_command_published_before_diagnostics": True,
            }
            if context is not None:
                fields.update(_plan_trace_fields(context))
            trace.record("path_clearance_obstacle", fields, estimated_bytes=2048)

            costmap = clearance.current_costmap
            if costmap is None or not trace.accepts("full"):
                return
            now_ns = time.monotonic_ns()
            min_interval_ns = int(
                max(0.0, self._global_config.navigation_trace_costmap_min_interval_sec)
                * 1_000_000_000
            )
            if now_ns - self._trace_last_costmap_blob_ns < min_interval_ns:
                trace.record(
                    "costmap_snapshot_skipped",
                    {
                        **(_plan_trace_fields(context) if context is not None else {}),
                        "reason": "minimum_interval",
                        "minimum_interval_ns": min_interval_ns,
                        "snapshot_kind": "binary_path_clearance",
                    },
                    estimated_bytes=640,
                )
                return
            accepted = trace.record_blob(
                "costmap",
                costmap.grid,
                {
                    **(_plan_trace_fields(context) if context is not None else {}),
                    "snapshot_reason": "path_clearance_obstacle",
                    "snapshot_kind": "binary_costmap",
                    "source_ts": float(costmap.ts),
                    "frame_id": costmap.frame_id,
                    "resolution": float(costmap.resolution),
                },
                stem="path-clearance-binary-costmap",
            )
            if accepted:
                self._trace_last_costmap_blob_ns = now_ns
        except Exception as exc:
            isolate_trace_failure(trace, exc)


def _twist_fields(cmd_vel: Twist) -> dict[str, float]:
    return {
        "linear_x": float(cmd_vel.linear.x),
        "linear_y": float(cmd_vel.linear.y),
        "linear_z": float(cmd_vel.linear.z),
        "angular_x": float(cmd_vel.angular.x),
        "angular_y": float(cmd_vel.angular.y),
        "angular_z": float(cmd_vel.angular.z),
    }


def _pose_trace_fields(pose: PoseStamped) -> dict[str, float]:
    return {
        "x": float(pose.position.x),
        "y": float(pose.position.y),
        "z": float(pose.position.z),
        "yaw": float(pose.orientation.euler[2]),
    }


def _plan_trace_fields(context: PlanContext) -> dict[str, object]:
    return {
        "navigation_session_id": context.navigation_session_id,
        "session_event_seq": context.session_event_seq,
        "plan_version": context.plan_version,
        "plan_reason": context.plan_reason,
    }
