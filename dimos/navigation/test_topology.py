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

import pytest

from dimos.navigation.topology import TopologyGraph
from dimos.types.spatial_record import SpatialRecord, RecordType


class TestTopologyGraph:
    def test_empty_graph(self) -> None:
        g = TopologyGraph()
        assert g.count() == 0

    def test_add_single_record(self) -> None:
        g = TopologyGraph()
        r = SpatialRecord(name="kitchen", record_type=RecordType.ROOM, position=(0.0, 0.0, 0.0))
        g.add_record(r)
        assert g.count() == 1

    def test_neighbors_within_range(self) -> None:
        g = TopologyGraph(max_edge_distance=10.0)
        a = SpatialRecord(name="room_a", position=(0.0, 0.0, 0.0))
        b = SpatialRecord(name="room_b", position=(3.0, 4.0, 0.0))  # 5m apart
        g.add_record(a)
        g.add_record(b)

        node_a = g.find_nearest_node(0.0, 0.0)
        assert node_a is not None
        assert "room_b" in [n.name for n in g.get_all_nodes() if n.record_id != node_a.record_id]

        # b should be a neighbor of a
        assert b.record_id in node_a.neighbors

    def test_no_edge_between_distant_nodes(self) -> None:
        g = TopologyGraph(max_edge_distance=5.0)
        a = SpatialRecord(name="far_a", position=(0.0, 0.0, 0.0))
        b = SpatialRecord(name="far_b", position=(100.0, 100.0, 0.0))
        g.add_record(a)
        g.add_record(b)

        node_a = g.find_nearest_node(0.0, 0.0)
        assert node_a is not None
        assert b.record_id not in node_a.neighbors

    def test_find_nearest_node(self) -> None:
        g = TopologyGraph()
        g.add_record(SpatialRecord(name="near", position=(1.0, 1.0, 0.0)))
        g.add_record(SpatialRecord(name="far", position=(50.0, 50.0, 0.0)))

        nearest = g.find_nearest_node(0.0, 0.0)
        assert nearest is not None
        assert nearest.name == "near"

    def test_shortest_path_uses_intermediate(self) -> None:
        """With two rooms, start and goal snap to different rooms,
        and the door between them is the intermediate waypoint."""
        g = TopologyGraph(max_edge_distance=10.0)
        g.add_record(SpatialRecord(name="room_a", position=(0.0, 0.0, 0.0)))
        g.add_record(SpatialRecord(name="door", position=(5.0, 0.0, 0.0)))
        g.add_record(SpatialRecord(name="room_b", position=(10.0, 0.0, 0.0)))

        path = g.shortest_path(0.0, 0.0, 10.0, 0.0)
        # room_a -> door -> room_b
        assert len(path) == 1
        assert path[0].name == "door"

    def test_shortest_path_through_doors(self) -> None:
        """RoomA -> Door1 -> Corridor -> Door2 -> RoomB"""
        g = TopologyGraph(max_edge_distance=6.0)
        g.add_record(SpatialRecord(name="room_a", record_type=RecordType.ROOM, position=(0.0, 0.0, 0.0)))
        g.add_record(SpatialRecord(name="door_1", record_type=RecordType.DOOR, position=(5.0, 0.0, 0.0)))
        g.add_record(SpatialRecord(name="corridor", record_type=RecordType.ROOM, position=(10.0, 0.0, 0.0)))
        g.add_record(SpatialRecord(name="door_2", record_type=RecordType.DOOR, position=(15.0, 0.0, 0.0)))
        g.add_record(SpatialRecord(name="room_b", record_type=RecordType.ROOM, position=(20.0, 0.0, 0.0)))

        path = g.shortest_path(0.0, 0.0, 20.0, 0.0)
        assert len(path) >= 3  # Should go through door_1, corridor, door_2
        assert any(n.name == "door_1" for n in path)
        assert any(n.name == "door_2" for n in path)

    def test_no_path(self) -> None:
        g = TopologyGraph(max_edge_distance=3.0)
        g.add_record(SpatialRecord(name="room_a", position=(0.0, 0.0, 0.0)))
        g.add_record(SpatialRecord(name="room_b", position=(100.0, 100.0, 0.0)))
        path = g.shortest_path(-5.0, -5.0, 105.0, 105.0)
        assert path == []

    def test_same_start_goal(self) -> None:
        g = TopologyGraph()
        g.add_record(SpatialRecord(name="room", position=(0.0, 0.0, 0.0)))
        path = g.shortest_path(0.0, 0.0, 1.0, 1.0)
        assert path == []

    def test_remove_record(self) -> None:
        g = TopologyGraph(max_edge_distance=10.0)
        a = SpatialRecord(name="room_a", position=(0.0, 0.0, 0.0))
        b = SpatialRecord(name="room_b", position=(3.0, 0.0, 0.0))
        g.add_record(a)
        g.add_record(b)
        g.remove_record(a.record_id)
        assert g.count() == 1

        node_b = g.find_nearest_node(3.0, 0.0)
        assert node_b is not None
        assert a.record_id not in node_b.neighbors

    def test_rebuild(self) -> None:
        g = TopologyGraph()
        g.add_record(SpatialRecord(name="old", position=(0.0, 0.0, 0.0)))
        g.rebuild([
            SpatialRecord(name="new", position=(10.0, 10.0, 0.0)),
        ])
        assert g.count() == 1
        assert g.find_nearest_node(10.0, 10.0) is not None
