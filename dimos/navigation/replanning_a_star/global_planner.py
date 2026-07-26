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

from functools import partial
import math
from pathlib import Path as FilePath
from threading import Event, RLock, Thread, current_thread
import time
from typing import Any

from dimos_lcm.std_msgs import Bool
from reactivex import Subject
from reactivex.disposable import CompositeDisposable

from dimos.constants import DEFAULT_THREAD_JOIN_TIMEOUT
from dimos.core.global_config import GlobalConfig
from dimos.core.resource import Resource
from dimos.mapping.occupancy.path_resampling import smooth_resample_path
from dimos.msgs.geometry_msgs.PoseStamped import PoseStamped
from dimos.msgs.geometry_msgs.Twist import Twist
from dimos.msgs.geometry_msgs.Vector3 import Vector3
from dimos.msgs.nav_msgs.OccupancyGrid import CostValues, OccupancyGrid
from dimos.msgs.nav_msgs.Path import Path
from dimos.navigation.base import NavigationState
from dimos.navigation.diagnostics.schema import (
    NavigationTerminal,
    PlanContext,
    SessionTransition,
)
from dimos.navigation.diagnostics.session import NavigationSessionTracker
from dimos.navigation.diagnostics.sink import TraceSink, isolate_trace_failure
from dimos.navigation.replanning_a_star.goal_validator import find_safe_goal
from dimos.navigation.replanning_a_star.local_planner import LocalPlanner, StopMessage
from dimos.navigation.replanning_a_star.min_cost_astar import min_cost_astar
from dimos.navigation.replanning_a_star.navigation_map import NavigationMap
from dimos.navigation.replanning_a_star.position_tracker import PositionTracker
from dimos.navigation.replanning_a_star.replan_limiter import ReplanLimiter
from dimos.utils.logging_config import setup_logger
from dimos.utils.trigonometry import angle_diff

logger = setup_logger()


class GlobalPlanner(Resource):
    path: Subject[Path]
    goal_reached: Subject[Bool]

    _current_odom: PoseStamped | None = None
    _current_goal: PoseStamped | None = None
    _goal_reached: bool = False
    _thread: Thread | None = None

    _global_config: GlobalConfig
    _navigation_map: NavigationMap
    _navigation_map_near: NavigationMap
    _local_planner: LocalPlanner
    _position_tracker: PositionTracker
    _replan_limiter: ReplanLimiter
    _disposables: CompositeDisposable
    _stop_planner: Event
    _replan_event: Event
    _replan_reason: StopMessage | None
    _lock: RLock
    _safe_goal_clearance: float
    _trace: TraceSink
    _session_tracker: NavigationSessionTracker | None
    _active_plan: PlanContext | None

    _safe_goal_tolerance: float = 4.0
    _goal_tolerance: float = 0.2
    _rotation_tolerance: float = math.radians(15)
    _replan_goal_tolerance: float = 0.5
    _stuck_time_window: float = 8.0
    _stuck_threshold: float = 0.4
    _max_path_deviation: float = 0.9
    _replanning_enabled: bool = True

    def __init__(self, global_config: GlobalConfig) -> None:
        self.path = Subject()
        self.goal_reached = Subject()

        self._global_config = global_config
        self._trace = TraceSink("planner", config=global_config)
        self._session_tracker = NavigationSessionTracker() if self._trace.enabled else None
        self._active_plan = None
        self._trace_last_odom_ns = 0
        self._trace_last_astar_blob_ns = 0
        self._navigation_map = NavigationMap(self._global_config, "voronoi")
        self._navigation_map_near = NavigationMap(self._global_config, "gradient")
        self._local_planner = LocalPlanner(
            self._global_config,
            self._navigation_map,
            self._goal_tolerance,
            trace_sink=self._trace,
            plan_context=lambda: self._active_plan,
        )

        stuck_threshold = self._stuck_threshold
        if global_config.simulation:
            stuck_threshold = 1.0

        self._position_tracker = PositionTracker(self._stuck_time_window, stuck_threshold)
        self._replan_limiter = ReplanLimiter()
        self._disposables = CompositeDisposable()
        self._stop_planner = Event()
        self._replan_event = Event()
        self._replan_reason = None
        self._lock = RLock()
        self._reset_safe_goal_clearance()

    def start(self) -> None:
        self._local_planner.start()
        self._disposables.add(
            self._local_planner.stopped_navigating.subscribe(self._on_stopped_navigating)
        )
        self._stop_planner.clear()
        self._thread = Thread(target=self._thread_entrypoint, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        # 先关闭入口，再清理当前目标。蓝图各 worker 的停止顺序不固定，
        # Patrolling 等模块可能在此期间发布最后一个“当前位置”目标。
        self._stop_planner.set()
        self.cancel_goal()
        self._local_planner.stop()
        self._disposables.dispose()
        self._replan_event.set()

        if self._thread is not None and self._thread is not current_thread():
            self._thread.join(DEFAULT_THREAD_JOIN_TIMEOUT)
            if self._thread.is_alive():
                logger.error("GlobalPlanner thread did not stop in time.")
            self._thread = None
        self._trace.close()

    def handle_odom(self, msg: PoseStamped) -> None:
        with self._lock:
            self._current_odom = msg

        self._local_planner.handle_odom(msg)
        self._position_tracker.add_position(msg)
        self._trace_odom(msg)

    def handle_global_costmap(self, msg: OccupancyGrid) -> None:
        self._navigation_map.update(msg)
        self._navigation_map_near.update(msg)

    def handle_goal_request(self, goal: PoseStamped, *, entry_source: str = "goal_request") -> None:
        if self._stop_planner.is_set():
            logger.info("Ignoring goal request while GlobalPlanner is stopping")
            return
        logger.info("Got new goal", goal=str(goal))
        with self._lock:
            self._current_goal = goal
            self._goal_reached = False
        self._replan_limiter.reset()
        self._trace_begin_session(entry_source, goal)
        self._plan_path("initial_goal")

    def set_safe_goal_clearance(self, clearance: float) -> None:
        with self._lock:
            self._safe_goal_clearance = clearance

    def reset_safe_goal_clearance(self) -> None:
        self._reset_safe_goal_clearance()

    def cancel_goal(
        self,
        *,
        but_will_try_again: bool = False,
        arrived: bool = False,
        failure_reason: str | None = None,
    ) -> None:
        # return silently so we don't flood the logs.
        with self._lock:
            no_goal = self._current_goal is None
        if no_goal and self._local_planner.get_state() == NavigationState.IDLE:
            return

        logger.info("Cancelling goal.", but_will_try_again=but_will_try_again, arrived=arrived)

        with self._lock:
            self._position_tracker.reset_data()

            if not but_will_try_again:
                self._current_goal = None
                self._goal_reached = arrived
                self._replan_limiter.reset()

        self.path.on_next(Path())
        self._local_planner.stop_planning()

        if not but_will_try_again:
            self.goal_reached.on_next(Bool(arrived))
            if arrived:
                self._trace_end_session("arrived", reason="goal_tolerance_reached")
            elif failure_reason is not None:
                self._trace_end_session("failed", reason=failure_reason)
            else:
                self._trace_end_session("cancelled", reason="cancel_goal")

    def set_replanning_enabled(self, enabled: bool) -> None:
        with self._lock:
            self._replanning_enabled = enabled

    def get_state(self) -> NavigationState:
        return self._local_planner.get_state()

    def is_goal_reached(self) -> bool:
        with self._lock:
            return self._goal_reached

    @property
    def cmd_vel(self) -> Subject[Twist]:
        return self._local_planner.cmd_vel

    @property
    def navigation_costmap(self) -> Subject[OccupancyGrid]:
        return self._local_planner.navigation_costmap

    def _thread_entrypoint(self) -> None:
        """Monitor if the robot is stuck, veers off track, or stopped navigating."""

        last_id = -1
        last_stuck_check = time.perf_counter()

        while not self._stop_planner.is_set():
            # Wait for either timeout or replan signal from local planner.
            replanning_wanted = self._replan_event.wait(timeout=0.1)

            if self._stop_planner.is_set():
                break

            # Handle stop message from local planner (priority)
            if replanning_wanted:
                self._replan_event.clear()
                with self._lock:
                    reason = self._replan_reason
                    self._replan_reason = None

                if reason is not None:
                    self._handle_stop_message(reason)
                    last_stuck_check = time.perf_counter()
                    continue

            with self._lock:
                current_goal = self._current_goal
                current_odom = self._current_odom

            if not current_goal or not current_odom:
                continue

            if (
                current_goal.position.distance(current_odom.position) < self._goal_tolerance
                and abs(
                    angle_diff(current_goal.orientation.euler[2], current_odom.orientation.euler[2])
                )
                < self._rotation_tolerance
            ):
                logger.info("Close enough to goal. Accepting as arrived.")
                self.cancel_goal(arrived=True)
                continue

            # Check if robot has veered too far off the path
            deviation = self._local_planner.get_distance_to_path()
            if deviation is not None and deviation > self._max_path_deviation:
                logger.info(
                    "Robot veered off track. Replanning.",
                    deviation=round(deviation, 2),
                    threshold=self._max_path_deviation,
                )
                self._replan_path("veered_off_path")
                last_stuck_check = time.perf_counter()
                continue

            _, new_id = self._local_planner.get_unique_state()

            if new_id != last_id:
                last_id = new_id
                last_stuck_check = time.perf_counter()
                continue

            if (
                time.perf_counter() - last_stuck_check > self._stuck_time_window
                and self._position_tracker.is_stuck()
            ):
                logger.info("Robot is stuck. Replanning.")
                self._replan_path("stuck")
                last_stuck_check = time.perf_counter()

    def _on_stopped_navigating(self, stop_message: StopMessage) -> None:
        with self._lock:
            self._replan_reason = stop_message
        # Signal the monitoring thread to do the replanning. This is so we don't have two
        # threads which could be replanning at the same time.
        self._replan_event.set()

    def _handle_stop_message(self, stop_message: StopMessage) -> None:
        # Note, this runs in the monitoring thread.

        self.path.on_next(Path())

        if stop_message == "arrived":
            logger.info("Arrived at goal.")
            self.cancel_goal(arrived=True)
        elif stop_message == "obstacle_found":
            logger.info("Replanning path due to obstacle found.")
            self._replan_path("obstacle_found")
        elif stop_message == "error":
            logger.info("Failure in navigation.")
            self._replan_path("local_planner_error")
        else:
            logger.error(f"No code to handle '{stop_message}'.")
            self.cancel_goal()

    def _replan_path(self, reason: str) -> None:
        with self._lock:
            current_odom = self._current_odom
            current_goal = self._current_goal

        if self._stop_planner.is_set() or current_odom is None or current_goal is None:
            logger.info("Skipping replan because GlobalPlanner is stopping or has no active goal")
            return

        logger.info("Replanning.", attempt=self._replan_limiter.get_attempt())

        if current_goal.position.distance(current_odom.position) < self._replan_goal_tolerance:
            self.cancel_goal(arrived=True)
            return

        if not self._replanning_enabled:
            self.cancel_goal(failure_reason="replanning_disabled")
            return

        if not self._replan_limiter.can_retry(current_odom.position):
            self.cancel_goal(failure_reason="replan_attempts_exhausted")
            return

        self._replan_limiter.will_retry()

        self._plan_path(reason)

    def _plan_path(self, reason: str) -> None:
        if self._stop_planner.is_set():
            return

        self.cancel_goal(but_will_try_again=True)

        with self._lock:
            current_odom = self._current_odom
            current_goal = self._current_goal

        if self._stop_planner.is_set() or current_goal is None:
            logger.info("Skipping path planning because the goal was cancelled during shutdown")
            return

        if current_odom is None:
            logger.warning("Cannot handle goal request: missing odometry.")
            self._trace_end_session("failed", reason="missing_odometry")
            return

        self._active_plan = self._trace_next_plan(reason, current_odom, current_goal)

        safe_goal = self._find_safe_goal(current_goal.position)

        if not safe_goal:
            logger.warning(
                "No safe goal found.", x=round(current_goal.x, 3), y=round(current_goal.y, 3)
            )
            self.cancel_goal(failure_reason="no_safe_goal")
            return

        path = self._find_wide_path(safe_goal, current_odom.position)

        if self._stop_planner.is_set():
            return

        if not path:
            logger.warning(
                "No path found to the goal.", x=round(safe_goal.x, 3), y=round(safe_goal.y, 3)
            )
            self.cancel_goal(failure_reason="no_path")
            return

        resampled_path = smooth_resample_path(path, current_goal, 0.1)

        self.path.on_next(resampled_path)

        self._local_planner.start_planning(resampled_path)
        self._trace_plan_completed(path, resampled_path, safe_goal)

    def _trace_begin_session(self, entry_source: str, goal: PoseStamped) -> None:
        if not self._trace.enabled:
            return
        try:
            tracker = self._session_tracker
            if tracker is None:
                return
            for transition in tracker.begin(entry_source):
                self._record_session_transition(transition, goal=goal)
        except Exception as exc:
            isolate_trace_failure(self._trace, exc)

    def _trace_odom(self, msg: PoseStamped) -> None:
        if not self._trace.accepts("summary"):
            return
        try:
            now_ns = time.monotonic_ns()
            if (
                self._trace.effective_level == "summary"
                and now_ns - self._trace_last_odom_ns < 1_000_000_000
            ):
                return
            self._trace_last_odom_ns = now_ns
            context = self._active_plan
            fields: dict[str, object] = {
                "source_ts": float(msg.ts),
                "planner_used_monotonic_ns": now_ns,
                "pose": _pose_fields(msg),
                "frame_id": msg.frame_id,
                "ground_truth": False,
                "estimate_kind": "planner_used_odom",
            }
            if context is not None:
                fields.update(_plan_context_fields(context))
            self._trace.record("planner_odom", fields, estimated_bytes=768)
        except Exception as exc:
            isolate_trace_failure(self._trace, exc)

    def _trace_next_plan(
        self,
        reason: str,
        current_odom: PoseStamped,
        current_goal: PoseStamped,
    ) -> PlanContext | None:
        if not self._trace.enabled:
            return None
        try:
            tracker = self._session_tracker
            context = tracker.next_plan(reason) if tracker is not None else None
            if context is None:
                return None
            self._trace.record(
                "plan_started",
                {
                    "navigation_session_id": context.navigation_session_id,
                    "session_event_seq": context.session_event_seq,
                    "plan_version": context.plan_version,
                    "plan_reason": context.plan_reason,
                    "current_pose": _pose_fields(current_odom),
                    "requested_goal": _pose_fields(current_goal),
                },
                estimated_bytes=1024,
            )
            return context
        except Exception as exc:
            isolate_trace_failure(self._trace, exc)
            return None

    def _trace_astar_attempt(
        self,
        costmap: OccupancyGrid,
        robot_pos: Vector3,
        goal: Vector3,
        robot_size_multiplier: float,
        path: Path | None,
        started_ns: int | None,
    ) -> None:
        if not self._trace.enabled:
            return
        try:
            context = self._active_plan
            fields: dict[str, object] = {
                "robot_position": _vector_fields(robot_pos),
                "safe_goal": _vector_fields(goal),
                "robot_size_multiplier": robot_size_multiplier,
                "costmap": _costmap_scalar_fields(costmap),
                "path_found": bool(path and path.poses),
                "raw_pose_count": len(path.poses) if path is not None else 0,
            }
            if started_ns is not None:
                fields["duration_ns"] = max(0, time.monotonic_ns() - started_ns)
            if context is not None:
                fields.update(_plan_context_fields(context))
            self._trace.record("astar_attempt_completed", fields, estimated_bytes=1280)
            if context is None or not self._trace.accepts("full"):
                return
            now_ns = time.monotonic_ns()
            min_interval_ns = int(
                max(0.0, self._global_config.navigation_trace_costmap_min_interval_sec)
                * 1_000_000_000
            )
            if (
                self._trace_last_astar_blob_ns != 0
                and now_ns - self._trace_last_astar_blob_ns < min_interval_ns
            ):
                self._trace.record(
                    "costmap_snapshot_skipped",
                    {
                        **_plan_context_fields(context),
                        "reason": "minimum_interval",
                        "minimum_interval_ns": min_interval_ns,
                        "snapshot_kind": "astar_navigation_costmap",
                    },
                    estimated_bytes=640,
                )
                return
            accepted = self._trace.record_blob(
                "costmap",
                costmap.grid,
                {
                    **_plan_context_fields(context),
                    "snapshot_reason": context.plan_reason,
                    "snapshot_kind": "astar_navigation_costmap",
                    "costmap": _costmap_scalar_fields(costmap),
                },
                stem=(
                    f"{context.navigation_session_id}-"
                    f"plan-{context.plan_version:04d}-navigation-costmap"
                ),
            )
            if accepted:
                self._trace_last_astar_blob_ns = now_ns
        except Exception as exc:
            isolate_trace_failure(self._trace, exc)

    def _trace_plan_completed(
        self,
        raw_path: Path,
        smoothed_path: Path,
        safe_goal: Vector3,
    ) -> None:
        if not self._trace.enabled:
            return
        try:
            context = self._active_plan
            if context is None:
                return
            context_fields = _plan_context_fields(context)
            self._trace.record(
                "plan_published",
                {
                    **context_fields,
                    "safe_goal": _vector_fields(safe_goal),
                    "raw_pose_count": len(raw_path.poses),
                    "smoothed_pose_count": len(smoothed_path.poses),
                    "path_publish_completed": True,
                    "local_planner_started": True,
                },
                estimated_bytes=768,
            )
            for path_kind, planned_path in (
                ("raw", raw_path),
                ("smoothed", smoothed_path),
            ):
                relative_path = FilePath(
                    "plans",
                    (
                        f"{context.navigation_session_id}-"
                        f"plan-{context.plan_version:04d}-{path_kind}.json"
                    ),
                )
                self._trace.record_json_artifact(
                    relative_path,
                    partial(_path_artifact, context, path_kind, planned_path),
                    {
                        **context_fields,
                        "artifact_kind": f"{path_kind}_path",
                        "pose_count": len(planned_path.poses),
                    },
                    estimated_bytes=512 + len(planned_path.poses) * 192,
                    redact_payload=False,
                )
        except Exception as exc:
            isolate_trace_failure(self._trace, exc)

    def _trace_end_session(
        self,
        terminal: NavigationTerminal,
        *,
        reason: str,
    ) -> None:
        if not self._trace.enabled:
            return
        try:
            tracker = self._session_tracker
            transition = tracker.end(terminal, reason=reason) if tracker is not None else None
            if transition is not None:
                self._record_session_transition(transition)
            self._active_plan = None
        except Exception as exc:
            isolate_trace_failure(self._trace, exc)

    def _record_session_transition(
        self,
        transition: SessionTransition,
        *,
        goal: PoseStamped | None = None,
    ) -> None:
        fields: dict[str, object] = {
            "navigation_session_id": transition.context.navigation_session_id,
            "session_event_seq": transition.context.session_event_seq,
            "plan_version": transition.context.plan_version,
            "entry_source": transition.entry_source,
        }
        if transition.terminal is not None:
            fields["terminal"] = transition.terminal
        if transition.reason is not None:
            fields["reason"] = transition.reason
        if goal is not None:
            fields["requested_goal"] = _pose_fields(goal)
        self._trace.record(transition.event, fields, estimated_bytes=768)

    def _find_wide_path(self, goal: Vector3, robot_pos: Vector3) -> Path | None:
        #        sizes_to_try: list[float] = [2.2, 1.7, 1.3, 1]
        sizes_to_try: list[float] = [1.1]

        for size in sizes_to_try:
            distance = robot_pos.distance(goal)
            navigation_map = self._navigation_map if distance > 1.5 else self._navigation_map_near
            costmap = navigation_map.make_gradient_costmap(size)
            astar_started_ns = time.monotonic_ns() if self._trace.enabled else None
            path = min_cost_astar(costmap, goal, robot_pos)
            self._trace_astar_attempt(
                costmap,
                robot_pos,
                goal,
                size,
                path,
                astar_started_ns,
            )
            if path and path.poses:
                logger.info(f"Found path {size}x robot width.")
                return path

        return None

    def _find_safe_goal(self, goal: Vector3) -> Vector3 | None:
        costmap = self._navigation_map.binary_costmap

        if costmap.cell_value(goal) == CostValues.UNKNOWN:
            return goal

        safe_goal = find_safe_goal(
            costmap,
            goal,
            algorithm="bfs_contiguous",
            cost_threshold=CostValues.OCCUPIED,
            min_clearance=self._safe_goal_clearance,
            max_search_distance=self._safe_goal_tolerance,
        )

        if safe_goal is None:
            logger.warning("No safe goal found near requested target.")
            return None

        goals_distance = safe_goal.distance(goal)
        if goals_distance > 0.2:
            logger.warning(f"Travelling to goal {goals_distance}m away from requested goal.")

        logger.info("Found safe goal.", x=round(safe_goal.x, 2), y=round(safe_goal.y, 2))

        return safe_goal

    def _reset_safe_goal_clearance(self) -> None:
        with self._lock:
            self._safe_goal_clearance = self._global_config.robot_rotation_diameter / 2


def _pose_fields(pose: PoseStamped) -> dict[str, float]:
    return {
        "x": float(pose.position.x),
        "y": float(pose.position.y),
        "z": float(pose.position.z),
        "yaw": float(pose.orientation.euler[2]),
    }


def _vector_fields(vector: Vector3) -> dict[str, float]:
    return {
        "x": float(vector.x),
        "y": float(vector.y),
        "z": float(vector.z),
    }


def _plan_context_fields(context: PlanContext) -> dict[str, object]:
    return {
        "navigation_session_id": context.navigation_session_id,
        "session_event_seq": context.session_event_seq,
        "plan_version": context.plan_version,
        "plan_reason": context.plan_reason,
    }


def _costmap_scalar_fields(costmap: OccupancyGrid) -> dict[str, object]:
    return {
        "source_ts": float(costmap.ts),
        "frame_id": costmap.frame_id,
        "width": costmap.width,
        "height": costmap.height,
        "resolution": float(costmap.resolution),
        "origin": {
            "position": _vector_fields(costmap.origin.position),
            "orientation_xyzw": [
                float(costmap.origin.orientation.x),
                float(costmap.origin.orientation.y),
                float(costmap.origin.orientation.z),
                float(costmap.origin.orientation.w),
            ],
            "yaw": _quaternion_yaw(costmap.origin.orientation),
        },
    }


def _path_artifact(
    context: PlanContext,
    path_kind: str,
    path: Path,
) -> dict[str, object]:
    return {
        "schema_version": "1.0",
        **_plan_context_fields(context),
        "path_kind": path_kind,
        "source_ts": float(path.ts),
        "frame_id": path.frame_id,
        "poses": [
            {
                "source_ts": float(pose.ts),
                "frame_id": pose.frame_id,
                "position": _vector_fields(pose.position),
                "orientation_xyzw": [
                    float(pose.orientation.x),
                    float(pose.orientation.y),
                    float(pose.orientation.z),
                    float(pose.orientation.w),
                ],
            }
            for pose in path.poses
        ],
    }


def _quaternion_yaw(orientation: Any) -> float:
    x = float(orientation.x)
    y = float(orientation.y)
    z = float(orientation.z)
    w = float(orientation.w)
    return math.atan2(
        2.0 * (w * z + x * y),
        1.0 - 2.0 * (y * y + z * z),
    )
