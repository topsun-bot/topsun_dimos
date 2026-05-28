import numpy as np
import pytest

from dimos.mapping.occupancy.relocalization import match_occupancy_se2, match_occupancy_translation
from dimos.msgs.geometry_msgs.Pose import Pose
from dimos.msgs.nav_msgs.OccupancyGrid import OccupancyGrid


def test_match_occupancy_translation_finds_patch_location() -> None:
    persistent_data = np.zeros((12, 12), dtype=np.int8)
    persistent_data[4:7, 8:10] = 100
    local_data = persistent_data[3:8, 7:11].copy()

    persistent = OccupancyGrid(
        grid=persistent_data,
        resolution=0.25,
        origin=Pose(-1.0, -1.0, 0.0),
    )
    local = OccupancyGrid(
        grid=local_data,
        resolution=0.25,
        origin=Pose(0.5, 0.0, 0.0),
    )

    result = match_occupancy_translation(
        persistent,
        local,
        search_radius_m=1.0,
        min_compared_cells=10,
    )

    assert result.success
    assert result.confidence == 1.0
    assert result.x == 0.75
    assert result.y == -0.25
    assert result.occupied_compared_cells > 0


def test_match_occupancy_se2_finds_right_angle_yaw() -> None:
    persistent_data = np.zeros((12, 12), dtype=np.int8)
    patch = np.array(
        [
            [100, 0, 0],
            [100, 100, 0],
            [0, 100, 100],
        ],
        dtype=np.int8,
    )
    persistent_data[5:8, 6:9] = patch
    local_data = np.rot90(patch, k=-1)

    persistent = OccupancyGrid(
        grid=persistent_data,
        resolution=0.5,
        origin=Pose(0.0, 0.0, 0.0),
    )
    local = OccupancyGrid(
        grid=local_data,
        resolution=0.5,
        origin=Pose(2.5, 2.0, 0.0),
    )

    result = match_occupancy_se2(
        persistent,
        local,
        search_radius_m=1.0,
        yaw_candidates_deg=(0.0, 90.0),
        min_compared_cells=9,
        min_occupied_cells=5,
    )

    assert result.success
    assert result.confidence == 1.0
    assert result.x == 3.0
    assert result.y == 2.5
    assert result.yaw == pytest.approx(np.pi / 2)


def test_match_occupancy_translation_rejects_low_overlap() -> None:
    persistent = OccupancyGrid(grid=np.zeros((5, 5), dtype=np.int8), resolution=0.5)
    local = OccupancyGrid(
        grid=np.zeros((5, 5), dtype=np.int8),
        resolution=0.5,
        origin=Pose(10.0, 10.0, 0.0),
    )

    result = match_occupancy_translation(
        persistent,
        local,
        search_radius_m=0.5,
        min_compared_cells=5,
    )

    assert not result.success
    assert result.compared_cells == 0


def test_match_occupancy_translation_requires_same_resolution() -> None:
    persistent = OccupancyGrid(grid=np.zeros((5, 5), dtype=np.int8), resolution=0.5)
    local = OccupancyGrid(grid=np.zeros((5, 5), dtype=np.int8), resolution=0.25)

    with pytest.raises(ValueError, match="resolutions must match"):
        match_occupancy_translation(persistent, local)


def test_match_occupancy_se2_rejects_non_right_angle_yaw() -> None:
    persistent = OccupancyGrid(grid=np.zeros((5, 5), dtype=np.int8), resolution=0.5)
    local = OccupancyGrid(grid=np.zeros((5, 5), dtype=np.int8), resolution=0.5)

    with pytest.raises(ValueError, match="right-angle"):
        match_occupancy_se2(persistent, local, yaw_candidates_deg=(45.0,))
