# Copyright 2026 Dimensional Inc.
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

"""
Door navigation skills for the LLM agent.

Depends on:
  - ``DoorSpatialMemorySpec`` for querying recorded doors.
  - ``NavigationInterfaceSpec`` for setting navigation goals.
"""

from __future__ import annotations

from typing import Any

from reactivex.disposable import Disposable

from dimos.agents.annotation import skill
from dimos.core.core import rpc
from dimos.core.module import Module
from dimos.core.stream import In
from dimos.msgs.geometry_msgs.PoseStamped import PoseStamped
from dimos.msgs.geometry_msgs.Quaternion import Quaternion
from dimos.msgs.geometry_msgs.Vector3 import Vector3, make_vector3
from dimos.msgs.sensor_msgs.Image import Image
from dimos.navigation.navigation_spec import NavigationInterfaceSpec
from dimos.types.door_memory_spec import DoorSpatialMemorySpec
from dimos.types.spatial_record import RecordType
from dimos.utils.logging_config import setup_logger

logger = setup_logger()


class DoorNavigationSkill(Module):
    """Agent skill for navigating to and querying recorded doors."""

    _door_memory: DoorSpatialMemorySpec
    _navigation: NavigationInterfaceSpec

    _latest_odom: PoseStamped | None = None
    _skill_started: bool = False

    odom: In[PoseStamped]

    @rpc
    def start(self) -> None:
        super().start()
        self.register_disposable(Disposable(self.odom.subscribe(self._on_odom)))
        self._skill_started = True

    @rpc
    def stop(self) -> None:
        super().stop()

    def _on_odom(self, odom: PoseStamped) -> None:
        self._latest_odom = odom

    # ------------------------------------------------------------------
    # Skills exposed to the LLM
    # ------------------------------------------------------------------

    @skill
    def record_door(self, door_name: str, door_state: str = "unknown") -> str:
        """Record the current position as a door in the spatial memory.

        Call this when you see a door and want to remember its location.
        The robot's current position and orientation will be saved with the door.

        Args:
            door_name: A name for this door (e.g. "kitchen door", "front door", "office entrance")
            door_state: Door state - "open", "closed", or "unknown" (default: "unknown")
        """
        return self._record_location(door_name, RecordType.DOOR, door_state)

    @skill
    def record_room(self, room_name: str, description: str = "") -> str:
        """Record the current position as a room in the spatial memory.

        Call this when you enter a room and want to remember its location.
        The robot's current position and orientation will be saved with the room.

        Args:
            room_name: A name for this room (e.g. "kitchen", "living room", "bedroom")
            description: Optional description of the room (e.g. "large room with blue walls")
        """
        return self._record_location(room_name, RecordType.ROOM, "", description)

    def _record_location(
        self,
        name: str,
        record_type: RecordType,
        state: str = "",
        description: str = "",
    ) -> str:
        """Internal helper to record any spatial location."""
        if not self._skill_started:
            raise ValueError(f"{self} has not been started.")

        if not self._latest_odom:
            return "No odometry data yet, cannot record location."

        from dimos.types.spatial_record import SpatialRecord

        pos = self._latest_odom.position
        rot = self._latest_odom.orientation

        rec = SpatialRecord(
            name=name,
            record_type=record_type,
            position=(float(pos.x), float(pos.y), float(pos.z)),
            rotation=(float(rot.x), float(rot.y), float(rot.z)),
            state=state,
            description=description,
        )
        self._door_memory.record_door(rec)
        logger.info("Recorded %s '%s'", record_type.value, name)
        return f"Recorded {record_type.value} '{name}' at position ({pos.x:.2f}, {pos.y:.2f})."

    @skill
    def navigate_to_door(self, door_name: str) -> str:
        """Navigate to a door recorded in the door spatial memory.

        Use this when the human asks you to go to a specific door,
        such as "go to the kitchen door", "go to the front door", etc.
        Uses the topological map to plan the best route through known doors.

        Args:
            door_name: The name of the door to navigate to (e.g. "kitchen door", "front door")
        """
        if not self._skill_started:
            raise ValueError(f"{self} has not been started.")

        from dimos.navigation.topology import TopologyGraph

        door = self._door_memory.find_by_name(door_name)
        if door is None:
            matches = self._door_memory.search_by_name(door_name)
            if matches:
                door = matches[0]
                door_name = door.name
            else:
                return f"No door found matching '{door_name}'. Use query_door to see available doors."

        return self._navigate_with_waypoints(door, door_name)

    def _navigate_with_waypoints(self, target: SpatialRecord, label: str) -> str:
        """Navigate to *target*, optionally via topological waypoints."""
        from dimos.navigation.topology import TopologyGraph

        # Build topo graph from all known landmarks
        all_records = self._door_memory.get_all_doors()
        topo = TopologyGraph()
        for r in all_records:
            topo.add_record(r)

        # If we have odometry, plan a topological path
        waypoints: list[TopologyNode] = []
        if self._latest_odom is not None:
            pos = self._latest_odom.position
            waypoints = topo.shortest_path(float(pos.x), float(pos.y), target.position[0], target.position[1])

        # If topology found useful waypoints (more than just the target),
        # navigate to the first one as an intermediate goal
        if waypoints and waypoints[-1].record_id != target.record_id:
            # Navigate to first topological waypoint
            wp = waypoints[0]
            logger.info("Topological path: navigating via waypoint '%s'", wp.name)
            return self._set_goal(wp.x, wp.y, f"Navigating toward {label} via {wp.name}")

        # Otherwise navigate directly to the target
        yaw = target.rotation[2]
        offset_x = 1.5 * __import__("math").cos(yaw)
        offset_y = 1.5 * __import__("math").sin(yaw)

        return self._set_goal(
            target.position[0] + offset_x,
            target.position[1] + offset_y,
            f"Navigating to {label} (state: {target.state})",
            yaw,
        )

    def _set_goal(self, x: float, y: float, message: str, yaw: float = 0.0) -> str:
        """Set a navigation goal and return a user-facing message."""
        goal_pose = PoseStamped(
            position=make_vector3(x, y, 0.0),
            orientation=Quaternion.from_euler(Vector3(0.0, 0.0, yaw)),
            frame_id="map",
        )
        self._navigation.set_goal(goal_pose)
        return f"{message}. To cancel call stop_navigation."

    @skill
    def query_door(self, query: str) -> str:
        """Search for landmarks (doors, rooms, etc.) in the spatial memory.

        Use this when you want to:
        - Find out what landmarks the robot knows about
        - Find a door or room by name
        - Check the state of a door (open/closed)

        Args:
            query: Search query (e.g. "all", "doors", "rooms", "kitchen", "open doors")
        """
        if not self._skill_started:
            raise ValueError(f"{self} has not been started.")

        query_lower = query.lower().strip()

        # Type filters
        if query_lower in ("doors", "all doors"):
            records = self._door_memory.query_by_type(RecordType.DOOR)
        elif query_lower in ("rooms", "all rooms"):
            records = self._door_memory.query_by_type(RecordType.ROOM)
        elif query_lower in ("all", "list all", "all landmarks"):
            records = self._door_memory.get_all_doors()
        elif query_lower in ("open", "opened", "open doors"):
            records = self._door_memory.query_by_state("open")
        elif query_lower in ("closed", "close", "closed doors"):
            records = self._door_memory.query_by_state("closed")
        else:
            records = self._door_memory.query_by_text(query, limit=10)

        if not records:
            return f"No landmarks found matching '{query}'."

        lines = [f"Found {len(records)} landmark(s):"]
        for r in records:
            name = r.name or f"rec_{r.record_id[:6]}"
            rtype = r.record_type.value
            state_str = f" ({r.state})" if r.state else ""
            pos = f"({r.position[0]:.1f}, {r.position[1]:.1f})"
            seen = r.observation_count
            lines.append(f"  - {name} [{rtype}{state_str}] at {pos} (seen {seen}x)")
        return "\n".join(lines)
