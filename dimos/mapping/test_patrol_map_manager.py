# Copyright 2025-2026 Dimensional Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");

import json

import numpy as np
import pytest

from dimos.mapping.patrol_map_manager import PatrolMapManager
from dimos.msgs.geometry_msgs import Pose
from dimos.msgs.nav_msgs import OccupancyGrid


def _make_costmap(free_pct: float = 80.0, resolution: float = 0.05) -> OccupancyGrid:
    grid = np.zeros((100, 100), dtype=np.int8)
    total = grid.size
    n_free = int(total * free_pct / 100)
    n_occ = int(total * (100 - free_pct) / 200)
    grid.flat[:n_free] = 0
    grid.flat[n_free : n_free + n_occ] = 100
    grid.flat[n_free + n_occ :] = -1
    return OccupancyGrid(grid=grid, resolution=resolution, origin=Pose())


class TestPatrolMapManager:
    def test_instantiation(self):
        mgr = PatrolMapManager()
        assert mgr._auto_save_interval == 60.0

    def test_build_metadata(self):
        mgr = PatrolMapManager()
        costmap = _make_costmap()
        meta = mgr._build_metadata(costmap)
        assert "free_percent" in meta
        assert "width" in meta
        assert meta["width"] == 100

    def test_save_and_load(self, tmp_path):
        mgr = PatrolMapManager(map_save_dir=str(tmp_path))
        costmap = _make_costmap()

        mgr._latest_costmap = costmap
        assert mgr.save_map("test_map") is True

        maps = mgr.list_maps()
        assert "test_map" in maps

        mgr._latest_costmap = None
        assert mgr.load_map("test_map") is True
        assert mgr._latest_costmap is not None

    def test_save_nonexistent_returns_false(self):
        mgr = PatrolMapManager()
        assert mgr._save_map_internal("test") is False

    def test_load_nonexistent_returns_false(self):
        mgr = PatrolMapManager(map_save_dir="/tmp/nonexistent_maps_test")
        assert mgr.load_map("nonexistent") is False

    def test_get_map_stats_no_data(self):
        mgr = PatrolMapManager()
        stats = mgr.get_map_stats()
        assert "error" in stats

    def test_get_map_stats_with_data(self):
        mgr = PatrolMapManager()
        mgr._latest_costmap = _make_costmap()
        stats = mgr.get_map_stats()
        assert "free_percent" in stats
        assert stats["width"] == 100

    def test_is_map_complete(self):
        mgr = PatrolMapManager(min_coverage_percent=50.0)
        mgr._latest_costmap = _make_costmap(free_pct=80.0)
        assert mgr.is_map_complete() is True

        mgr._latest_costmap = _make_costmap(free_pct=20.0)
        assert mgr.is_map_complete() is False

    def test_list_maps_empty(self, tmp_path):
        mgr = PatrolMapManager(map_save_dir=str(tmp_path))
        assert mgr.list_maps() == []


class TestPatrolMapManagerSkills:
    def test_save_current_map_skill(self, tmp_path):
        mgr = PatrolMapManager(map_save_dir=str(tmp_path))
        mgr._latest_costmap = _make_costmap()
        result = mgr.save_current_map("test_save")
        assert "Saved map 'test_save'" in result
        assert "80%" in result

    def test_save_current_map_no_data(self, tmp_path):
        mgr = PatrolMapManager(map_save_dir=str(tmp_path))
        result = mgr.save_current_map("test")
        assert "No map data" in result

    def test_load_saved_map_skill(self, tmp_path):
        mgr = PatrolMapManager(map_save_dir=str(tmp_path))
        mgr._latest_costmap = _make_costmap()
        mgr.save_map("test_load")

        result = mgr.load_saved_map("test_load")
        assert "Loaded map 'test_load'" in result

    def test_load_nonexistent_map_skill(self, tmp_path):
        mgr = PatrolMapManager(map_save_dir=str(tmp_path))
        result = mgr.load_saved_map("nonexistent")
        assert "not found" in result

    def test_check_map_coverage_skill(self):
        mgr = PatrolMapManager()
        mgr._latest_costmap = _make_costmap()
        result = mgr.check_map_coverage()
        assert "Map coverage" in result
        assert "free" in result

    def test_check_map_coverage_no_data(self):
        mgr = PatrolMapManager()
        result = mgr.check_map_coverage()
        assert "No map data" in result

    def test_list_available_maps_skill(self, tmp_path):
        mgr = PatrolMapManager(map_save_dir=str(tmp_path))
        result = mgr.list_available_maps()
        assert "No saved maps" in result

        mgr._latest_costmap = _make_costmap()
        mgr.save_map("map_a")
        result = mgr.list_available_maps()
        assert "map_a" in result
