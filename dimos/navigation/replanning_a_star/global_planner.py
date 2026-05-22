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
from collections.abc import Callable
from threading import Event, RLock, Thread, current_thread
import time

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
from dimos.navigation.replanning_a_star.foot_raise import cross_threshold_gait
from dimos.navigation.replanning_a_star.goal_validator import find_safe_goal
from dimos.navigation.replanning_a_star.local_planner import LocalPlanner, StopMessage
from dimos.navigation.replanning_a_star.min_cost_astar import min_cost_astar
from dimos.navigation.replanning_a_star.navigation_map import NavigationMap
from dimos.navigation.obstacle_snapshot.types import ObstacleNavOutcome, ObstacleTriggerReason
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

    _safe_goal_tolerance: float = 4.0
    _goal_tolerance: float = 0.2
    _rotation_tolerance: float = math.radians(15)
    # Integral of planar odom motion while a goal is active (includes replans).
    _trip_path_length_m: float
    _last_odom_xy: tuple[float, float] | None
    _last_completed_navigation_path_m: float
    _replan_goal_tolerance: float = 0.5
    _stuck_time_window: float = 8.0
    _stuck_threshold: float = 0.4
    _max_path_deviation: float = 0.9
    _replanning_enabled: bool = True
    _obstacle_block_handler: Callable[[ObstacleTriggerReason], ObstacleNavOutcome] | None

    def __init__(self, global_config: GlobalConfig) -> None:
        self.path = Subject()
        self.goal_reached = Subject()

        self._global_config = global_config
        self._navigation_map = NavigationMap(self._global_config, "voronoi")
        self._navigation_map_near = NavigationMap(self._global_config, "gradient")
        self._local_planner = LocalPlanner(
            self._global_config, self._navigation_map, self._goal_tolerance
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
        self._trip_path_length_m = 0.0
        self._last_odom_xy = None
        self._last_completed_navigation_path_m = 0.0
        self._obstacle_block_handler = None
        self._foot_raise_callback: Callable[[float, float, int], None] | None = None

    def set_obstacle_block_handler(
        self, handler: Callable[[ObstacleTriggerReason], ObstacleNavOutcome] | None
    ) -> None:
        self._obstacle_block_handler = handler

    def set_foot_raise_callback(self, callback: Callable[[float, float, int], None] | None) -> None:
        self._foot_raise_callback = callback

    def _apply_threshold_gait(self, raise_m: float, reach_m: float, front_leg_mask: int = 0) -> None:
        if self._foot_raise_callback is not None:
            self._foot_raise_callback(raise_m, reach_m, front_leg_mask)

    def _hold_single_leg_phase(
        self,
        raise_m: float,
        reach_m: float,
        lift_mask: int,
        settle_s: float,
        land_s: float,
        *,
        step_m: float = 0.0,
        creep_s: float = 0.0,
    ) -> None:
        """Lift one front leg, optional creep forward while raised, then land before the other leg."""
        stop_twist = Twist()
        self._local_planner.cmd_vel.on_next(stop_twist)
        self._apply_threshold_gait(raise_m, reach_m, lift_mask)
        time.sleep(settle_s * 0.35)

        if step_m > 0.0 and creep_s > 0.0:
            creep_speed = step_m / creep_s
            self._local_planner.cmd_vel.on_next(
                Twist(linear=Vector3(creep_speed, 0.0, 0.0))
            )
            time.sleep(creep_s)
            self._local_planner.cmd_vel.on_next(stop_twist)

        time.sleep(settle_s * 0.35)
        self._apply_threshold_gait(0.0, 0.0, 0)
        time.sleep(land_s)

    def _run_cross_gait_sequence(self, raise_m: float, reach_m: float) -> None:
        """Backup, then staged FL step + FR step (no continuous walk while one foot is on the sill)."""
        g = self._global_config
        stop_twist = Twist()
        self._local_planner.cmd_vel.on_next(stop_twist)
        self._apply_threshold_gait(0.0, 0.0, 0)

        if g.obstacle_cross_backup_s > 0.0:
            backup = Twist(linear=Vector3(-g.obstacle_cross_linear_speed, 0.0, 0.0))
            self._local_planner.cmd_vel.on_next(backup)
            time.sleep(g.obstacle_cross_backup_s)
            self._local_planner.cmd_vel.on_next(stop_twist)
            time.sleep(0.12)

        phase_s = g.obstacle_cross_leg_phase_s
        land_s = g.obstacle_cross_leg_land_s
        step_m = g.obstacle_cross_leg_step_m
        creep_s = g.obstacle_cross_leg_creep_s
        logger.info(
            "Cross gait sequence",
            foot_raise_m=round(raise_m, 3),
            front_reach_m=round(reach_m, 3),
            leg_step_m=round(step_m, 3),
        )
        self._hold_single_leg_phase(
            raise_m, reach_m, 2, phase_s, land_s, step_m=step_m, creep_s=creep_s
        )
        self._hold_single_leg_phase(
            raise_m, reach_m, 1, phase_s, land_s, step_m=step_m, creep_s=creep_s
        )

    def attempt_cross_forward(self, forward_m: float) -> bool:
        """Drive forward without changing the stored navigation goal (cross maneuver)."""
        with self._lock:
            start_odom = self._current_odom
            goal = self._current_goal
        if start_odom is None or goal is None:
            return False

        cross_raise_m = 0.0
        cross_reach_m = 0.0
        gait_active = False
        if self._foot_raise_callback is not None and self._global_config.simulation:
            # Local planner may already be idle (path cleared); still use cross gait.
            max_cost = 100
            path_clearance = self._local_planner.get_path_clearance()
            if path_clearance is not None:
                max_cost = max(max_cost, path_clearance.max_cost_ahead())
            cross_raise_m, cross_reach_m = cross_threshold_gait(
                max_cost,
                self._global_config.obstacle_cross_foot_raise_m,
                self._global_config.obstacle_cross_front_reach_m,
            )
            gait_active = cross_raise_m > 0.0 or cross_reach_m > 0.0

        if gait_active:
            g = self._global_config
            min_advance = max(0.20, 2.0 * g.obstacle_cross_leg_step_m * 0.85)
        else:
            min_advance = max(0.25, forward_m * 0.55)
        timeout_s = 16.0

        logger.info(
            "Attempting cross forward",
            forward_m=forward_m,
            min_advance_m=min_advance,
            foot_raise_m=round(cross_raise_m, 3),
            front_reach_m=round(cross_reach_m, 3),
        )

        stop_twist = Twist()
        deadline = time.monotonic() + timeout_s
        try:
            if gait_active:
                self._run_cross_gait_sequence(cross_raise_m, cross_reach_m)

            while time.monotonic() < deadline:
                with self._lock:
                    odom = self._current_odom
                if odom is not None and start_odom.position.distance(odom.position) >= min_advance:
                    self._local_planner.cmd_vel.on_next(stop_twist)
                    logger.info(
                        "Cross forward succeeded",
                        advance_m=round(start_odom.position.distance(odom.position), 3),
                    )
                    return True
                self._local_planner.cmd_vel.on_next(stop_twist)
                time.sleep(0.1)

            self._local_planner.cmd_vel.on_next(stop_twist)
            logger.info("Cross forward failed (timeout or insufficient advance)")
            return False
        finally:
            if gait_active:
                self._apply_threshold_gait(0.0, 0.0, 0)

    def start(self) -> None:
        self._local_planner.start()
        self._disposables.add(
            self._local_planner.stopped_navigating.subscribe(self._on_stopped_navigating)
        )
        self._stop_planner.clear()
        self._thread = Thread(target=self._thread_entrypoint, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self.cancel_goal()
        self._local_planner.stop()
        self._disposables.dispose()
        self._stop_planner.set()
        self._replan_event.set()

        if self._thread is not None and self._thread is not current_thread():
            self._thread.join(DEFAULT_THREAD_JOIN_TIMEOUT)
            if self._thread.is_alive():
                logger.error("GlobalPlanner thread did not stop in time.")
            self._thread = None

    def handle_odom(self, msg: PoseStamped) -> None:
        with self._lock:
            self._current_odom = msg
            if self._current_goal is not None and not self._goal_reached:
                x, y = float(msg.position.x), float(msg.position.y)
                if self._last_odom_xy is not None:
                    lx, ly = self._last_odom_xy
                    self._trip_path_length_m += math.hypot(x - lx, y - ly)
                self._last_odom_xy = (x, y)

        self._local_planner.handle_odom(msg)
        self._position_tracker.add_position(msg)

    def handle_global_costmap(self, msg: OccupancyGrid) -> None:
        self._navigation_map.update(msg)
        self._navigation_map_near.update(msg)

    def handle_goal_request(self, goal: PoseStamped) -> None:
        logger.info("Got new goal", goal=str(goal))
        with self._lock:
            self._current_goal = goal
            self._goal_reached = False
            self._trip_path_length_m = 0.0
            self._last_odom_xy = None
        self._replan_limiter.reset()
        self._plan_path()

    def set_safe_goal_clearance(self, clearance: float) -> None:
        with self._lock:
            self._safe_goal_clearance = clearance

    def reset_safe_goal_clearance(self) -> None:
        self._reset_safe_goal_clearance()

    def cancel_goal(self, *, but_will_try_again: bool = False, arrived: bool = False) -> None:
        logger.info("Cancelling goal.", but_will_try_again=but_will_try_again, arrived=arrived)

        path_m = 0.0
        with self._lock:
            self._position_tracker.reset_data()

            if not but_will_try_again:
                path_m = self._trip_path_length_m
                self._last_completed_navigation_path_m = path_m
                self._current_goal = None
                self._goal_reached = arrived
                self._replan_limiter.reset()
                self._last_odom_xy = None

        if not but_will_try_again:
            # Emit before path/stop/LCM: ensures trip distance is logged even if later steps
            # block, throw, or if an older dimos build had goal_reached before this line.
            log_kwargs: dict[str, object] = {
                "arrived": arrived,
                "path_length_m": round(path_m, 3),
            }
            if arrived:
                log_kwargs["trip_summary_cn"] = (
                    f"已到达目的地：本次行程总距离 {path_m:.3f} 米（XY 平面里程计累计）。"
                )
            logger.info("Navigation ended.", **log_kwargs)

        self.path.on_next(Path())
        self._local_planner.stop_planning()

        if not but_will_try_again:
            self.goal_reached.on_next(Bool(arrived))

    def set_replanning_enabled(self, enabled: bool) -> None:
        with self._lock:
            self._replanning_enabled = enabled

    def get_state(self) -> NavigationState:
        return self._local_planner.get_state()

    def is_goal_reached(self) -> bool:
        with self._lock:
            return self._goal_reached

    def get_last_navigation_path_length_m(self) -> float:
        """Planar path length (m) integrated from odom for the last finished navigation."""
        with self._lock:
            return self._last_completed_navigation_path_m

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
                self._replan_path()
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
                logger.info("Robot is stuck.")
                self._handle_navigation_blocked("stuck")
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
            logger.info("Obstacle found on path.")
            self._handle_navigation_blocked("obstacle_found")
        elif stop_message == "error":
            logger.info("Failure in navigation.")
            self._replan_path()
        else:
            logger.error(f"No code to handle '{stop_message}'.")
            self.cancel_goal()

    def _handle_navigation_blocked(self, reason: ObstacleTriggerReason) -> None:
        if not self._global_config.obstacle_navigation:
            self._replan_path()
            return

        if self._obstacle_block_handler is None:
            logger.warning("obstacle_navigation enabled but no ObstacleSnapshotModule in blueprint")
            self._replan_path()
            return

        try:
            outcome = self._obstacle_block_handler(reason)
        except Exception as exc:
            logger.error("Obstacle navigation handler failed; replanning.", error=str(exc))
            self._replan_path()
            return

        logger.info("Obstacle navigation outcome", outcome=outcome, reason=reason)
        if outcome == "continue":
            self._position_tracker.reset_data()
            self._plan_path()
        elif outcome == "detour":
            self._replan_path()
        elif outcome == "stop":
            self.cancel_goal()

    def _replan_path(self) -> None:
        with self._lock:
            current_odom = self._current_odom
            current_goal = self._current_goal

        logger.info("Replanning.", attempt=self._replan_limiter.get_attempt())

        assert current_odom is not None
        assert current_goal is not None

        if current_goal.position.distance(current_odom.position) < self._replan_goal_tolerance:
            self.cancel_goal(arrived=True)
            return

        if not self._replanning_enabled:
            self.cancel_goal()
            return

        if not self._replan_limiter.can_retry(current_odom.position):
            self.cancel_goal()
            return

        self._replan_limiter.will_retry()

        self._plan_path()

    def _plan_path(self) -> None:
        self.cancel_goal(but_will_try_again=True)

        with self._lock:
            current_odom = self._current_odom
            current_goal = self._current_goal

        assert current_goal is not None

        if current_odom is None:
            logger.warning("Cannot handle goal request: missing odometry.")
            return

        safe_goal = self._find_safe_goal(current_goal.position)

        if not safe_goal:
            logger.warning(
                "No safe goal found.", x=round(current_goal.x, 3), y=round(current_goal.y, 3)
            )
            self.cancel_goal()
            return

        path = self._find_wide_path(safe_goal, current_odom.position)

        if not path:
            logger.warning(
                "No path found to the goal.", x=round(safe_goal.x, 3), y=round(safe_goal.y, 3)
            )
            self.cancel_goal()
            return

        resampled_path = smooth_resample_path(path, current_goal, 0.1)

        self.path.on_next(resampled_path)

        self._local_planner.start_planning(resampled_path)

    def _find_wide_path(self, goal: Vector3, robot_pos: Vector3) -> Path | None:
        #        sizes_to_try: list[float] = [2.2, 1.7, 1.3, 1]
        sizes_to_try: list[float] = [1.1]

        for size in sizes_to_try:
            distance = robot_pos.distance(goal)
            navigation_map = self._navigation_map if distance > 1.5 else self._navigation_map_near
            costmap = navigation_map.make_gradient_costmap(size)
            path = min_cost_astar(costmap, goal, robot_pos)
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
