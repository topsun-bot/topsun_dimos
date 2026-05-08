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

import json
import threading
import time
from pathlib import Path

from dimos_lcm.std_msgs import Bool, String
from reactivex.disposable import Disposable

from dimos.agents.annotation import skill
from dimos.core.core import rpc
from dimos.core.module import Module
from dimos.core.stream import In, Out
from dimos.msgs.geometry_msgs import PoseStamped, Quaternion
from dimos.msgs.geometry_msgs.Vector3 import make_vector3
from dimos.msgs.nav_msgs import OccupancyGrid
from dimos.navigation.patrol.types import PatrolRoute, PatrolState, PatrolStateEnum, Waypoint
from dimos.utils.logging_config import setup_logger

logger = setup_logger()

_ROUTES_DIR = Path("~/.dimos/routes").expanduser()


class PatrolExecutor(Module):
    slam_pose: In[PoseStamped]
    goal_reached: In[Bool]
    patrol_costmap: In[OccupancyGrid]

    goal_request: Out[PoseStamped]
    patrol_status: Out[String]
    patrol_event: Out[String]

    rpc_calls: list[str] = [
        "NavigationInterface.set_goal",
        "NavigationInterface.get_state",
        "NavigationInterface.cancel_goal",
    ]

    def __init__(
        self,
        default_waypoint_timeout: float = 60.0,
        default_loiter_time: float = 5.0,
        retry_count: int = 3,
    ) -> None:
        super().__init__()
        self._waypoint_timeout = default_waypoint_timeout
        self._default_loiter_time = default_loiter_time
        self._max_retries = retry_count

        self._lock = threading.Lock()
        self._state = PatrolState()
        self._routes: dict[str, PatrolRoute] = {}
        self._current_pose: PoseStamped | None = None
        self._patrol_thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._goal_reached_event = threading.Event()

    @rpc
    def start(self) -> None:
        super().start()
        self._disposables.add(Disposable(self.slam_pose.subscribe(self._on_pose)))
        self._disposables.add(Disposable(self.goal_reached.subscribe(self._on_goal_reached)))

    @rpc
    def stop(self) -> None:
        self._stop_patrol_internal()
        super().stop()

    def _on_pose(self, pose: PoseStamped) -> None:
        with self._lock:
            self._current_pose = pose

    def _on_goal_reached(self, msg: Bool) -> None:
        if msg.data:
            self._goal_reached_event.set()

    def _on_costmap(self, costmap: OccupancyGrid) -> None:
        pass  # available for future obstacle checking

    def _publish_status(self) -> None:
        with self._lock:
            state = self._state
            route = state.route
            data = {
                "state": state.state.value,
                "route_name": route.name if route else None,
                "current_waypoint_index": state.current_waypoint_index,
                "total_waypoints": len(route.waypoints) if route else 0,
                "visited_waypoints": state.visited_waypoints,
                "start_time": state.start_time,
                "retry_count": state.retry_count,
            }
        self.patrol_status.publish(json.dumps(data))

    def _publish_event(self, event_type: str, **kwargs: object) -> None:
        data = {"type": event_type, "timestamp": time.time(), **kwargs}
        self.patrol_event.publish(json.dumps(data))

    # ── Route Management RPC ──

    @rpc
    def create_route(self, name: str) -> bool:
        with self._lock:
            if name in self._routes:
                return False
            self._routes[name] = PatrolRoute(name=name)
        return True

    @rpc
    def add_waypoint(self, route_name: str, waypoint: Waypoint) -> bool:
        with self._lock:
            route = self._routes.get(route_name)
            if route is None:
                return False
            waypoint.order = len(route.waypoints)
            route.waypoints.append(waypoint)
        return True

    @rpc
    def remove_waypoint(self, route_name: str, waypoint_name: str) -> bool:
        with self._lock:
            route = self._routes.get(route_name)
            if route is None:
                return False
            route.waypoints = [w for w in route.waypoints if w.name != waypoint_name]
            for i, w in enumerate(route.waypoints):
                w.order = i
        return True

    @rpc
    def start_patrol(self, route_name: str) -> bool:
        with self._lock:
            route = self._routes.get(route_name)
            if route is None or not route.waypoints:
                return False
            if self._state.state != PatrolStateEnum.IDLE:
                return False
            self._state = PatrolState(
                route=PatrolRoute(
                    name=route.name,
                    waypoints=list(route.waypoints),
                    loop=route.loop,
                ),
                state=PatrolStateEnum.PLANNING,
            )
            self._stop_event.clear()

        self._patrol_thread = threading.Thread(
            target=self._patrol_loop, name="patrol-executor", daemon=True
        )
        self._patrol_thread.start()
        return True

    @rpc
    def pause_patrol(self) -> bool:
        with self._lock:
            if self._state.state not in (
                PatrolStateEnum.NAVIGATING,
                PatrolStateEnum.LOITERING,
            ):
                return False
            self._state.state = PatrolStateEnum.IDLE
        self._cancel_navigation()
        return True

    @rpc
    def resume_patrol(self) -> bool:
        with self._lock:
            if self._state.state != PatrolStateEnum.IDLE or self._state.route is None:
                return False
            self._state.state = PatrolStateEnum.NAVIGATING
        return True

    @rpc
    def stop_patrol(self) -> bool:
        return self._stop_patrol_internal()

    def _stop_patrol_internal(self) -> bool:
        self._stop_event.set()
        self._cancel_navigation()
        with self._lock:
            if self._state.state == PatrolStateEnum.IDLE:
                return False
            self._state.state = PatrolStateEnum.IDLE
        self._publish_status()
        return True

    @rpc
    def get_patrol_state(self) -> PatrolState:
        with self._lock:
            return PatrolState(
                route=self._state.route,
                current_waypoint_index=self._state.current_waypoint_index,
                state=self._state.state,
                visited_waypoints=list(self._state.visited_waypoints),
                start_time=self._state.start_time,
                estimated_completion=self._state.estimated_completion,
                retry_count=self._state.retry_count,
            )

    @rpc
    def list_routes(self) -> list[PatrolRoute]:
        with self._lock:
            return list(self._routes.values())

    @rpc
    def save_route(self, route_name: str) -> bool:
        with self._lock:
            route = self._routes.get(route_name)
        if route is None:
            return False

        try:
            _ROUTES_DIR.mkdir(parents=True, exist_ok=True)
            data = {
                "name": route.name,
                "loop": route.loop,
                "waypoints": [
                    {
                        "name": w.name,
                        "x": w.pose.position.x,
                        "y": w.pose.position.y,
                        "z": w.pose.position.z,
                        "loiter_time": w.loiter_time,
                    }
                    for w in route.waypoints
                ],
            }
            with open(_ROUTES_DIR / f"{route_name}.json", "w") as f:
                json.dump(data, f, indent=2)
            return True
        except Exception:
            logger.error(f"Failed to save route '{route_name}'", exc_info=True)
            return False

    @rpc
    def load_route(self, route_name: str) -> bool:
        path = _ROUTES_DIR / f"{route_name}.json"
        if not path.exists():
            return False

        try:
            with open(path) as f:
                data = json.load(f)

            waypoints = [
                Waypoint(
                    name=w["name"],
                    pose=PoseStamped(
                        position=make_vector3(w["x"], w["y"], w.get("z", 0)),
                        orientation=Quaternion(0.0, 0.0, 0.0, 1.0),
                        frame_id="map",
                    ),
                    loiter_time=w.get("loiter_time", 5.0),
                    order=i,
                )
                for i, w in enumerate(data["waypoints"])
            ]

            with self._lock:
                self._routes[route_name] = PatrolRoute(
                    name=data["name"],
                    waypoints=waypoints,
                    loop=data.get("loop", False),
                )
            return True
        except Exception:
            logger.error(f"Failed to load route '{route_name}'", exc_info=True)
            return False

    # ── Patrol State Machine ──

    def _patrol_loop(self) -> None:
        with self._lock:
            self._state.start_time = time.time()
            self._state.state = PatrolStateEnum.NAVIGATING

        self._publish_status()
        self._publish_event("patrol_started", route=self._state.route.name if self._state.route else None)

        try:
            while not self._stop_event.is_set():
                with self._lock:
                    state = self._state
                    route = state.route

                if route is None:
                    break

                if state.current_waypoint_index >= len(route.waypoints):
                    with self._lock:
                        self._state.state = PatrolStateEnum.COMPLETED
                    self._publish_status()
                    self._publish_event("patrol_complete", route=route.name)
                    break

                waypoint = route.waypoints[state.current_waypoint_index]
                reached = self._navigate_to_waypoint(waypoint)

                if self._stop_event.is_set():
                    break

                if reached:
                    with self._lock:
                        self._state.visited_waypoints.append(waypoint.name)
                        self._state.current_waypoint_index += 1
                        self._state.retry_count = 0

                    self._publish_event(
                        "waypoint_arrived",
                        waypoint=waypoint.name,
                        index=state.current_waypoint_index,
                    )
                    self._publish_status()

                    if waypoint.loiter_time > 0:
                        with self._lock:
                            self._state.state = PatrolStateEnum.LOITERING
                        self._publish_status()
                        self._stop_event.wait(waypoint.loiter_time)
                        if self._stop_event.is_set():
                            break

                    with self._lock:
                        self._state.state = PatrolStateEnum.NAVIGATING
                else:
                    with self._lock:
                        self._state.retry_count += 1
                        if self._state.retry_count >= self._max_retries:
                            logger.warning(
                                f"Skipping waypoint '{waypoint.name}' after {self._max_retries} retries"
                            )
                            self._state.current_waypoint_index += 1
                            self._state.retry_count = 0
                            self._publish_event(
                                "waypoint_skipped",
                                waypoint=waypoint.name,
                                reason="max retries exceeded",
                            )
                        else:
                            self._state.state = PatrolStateEnum.RECOVERING
                    self._publish_status()

            with self._lock:
                self._state.state = PatrolStateEnum.IDLE
        except Exception:
            logger.error("Patrol loop error", exc_info=True)
            with self._lock:
                self._state.state = PatrolStateEnum.IDLE
        finally:
            self._publish_status()

    def _navigate_to_waypoint(self, waypoint: Waypoint) -> bool:
        try:
            set_goal = self.get_rpc_calls("NavigationInterface.set_goal")
        except Exception:
            logger.error("NavigationInterface not connected")
            return False

        self._goal_reached_event.clear()
        set_goal(waypoint.pose)

        start = time.time()
        while not self._stop_event.is_set():
            if self._goal_reached_event.is_set():
                return True

            if time.time() - start > self._waypoint_timeout:
                logger.warning(f"Waypoint '{waypoint.name}' timed out")
                self._cancel_navigation()
                return False

            self._stop_event.wait(0.5)

        return False

    def _cancel_navigation(self) -> None:
        try:
            cancel = self.get_rpc_calls("NavigationInterface.cancel_goal")
            cancel()
        except Exception:
            pass

    # ── Skills ──

    @skill
    def create_patrol_route(self, name: str) -> str:
        """Create a new empty patrol route with a given name.

        Args:
            name (str): Name for the new patrol route

        Returns:
            str: Outcome of route creation
        """
        with self._lock:
            if name in self._routes:
                return f"Route '{name}' already exists."
        success = self.create_route(name)
        if success:
            return f"Created empty patrol route '{name}'. Add waypoints with add_patrol_waypoint."
        return f"Failed to create route '{name}'."

    @skill
    def add_patrol_waypoint(
        self, route_name: str, waypoint_name: str, x: float, y: float, loiter_time: float = 5.0
    ) -> str:
        """Add a waypoint to a patrol route at the specified coordinates.

        Args:
            route_name (str): Name of the patrol route
            waypoint_name (str): Name for this waypoint
            x (float): X coordinate in meters
            y (float): Y coordinate in meters
            loiter_time (float): Seconds to pause at this waypoint. Default: 5.0

        Returns:
            str: Outcome of adding the waypoint
        """
        with self._lock:
            if route_name not in self._routes:
                return f"Route '{route_name}' not found. Create it first with create_patrol_route."

        waypoint = Waypoint(
            name=waypoint_name,
            pose=PoseStamped(
                position=make_vector3(x, y, 0),
                orientation=Quaternion(0.0, 0.0, 0.0, 1.0),
                frame_id="map",
            ),
            loiter_time=loiter_time,
        )
        success = self.add_waypoint(route_name, waypoint)
        if success:
            return f"Added waypoint '{waypoint_name}' at ({x}, {y}) to route '{route_name}'."
        return f"Failed to add waypoint to route '{route_name}'."

    @skill
    def add_current_position_as_waypoint(
        self, route_name: str, waypoint_name: str, loiter_time: float = 5.0
    ) -> str:
        """Add the robot's current position as a waypoint in a patrol route.

        Args:
            route_name (str): Name of the patrol route
            waypoint_name (str): Name for this waypoint
            loiter_time (float): Seconds to pause at this waypoint. Default: 5.0

        Returns:
            str: Outcome of adding the waypoint
        """
        with self._lock:
            if route_name not in self._routes:
                return f"Route '{route_name}' not found. Create it first with create_patrol_route."
            pose = self._current_pose

        if pose is None:
            return "No position data available yet. Wait for SLAM to initialize."

        waypoint = Waypoint(
            name=waypoint_name,
            pose=pose,
            loiter_time=loiter_time,
        )
        success = self.add_waypoint(route_name, waypoint)
        if success:
            return (
                f"Added waypoint '{waypoint_name}' at current position "
                f"({pose.position.x:.1f}, {pose.position.y:.1f}) to route '{route_name}'."
            )
        return f"Failed to add waypoint to route '{route_name}'."

    @skill
    def start_patrol(self, route_name: str) -> str:
        """Start executing a patrol route. The robot will navigate to each waypoint in order.

        Args:
            route_name (str): Name of the patrol route to execute

        Returns:
            str: Patrol start status
        """
        with self._lock:
            route = self._routes.get(route_name)
            if route is None:
                return f"Route '{route_name}' not found."
            if not route.waypoints:
                return f"Route '{route_name}' has no waypoints. Add some first."
            if self._state.state != PatrolStateEnum.IDLE:
                return f"A patrol is already running (state: {self._state.state.value}). Stop it first."

        success = self.start_patrol(route_name)
        if success:
            n = len(route.waypoints)
            return f"Starting patrol '{route_name}' with {n} waypoints."
        return f"Failed to start patrol '{route_name}'."

    @skill
    def stop_patrol(self) -> str:
        """Stop the current patrol immediately.

        Returns:
            str: Patrol stop status
        """
        with self._lock:
            if self._state.state == PatrolStateEnum.IDLE:
                return "No patrol is currently running."
            visited = len(self._state.visited_waypoints)
            total = len(self._state.route.waypoints) if self._state.route else 0

        self.stop_patrol()
        return f"Patrol stopped after visiting {visited} of {total} waypoints."

    @skill
    def get_patrol_status(self) -> str:
        """Get the current status of the active patrol.

        Returns:
            str: Current patrol status with progress information
        """
        with self._lock:
            state = self._state
            route = state.route

        if state.state == PatrolStateEnum.IDLE:
            return "No patrol is currently active."

        route_name = route.name if route else "unknown"
        total = len(route.waypoints) if route else 0
        current = state.current_waypoint_index
        visited = len(state.visited_waypoints)
        wp_name = (
            route.waypoints[current].name
            if route and current < len(route.waypoints)
            else "none"
        )
        elapsed = time.time() - state.start_time if state.start_time else 0
        minutes = int(elapsed // 60)
        seconds = int(elapsed % 60)

        return (
            f"Patrol '{route_name}' {state.state.value}. "
            f"At waypoint {current + 1}/{total} '{wp_name}'. "
            f"{visited} visited, {minutes}:{seconds:02d} elapsed."
        )

    @skill
    def list_patrol_routes(self) -> str:
        """List all defined patrol routes.

        Returns:
            str: List of patrol routes with waypoint counts
        """
        with self._lock:
            routes = list(self._routes.values())
        if not routes:
            return "No patrol routes defined. Create one with create_patrol_route."
        entries = [f"'{r.name}' ({len(r.waypoints)} waypoints)" for r in routes]
        return "Routes: " + ", ".join(entries) + "."

    @skill
    def save_patrol_route(self, route_name: str) -> str:
        """Save a patrol route to disk for persistence across sessions.

        Args:
            route_name (str): Name of the route to save

        Returns:
            str: Outcome of saving the route
        """
        with self._lock:
            if route_name not in self._routes:
                return f"Route '{route_name}' not found."
            n = len(self._routes[route_name].waypoints)

        success = self.save_route(route_name)
        if success:
            return f"Saved route '{route_name}' with {n} waypoints."
        return f"Failed to save route '{route_name}'."


patrol_executor = PatrolExecutor.blueprint

__all__ = ["PatrolExecutor", "patrol_executor"]
