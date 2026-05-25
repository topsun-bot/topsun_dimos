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
Topological map built from spatial landmarks.

Builds a graph where:
  - Nodes are recorded landmarks (doors, rooms, etc.)
  - Edges connect nearby nodes (within ``MAX_EDGE_DISTANCE``)
  - Path finding returns a sequence of waypoints through known doors

This provides coarse-to-fine navigation: the topological planner
says "go through door A then door B", and the local planner handles
the fine-grained path between each waypoint.
"""

from __future__ import annotations

import heapq

from dimos.types.spatial_record import SpatialRecord

# Maximum distance for two nodes to be directly connected.
_MAX_EDGE_DISTANCE = 8.0


class TopologyNode:
    """A node in the topological graph."""

    def __init__(self, record: SpatialRecord) -> None:
        self.record_id = record.record_id
        self.name = record.name or record.record_id
        self.x = record.position[0]
        self.y = record.position[1]
        self.record_type = record.record_type
        self.neighbors: dict[str, float] = {}  # node_id -> distance


class TopologyGraph:
    """Graph of spatial landmarks for topological path planning.

    Usage::

        graph = TopologyGraph()
        graph.add_record(spatial_record)
        path = graph.shortest_path(start_x, start_y, goal_x, goal_y)
        # path == [record1, record2, ...] through known doors
    """

    def __init__(self, max_edge_distance: float = _MAX_EDGE_DISTANCE) -> None:
        self._nodes: dict[str, TopologyNode] = {}
        self._max_edge = max_edge_distance

    # ------------------------------------------------------------------
    # Build
    # ------------------------------------------------------------------

    def add_record(self, record: SpatialRecord) -> None:
        """Add or update a node in the graph."""
        # Remove existing node first to clear stale neighbor links
        if record.record_id in self._nodes:
            self.remove_record(record.record_id)
        node = TopologyNode(record)
        # Recompute edges to all existing nodes
        for existing in self._nodes.values():
            dx = node.x - existing.x
            dy = node.y - existing.y
            dist = (dx * dx + dy * dy) ** 0.5
            if dist < self._max_edge:
                node.neighbors[existing.record_id] = dist
                existing.neighbors[node.record_id] = dist
        self._nodes[node.record_id] = node

    def remove_record(self, record_id: str) -> None:
        """Remove a node and its edges."""
        self._nodes.pop(record_id, None)
        for node in self._nodes.values():
            node.neighbors.pop(record_id, None)

    def rebuild(self, records: list[SpatialRecord]) -> None:
        """Clear and rebuild the graph from a list of records."""
        self._nodes.clear()
        for r in records:
            self.add_record(r)

    # ------------------------------------------------------------------
    # Query
    # ------------------------------------------------------------------

    def find_nearest_node(self, x: float, y: float) -> TopologyNode | None:
        """Find the graph node closest to (x, y)."""
        best: TopologyNode | None = None
        best_dist = float("inf")
        for node in self._nodes.values():
            dx = node.x - x
            dy = node.y - y
            dist = dx * dx + dy * dy
            if dist < best_dist:
                best_dist = dist
                best = node
        return best

    def shortest_path(
        self,
        start_x: float,
        start_y: float,
        goal_x: float,
        goal_y: float,
    ) -> list[TopologyNode]:
        """A* shortest path in the topological graph.

        Returns a list of nodes (waypoints) from start to goal,
        excluding the start/end positions themselves.
        Returns empty list if no path exists.
        """
        start_node = self.find_nearest_node(start_x, start_y)
        goal_node = self.find_nearest_node(goal_x, goal_y)

        if start_node is None or goal_node is None:
            return []
        if start_node.record_id == goal_node.record_id:
            return []

        # A*
        open_set: list[tuple[float, str, str | None]] = [(0.0, start_node.record_id, None)]
        came_from: dict[str, str | None] = {}
        g_score: dict[str, float] = {start_node.record_id: 0.0}

        while open_set:
            _, current_id, _ = heapq.heappop(open_set)

            if current_id == goal_node.record_id:
                return self._reconstruct_path(
                    came_from, current_id, start_node.record_id, goal_node
                )

            current = self._nodes[current_id]
            for neighbor_id, dist in current.neighbors.items():
                tentative = g_score[current_id] + dist
                if tentative < g_score.get(neighbor_id, float("inf")):
                    came_from[neighbor_id] = current_id
                    g_score[neighbor_id] = tentative
                    h = self._heuristic(self._nodes[neighbor_id], goal_node)
                    heapq.heappush(open_set, (tentative + h, neighbor_id, current_id))

        return []

    def _heuristic(self, a: TopologyNode, b: TopologyNode) -> float:
        dx = a.x - b.x
        dy = a.y - b.y
        return (dx * dx + dy * dy) ** 0.5

    def _reconstruct_path(
        self,
        came_from: dict[str, str | None],
        current_id: str,
        start_id: str,
        goal_node: TopologyNode,
    ) -> list[TopologyNode]:
        path: list[TopologyNode] = []
        while current_id in came_from and current_id != start_id:
            if current_id in self._nodes and current_id != goal_node.record_id:
                path.append(self._nodes[current_id])
            current_id = came_from[current_id]  # type: ignore[assignment]
        path.reverse()
        return path

    def count(self) -> int:
        return len(self._nodes)

    def get_all_nodes(self) -> list[TopologyNode]:
        return list(self._nodes.values())
