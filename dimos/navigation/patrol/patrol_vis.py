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
import math
import threading
import time
from collections import deque

from dimos_lcm.std_msgs import String
from reactivex.disposable import Disposable

from dimos.agents.annotation import skill
from dimos.core.core import rpc
from dimos.core.module import Module
from dimos.core.stream import In
from dimos.msgs.geometry_msgs import PoseStamped
from dimos.msgs.nav_msgs import OccupancyGrid, Path
from dimos.utils.logging_config import setup_logger

logger = setup_logger()

try:
    import socketio

    SOCKETIO_AVAILABLE = True
except ImportError:
    SOCKETIO_AVAILABLE = False


class PatrolVisModule(Module):
    slam_pose: In[PoseStamped]
    patrol_status: In[String]
    patrol_costmap: In[OccupancyGrid]
    path: In[Path]
    patrol_event: In[String]

    def __init__(self, vis_port: int = 7779) -> None:
        super().__init__()
        self._vis_port = vis_port
        self._lock = threading.Lock()
        self._sio: socketio.SimpleClient | None = None
        self._event_log: deque[dict] = deque(maxlen=50)
        self._last_pose_emit = 0.0
        self._pose_interval = 0.5  # 2Hz rate limit
        self._current_route_data: dict | None = None
        self._visited_count = 0
        self._total_waypoints = 0
        self._current_waypoint_name = ""

    @rpc
    def start(self) -> None:
        super().start()
        if SOCKETIO_AVAILABLE:
            self._connect_socketio()

        self._disposables.add(Disposable(self.slam_pose.subscribe(self._on_pose)))
        self._disposables.add(Disposable(self.patrol_status.subscribe(self._on_status)))
        self._disposables.add(Disposable(self.patrol_event.subscribe(self._on_event)))
        self._disposables.add(Disposable(self.path.subscribe(self._on_path)))

    @rpc
    def stop(self) -> None:
        if self._sio is not None:
            try:
                self._sio.disconnect()
            except Exception:
                pass
        super().stop()

    def _connect_socketio(self) -> None:
        if not SOCKETIO_AVAILABLE:
            logger.warning("socketio not available, visualization will be log-only")
            return

        try:
            self._sio = socketio.SimpleClient()
            self._sio.connect(f"http://localhost:{self._vis_port}", wait_timeout=5)
            logger.info(f"PatrolVis connected to SocketIO server on port {self._vis_port}")
        except Exception:
            logger.warning(
                f"Failed to connect to SocketIO server on port {self._vis_port}. "
                "Patrol visualization will be log-only."
            )
            self._sio = None

    def _emit(self, event: str, data: dict) -> None:
        logger.debug(f"PatrolVis event: {event}", data=data)
        if self._sio is not None:
            try:
                self._sio.emit(event, data)
            except Exception:
                logger.debug(f"Failed to emit {event}")

    def _on_pose(self, pose: PoseStamped) -> None:
        now = time.time()
        if now - self._last_pose_emit < self._pose_interval:
            return
        self._last_pose_emit = now

        qx = pose.orientation.x
        qy = pose.orientation.y
        qz = pose.orientation.z
        qw = pose.orientation.w
        heading = math.atan2(2 * (qw * qz + qx * qy), 1 - 2 * (qy * qy + qz * qz))

        self._emit("patrol_position", {
            "x": pose.position.x,
            "y": pose.position.y,
            "heading": heading,
        })

    def _on_status(self, status_json: String) -> None:
        try:
            data = json.loads(status_json.data if hasattr(status_json, "data") else status_json)
        except (json.JSONDecodeError, TypeError):
            return

        with self._lock:
            self._current_route_data = data

        route_name = data.get("route_name")
        visited = len(data.get("visited_waypoints", []))
        total = data.get("total_waypoints", 0)
        current_wp = ""
        waypoints = data.get("visited_waypoints", [])
        if waypoints:
            current_wp = waypoints[-1] if data.get("state") != "idle" else ""

        self._visited_count = visited
        self._total_waypoints = total
        self._current_waypoint_name = current_wp

        elapsed = 0.0
        if data.get("start_time"):
            elapsed = time.time() - data["start_time"]

        self._emit("patrol_progress", {
            "visited": visited,
            "total": total,
            "current_waypoint": current_wp,
            "elapsed_seconds": int(elapsed),
            "state": data.get("state", "idle"),
        })

    def _on_event(self, event_json: String) -> None:
        try:
            data = json.loads(event_json.data if hasattr(event_json, "data") else event_json)
        except (json.JSONDecodeError, TypeError):
            return

        with self._lock:
            self._event_log.append(data)

        self._emit("patrol_event", data)

    def _on_path(self, path: Path) -> None:
        if not hasattr(path, "poses") or not path.poses:
            return

        path_points = []
        for pose in path.poses[:200]:  # limit points for bandwidth
            path_points.append([pose.position.x, pose.position.y])

        self._emit("patrol_path", {"points": path_points})

    @rpc
    def set_route_visualization(self, route_data: dict) -> bool:
        with self._lock:
            self._current_route_data = route_data
        self._emit("patrol_route", route_data)
        return True

    @skill
    def show_patrol_status(self) -> str:
        """Show the current patrol visualization status.

        Returns:
            str: Summary of what is currently being displayed
        """
        with self._lock:
            route_data = self._current_route_data
            event_count = len(self._event_log)

        if route_data is None:
            return "Patrol viz: no active patrol displayed."

        route_name = route_data.get("route_name", "unknown")
        return (
            f"Patrol viz: route '{route_name}' displayed, "
            f"{self._visited_count}/{self._total_waypoints} waypoints visited, "
            f"{event_count} events logged."
        )


patrol_vis = PatrolVisModule.blueprint

__all__ = ["PatrolVisModule", "patrol_vis"]
