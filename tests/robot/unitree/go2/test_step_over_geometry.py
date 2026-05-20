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

"""Unit tests for step-over geometry functions (pure functions, no I/O)."""

import numpy as np
import pytest

from dimos.robot.unitree.go2.step_over import (
    _estimate_ground_z,
    _extract_roi_height_map,
    _morphological_open,
    compute_step_over_geometry,
    is_passable,
)
from dimos.robot.unitree.go2.step_over_config import StepOverConfig


def _make_flat_ground(rows: int = 6, cols: int = 6) -> np.ndarray:
    """Create a flat ground height map at z=0 with all cells filled."""
    return np.full((rows, cols), 0.0, dtype=np.float32)


def _add_obstacle(
    height_map: np.ndarray,
    row_start: int,
    row_end: int,
    col_start: int,
    col_end: int,
    height: float,
) -> np.ndarray:
    """Add an obstacle block to the height map."""
    hm = height_map.copy()
    hm[row_start:row_end, col_start:col_end] = height
    return hm


def _cfg(**overrides) -> StepOverConfig:
    """Create a StepOverConfig, only setting StepOverConfig's own fields."""
    from dimos.core.module import ModuleConfig

    inherited = set(ModuleConfig.model_fields.keys())
    own_fields = set(StepOverConfig.model_fields.keys()) - inherited
    kw = {f: StepOverConfig.model_fields[f].default for f in own_fields}
    kw.update(overrides)
    return StepOverConfig(**kw)


# ── ground estimation ────────────────────────────────────────────────


class TestEstimateGroundZ:
    def test_flat_ground(self) -> None:
        hm = _make_flat_ground()
        assert _estimate_ground_z(hm) == pytest.approx(0.0)

    def test_ground_with_obstacle_10pct(self) -> None:
        hm = _make_flat_ground(10, 10)
        # Small obstacle in top 2 rows
        hm = _add_obstacle(hm, 0, 2, 0, 10, 0.08)
        z = _estimate_ground_z(hm)
        assert z == pytest.approx(0.0, abs=0.01)

    def test_empty_map(self) -> None:
        hm = np.full((5, 5), -np.inf, dtype=np.float32)
        assert _estimate_ground_z(hm) == 0.0


# ── ROI extraction ───────────────────────────────────────────────────


class TestRoiExtraction:
    def test_extracts_forward_strip(self) -> None:
        """Points directly ahead of the robot at yaw=0 are included."""
        # Generate 15 points in the ROI to exceed the minimum-point threshold
        xyz = np.array(
            [[0.35 + i * 0.015, np.sin(i) * 0.03, 0.0] for i in range(15)],
            dtype=np.float32,
        )
        hm = _extract_roi_height_map(xyz, (0.0, 0.0, 0.0), roi_start=0.25, roi_end=0.55, roi_half_width=0.2)
        assert hm is not None
        assert hm.shape[0] >= 1
        assert hm.shape[1] >= 1

    def test_excludes_behind_robot(self) -> None:
        xyz = np.array([[-0.5, 0.0, 0.0], [-0.3, 0.0, 0.0]], dtype=np.float32)
        hm = _extract_roi_height_map(xyz, (0.0, 0.0, 0.0), roi_start=0.25, roi_end=0.55, roi_half_width=0.2)
        assert hm is None  # too few points

    def test_respects_lateral_bounds(self) -> None:
        """Points far to the side are excluded."""
        xyz = np.array([[0.4, 0.5, 0.0], [0.4, -0.5, 0.0]], dtype=np.float32)
        hm = _extract_roi_height_map(xyz, (0.0, 0.0, 0.0), roi_start=0.25, roi_end=0.55, roi_half_width=0.2)
        assert hm is None

    def test_rotated_robot_frame(self) -> None:
        """Robot facing +Y (yaw=pi/2). Forward = +Y."""
        xyz = np.array(
            [[0.0, 0.35 + i * 0.015, 0.0] for i in range(15)],
            dtype=np.float32,
        )
        hm = _extract_roi_height_map(xyz, (0.0, 0.0, np.pi / 2), roi_start=0.25, roi_end=0.55, roi_half_width=0.2)
        assert hm is not None


# ── morphological opening ─────────────────────────────────────────────


class TestMorphologicalOpening:
    def test_removes_isolated_spike(self) -> None:
        hm = _make_flat_ground(10, 10)
        hm[1, 1] = 0.1  # single isolated high cell
        opened = _morphological_open(hm, iterations=1)
        # The spike should be removed (set to -inf)
        assert opened[1, 1] == -np.inf or opened[1, 1] < 0.1

    def test_preserves_large_obstacle(self) -> None:
        hm = _make_flat_ground(10, 10)
        hm = _add_obstacle(hm, 2, 8, 3, 7, 0.08)  # large 5x4 block
        opened = _morphological_open(hm, iterations=1)
        assert np.any(opened[4, 5] > 0.0)

    def test_zero_iterations_noop(self) -> None:
        hm = _make_flat_ground(6, 6)
        hm[2, 2] = 0.1
        opened = _morphological_open(hm, iterations=0)
        assert opened[2, 2] == 0.1


# ── geometry computation ──────────────────────────────────────────────


class TestComputeGeometry:
    def test_empty_map(self) -> None:
        hm = np.full((5, 5), -np.inf, dtype=np.float32)
        g = compute_step_over_geometry(hm, 0.0)
        assert g["h"] == 0.0
        assert g["num_obstacles"] == 0

    def test_flat_ground_no_obstacle(self) -> None:
        hm = _make_flat_ground(10, 10)
        g = compute_step_over_geometry(hm, 0.0, ignore_noise_m=0.05)
        assert g["h"] == 0.0
        assert g["num_obstacles"] == 0

    def test_8cm_kerb_T1(self) -> None:
        """T1: 8 cm kerb, should be detected at correct height."""
        hm = _make_flat_ground(10, 10)
        # obstacle spans entire width (continuous kerb), 3 cols deep (=0.15m at 5cm cells)
        hm = _add_obstacle(hm, 0, 10, 0, 3, 0.08)
        g = compute_step_over_geometry(hm, 0.0)
        assert g["h"] == pytest.approx(0.08)
        assert g["d"] == pytest.approx(0.15, abs=0.01)  # 3 contiguous cols at 5cm = 0.15m
        assert g["num_obstacles"] == 1

    def test_12cm_edge_T2(self) -> None:
        """T2: 12 cm edge, height exceeds max_step_height_m."""
        hm = _make_flat_ground(10, 10)
        hm = _add_obstacle(hm, 0, 10, 0, 2, 0.12)
        g = compute_step_over_geometry(hm, 0.0, max_step_height_m=0.10)
        assert g["h"] == pytest.approx(0.12)

    def test_ledge_too_deep_T3(self) -> None:
        """T3: 8cm height but ledge deeper than 20cm."""
        hm = _make_flat_ground(10, 10)
        hm = _add_obstacle(hm, 0, 10, 0, 6, 0.08)  # 6*5cm = 30cm deep, no gap
        g = compute_step_over_geometry(hm, 0.0)
        assert g["h"] == pytest.approx(0.08)
        # Continuous depth from first occupied column to first gap (or end).
        # 6 cols at 5cm = 0.30m, correctly exceeding max_ledge_depth_m (0.20m).
        assert g["d"] == pytest.approx(0.30, abs=0.01)
        # is_passable should reject this
        cfg = _cfg()
        belly = cfg.body_bottom_height_m - g["h"]
        assert is_passable(g, belly, cfg) is False

    def test_isolated_narrow_spike_T4(self) -> None:
        """T4: 5cm wide noise spike should produce small width."""
        hm = _make_flat_ground(10, 10)
        hm[5, 5] = 0.06  # single cell = 5cm width
        g = compute_step_over_geometry(hm, 0.0, ignore_noise_m=0.05)
        assert g["h"] > 0.0
        assert g["width"] <= 0.05

    def test_two_obstacles_with_gap(self) -> None:
        hm = _make_flat_ground(10, 15)
        hm = _add_obstacle(hm, 0, 10, 0, 3, 0.08)
        hm = _add_obstacle(hm, 0, 10, 9, 12, 0.08)  # 6 cols gap = 30cm
        g = compute_step_over_geometry(hm, 0.0)
        assert g["num_obstacles"] == 2
        assert g["gap"] == pytest.approx(0.30, abs=0.05)


# ── is_passable ───────────────────────────────────────────────────────


class TestIsPassable:
    def test_T1_8cm_kerb_passable(self) -> None:
        """T1: 8cm kerb, 12cm belly clearance → passable."""
        cfg = _cfg(body_bottom_height_m=0.22)
        g = {"h": 0.08, "d": 0.10, "width": 0.50, "gap": float("inf"), "num_obstacles": 1}
        belly = cfg.body_bottom_height_m - g["h"]
        assert is_passable(g, belly, cfg) is True

    def test_T2_12cm_not_passable(self) -> None:
        """T2: 12cm edge exceeds max_step_height_m → not passable."""
        cfg = _cfg()
        g = {"h": 0.12, "d": 0.05, "width": 0.30, "gap": float("inf"), "num_obstacles": 1}
        belly = cfg.body_bottom_height_m - g["h"]
        assert is_passable(g, belly, cfg) is False

    def test_belly_clearance_insufficient(self) -> None:
        """8cm obstacle but belly clearance only 8cm (< 12cm min) → not passable."""
        cfg = _cfg(body_bottom_height_m=0.16)  # low belly
        g = {"h": 0.08, "d": 0.05, "width": 0.20, "gap": float("inf"), "num_obstacles": 1}
        belly = cfg.body_bottom_height_m - g["h"]  # 0.16 - 0.08 = 0.08
        assert is_passable(g, belly, cfg) is False

    def test_ledge_too_deep_not_passable(self) -> None:
        cfg = _cfg()
        g = {"h": 0.06, "d": 0.25, "width": 0.30, "gap": float("inf"), "num_obstacles": 1}
        belly = cfg.body_bottom_height_m - g["h"]
        assert is_passable(g, belly, cfg) is False

    def test_narrow_spike_filtered_T4(self) -> None:
        """T4: 5cm wide spike → filtered as noise (width < min)."""
        cfg = _cfg()
        g = {"h": 0.06, "d": 0.05, "width": 0.03, "gap": float("inf"), "num_obstacles": 1}
        belly = cfg.body_bottom_height_m - g["h"]
        assert is_passable(g, belly, cfg) is True  # filtered → passable

    def test_gap_too_small_not_passable(self) -> None:
        cfg = _cfg()
        g = {"h": 0.06, "d": 0.05, "width": 0.15, "gap": 0.10, "num_obstacles": 2}
        belly = cfg.body_bottom_height_m - g["h"]
        assert is_passable(g, belly, cfg) is False

    def test_below_noise_floor_passable(self) -> None:
        cfg = _cfg()
        g = {"h": 0.02, "d": 0.05, "width": 0.20, "gap": float("inf"), "num_obstacles": 1}
        belly = cfg.body_bottom_height_m - g["h"]
        assert is_passable(g, belly, cfg) is True
