from pathlib import Path
from threading import RLock
from types import SimpleNamespace

import numpy as np

from dimos.mapping.costmapper import CostMapper
from dimos.msgs.geometry_msgs.Pose import Pose
from dimos.msgs.nav_msgs.OccupancyGrid import OccupancyGrid


class _PublishedCostmaps:
    def __init__(self) -> None:
        self.messages: list[OccupancyGrid] = []

    def publish(self, grid: OccupancyGrid) -> None:
        self.messages.append(grid)


def _mapper(tmp_path: Path) -> CostMapper:
    mapper = object.__new__(CostMapper)
    mapper.config = SimpleNamespace(persistent_map_dir=str(tmp_path / "costmap"))
    mapper._latest_lock = RLock()
    mapper._latest_costmap = None
    mapper._persistent_costmap = None
    mapper.global_costmap = _PublishedCostmaps()
    mapper.persistent_costmap = _PublishedCostmaps()
    return mapper


def test_save_and_load_persistent_costmap(tmp_path: Path) -> None:
    mapper = _mapper(tmp_path)
    data = np.array([[0, 100], [-1, 50]], dtype=np.int8)
    mapper._latest_costmap = OccupancyGrid(
        grid=data,
        resolution=0.2,
        origin=Pose(1.0, -1.0, 0.0),
        frame_id="map_old",
        ts=10.0,
    )

    saved_path = mapper.save_map()
    mapper._latest_costmap = None

    assert saved_path == str(tmp_path / "costmap")
    assert mapper.load_map(publish=False)
    assert mapper._latest_costmap is None
    assert mapper._persistent_costmap is not None
    assert np.array_equal(mapper._persistent_costmap.grid, data)
    assert mapper._persistent_costmap.resolution == 0.2
    assert mapper._persistent_costmap.frame_id == "map_old"
    assert mapper._persistent_costmap.origin.position.x == 1.0
    assert mapper._persistent_costmap.origin.position.y == -1.0
    assert mapper.global_costmap.messages == []


def test_load_can_publish_when_explicitly_requested(tmp_path: Path) -> None:
    mapper = _mapper(tmp_path)
    mapper._latest_costmap = OccupancyGrid(grid=np.zeros((2, 2), dtype=np.int8))
    mapper.save_map()
    mapper._latest_costmap = None

    assert mapper.load_map(publish=True)
    assert len(mapper.global_costmap.messages) == 1


def test_publish_persistent_map(tmp_path: Path) -> None:
    mapper = _mapper(tmp_path)
    mapper._persistent_costmap = OccupancyGrid(grid=np.zeros((2, 2), dtype=np.int8))

    assert mapper.publish_persistent_map()
    assert len(mapper.persistent_costmap.messages) == 1


def test_relocalize_current_map(tmp_path: Path) -> None:
    mapper = _mapper(tmp_path)
    persistent_data = np.zeros((10, 10), dtype=np.int8)
    persistent_data[4:6, 6:8] = 100
    local_data = persistent_data[4:8, 5:9].copy()
    mapper._persistent_costmap = OccupancyGrid(
        grid=persistent_data,
        resolution=0.5,
        origin=Pose(0.0, 0.0, 0.0),
    )
    mapper._latest_costmap = OccupancyGrid(
        grid=local_data,
        resolution=0.5,
        origin=Pose(2.0, 2.0, 0.0),
    )

    result = mapper.relocalize_current_map(search_radius_m=1.0, min_compared_cells=4)

    assert result["success"] is True
    assert result["x"] == 2.5
    assert result["y"] == 2.0
    assert result["map_from_current_x"] == 0.5
    assert result["map_from_current_y"] == 0.0
    assert result["map_from_current_yaw"] == 0.0
    assert result["occupied_compared_cells"] > 0


def test_auto_save_skips_missing_costmap(tmp_path: Path) -> None:
    mapper = _mapper(tmp_path)

    mapper._auto_save_map()

    assert not (tmp_path / "costmap").exists()


def test_load_ros_map_yaml(tmp_path: Path) -> None:
    from PIL import Image

    pgm_path = tmp_path / "map.pgm"
    Image.fromarray(np.full((4, 4), 255, dtype=np.uint8)).save(pgm_path)
    yaml_path = tmp_path / "map.yaml"
    yaml_path.write_text(
        "image: map.pgm\nresolution: 0.2\norigin: [-1, 2, 0]\nnegate: 0\n"
        "occupied_thresh: 0.65\nfree_thresh: 0.196\n"
    )

    mapper = _mapper(tmp_path)
    mapper.config = SimpleNamespace(
        persistent_map_dir=str(tmp_path / "costmap"),
        persistent_map_yaml=str(yaml_path),
    )

    assert mapper.load_map(publish=True)
    assert mapper._persistent_costmap is not None
    assert mapper._persistent_costmap.resolution == 0.2
    assert len(mapper.global_costmap.messages) == 1


def test_publish_prefers_persistent_before_merge(tmp_path: Path) -> None:
    mapper = _mapper(tmp_path)
    persistent = OccupancyGrid(
        grid=np.full((2, 2), 100, dtype=np.int8),
        resolution=0.5,
        origin=Pose(0.0, 0.0, 0.0),
    )
    live = OccupancyGrid(
        grid=np.zeros((2, 2), dtype=np.int8),
        resolution=0.5,
        origin=Pose(1.0, 1.0, 0.0),
    )
    mapper._persistent_costmap = persistent
    mapper._latest_costmap = None
    mapper._merge_live_into_persistent = False

    # Simulate the publish path used on incoming lidar frames.
    publish_grid = live
    if mapper._persistent_costmap is not None and not mapper._merge_live_into_persistent:
        publish_grid = mapper._persistent_costmap
    mapper.global_costmap.publish(publish_grid)

    assert np.array_equal(mapper.global_costmap.messages[-1].grid, persistent.grid)
