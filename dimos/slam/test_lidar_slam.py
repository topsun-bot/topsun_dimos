# Copyright 2025-2026 Dimensional Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");

import numpy as np
import pytest

from dimos.msgs.geometry_msgs import PoseStamped, Quaternion
from dimos.msgs.geometry_msgs.Vector3 import make_vector3
from dimos.slam.lidar_slam import LidarSlamModule, _matrix_to_pose, _pose_to_matrix
from dimos.slam.types import PoseGraphNode, ScanMatchResult


def _make_pose(x: float, y: float, z: float = 0.0) -> PoseStamped:
    return PoseStamped(
        position=make_vector3(x, y, z),
        orientation=Quaternion(0.0, 0.0, 0.0, 1.0),
        frame_id="map",
    )


class TestPoseMatrixConversion:
    def test_identity_roundtrip(self):
        pose = _make_pose(0, 0, 0)
        T = _pose_to_matrix(pose)
        result = _matrix_to_pose(T)
        assert abs(result.position.x) < 1e-6
        assert abs(result.position.y) < 1e-6

    def test_translation_preserved(self):
        pose = _make_pose(1.5, 2.3, 0.1)
        T = _pose_to_matrix(pose)
        result = _matrix_to_pose(T)
        assert abs(result.position.x - 1.5) < 1e-6
        assert abs(result.position.y - 2.3) < 1e-6
        assert abs(result.position.z - 0.1) < 1e-6


class TestScanMatchResult:
    def test_creation(self):
        r = ScanMatchResult(fitness=0.9, rmse=0.01, transformation=np.eye(4))
        assert r.fitness == 0.9
        assert r.transformation.shape == (4, 4)


class TestPoseGraphNode:
    def test_creation(self):
        node = PoseGraphNode(index=0, pose=_make_pose(1, 2), cumulative_transform=np.eye(4))
        assert node.index == 0


class TestLidarSlamModule:
    def test_instantiation(self):
        mod = LidarSlamModule(voxel_size=0.1)
        assert mod._voxel_size == 0.1

    def test_initial_state(self):
        mod = LidarSlamModule()
        metrics = mod.get_quality_metrics()
        assert metrics["avg_fitness"] == 0.0
        assert metrics["keyframe_count"] == 0

    def test_reset(self):
        mod = LidarSlamModule()
        mod._scan_count = 10
        mod._total_fitness = 5.0
        mod._match_count = 5
        result = mod.reset()
        assert result is True
        metrics = mod.get_quality_metrics()
        assert metrics["scan_count"] == 0

    def test_check_localization_quality_skill(self):
        mod = LidarSlamModule()
        result = mod.check_localization_quality()
        assert isinstance(result, str)
        assert len(result) > 0

    def test_get_pose_initial(self):
        mod = LidarSlamModule()
        pose = mod.get_pose()
        assert isinstance(pose, PoseStamped)

    def test_to_o3d_with_numpy(self):
        mod = LidarSlamModule()
        pts = np.array([[1, 2, 3], [4, 5, 6]], dtype=np.float32)
        result = mod._to_o3d(pts)
        assert result is not None

    def test_to_o3d_with_none(self):
        mod = LidarSlamModule()
        assert mod._to_o3d(None) is None
