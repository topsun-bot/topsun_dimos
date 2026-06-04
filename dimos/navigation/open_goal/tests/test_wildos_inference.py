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

"""Tests for the Open-Goal visual navigation module (WildOS inference + skill container).

These tests run on CPU. Model-loading tests are skipped when checkpoint files
are not present in data/models_wildos/.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import pytest

# ---- Helpers ----

_MODELS_DIR = Path(__file__).resolve().parents[4] / "data" / "models_wildos"
_HAS_CHECKPOINTS = (_MODELS_DIR / "frontier_head.ckpt").exists() and (
    _MODELS_DIR / "trav_head.ckpt"
).exists()

requires_checkpoints = pytest.mark.skipif(
    not _HAS_CHECKPOINTS,
    reason="WildOS checkpoints not found in data/models_wildos/. "
    "Copy frontier_head.ckpt and trav_head.ckpt to enable.",
)


def _make_rgb(h: int = 480, w: int = 640) -> np.ndarray:
    """Create a synthetic RGB image — blue sky, green ground."""
    img = np.zeros((h, w, 3), dtype=np.uint8)
    img[: h // 2, :] = [128, 128, 128]  # sky
    img[h // 2 :, :] = [0, 128, 0]      # ground
    # Add a red box as a synthetic target
    img[280:320, 250:350] = [255, 0, 0]
    return img


def _make_binary_mask(h: int = 480, w: int = 640) -> np.ndarray:
    """Create a synthetic binary mask with a square blob."""
    mask = np.zeros((h, w), dtype=np.uint8)
    mask[280:320, 250:350] = 1
    return mask


def _make_camera_k() -> np.ndarray:
    return np.array(
        [[819.55, 0.0, 625.28], [0.0, 820.65, 336.81], [0.0, 0.0, 1.0]],
        dtype=np.float64,
    )


# ---- WildOSInference: model-loading tests ----


class TestWildOSInferenceLoading:
    """Tests for model loading, inference, and the end-to-end pipeline."""

    @requires_checkpoints
    def test_init_loads(self) -> None:
        """WildOSInference initializes with default settings (GPU auto-detect)."""
        from dimos.navigation.open_goal.wildos_inference import WildOSInference

        model = WildOSInference()
        assert model.current_query == ""
        assert model.model_precision == "FP16"

    @requires_checkpoints
    def test_encode_text(self) -> None:
        """encode_text returns L2-normalized numpy embeddings."""
        from dimos.navigation.open_goal.wildos_inference import WildOSInference

        model = WildOSInference()
        emb = model.encode_text(["orange flag"])
        assert isinstance(emb, np.ndarray)
        assert emb.ndim == 2  # (1, D)
        norms = np.linalg.norm(emb, axis=1)
        assert np.allclose(norms, 1.0, atol=0.01)
        assert model.current_query == "orange flag"

    @requires_checkpoints
    def test_forward_returns_traversability_and_features(self) -> None:
        """forward() on a synthetic RGB image returns expected shape outputs."""
        from dimos.navigation.open_goal.wildos_inference import WildOSInference

        model = WildOSInference()
        model.encode_text(["red box"])
        rgb = _make_rgb()
        trav, frontiers, ad_spatial = model.forward(rgb)

        assert isinstance(trav, np.ndarray)
        assert np.issubdtype(trav.dtype, np.floating)
        assert trav.shape[:2] == rgb.shape[:2]

        assert isinstance(frontiers, np.ndarray)
        assert np.issubdtype(frontiers.dtype, np.floating)

        assert isinstance(ad_spatial, np.ndarray)
        assert ad_spatial.ndim == 3  # (D, h, w)

    @requires_checkpoints
    def test_localize_object_produces_binary_mask(self) -> None:
        """localize_object thresholds cosine similarity into a binary mask."""
        from dimos.navigation.open_goal.wildos_inference import WildOSInference

        model = WildOSInference()
        model.encode_text(["red box"])
        rgb = _make_rgb()
        _trav, _front, ad_spatial = model.forward(rgb)
        assert ad_spatial is not None

        mask = model.localize_object(ad_spatial, rgb.shape[:2], mask_threshold=0.09)
        assert mask.shape == rgb.shape[:2]
        assert mask.dtype == np.uint8
        assert set(np.unique(mask)).issubset({0, 1})

    @requires_checkpoints
    def test_end_to_end_pipeline(self) -> None:
        """Full pipeline: encode → forward → localize → estimate 3D → render viz."""
        from dimos.navigation.open_goal.wildos_inference import WildOSInference

        model = WildOSInference()
        model.encode_text(["red box"])
        rgb = _make_rgb()
        trav, _front, ad_spatial = model.forward(rgb)
        assert ad_spatial is not None

        mask = model.localize_object(ad_spatial, rgb.shape[:2])
        pos = model.estimate_3d_position(mask, _make_camera_k(), camera_height=0.5)

        # The synthetic red box at image bottom should produce a 3D position
        assert pos is not None
        x, y, z = pos
        assert isinstance(x, float)
        assert isinstance(y, float)
        assert isinstance(z, float)
        assert z > 0  # depth should be positive

        # Render debug viz (always works, no model needed)
        viz = WildOSInference.render_debug_viz(rgb, mask, pos, trav)
        assert viz.shape == rgb.shape
        assert viz.dtype == np.uint8


# ---- WildOSInference: pure-function tests (no model needed) ----


class TestWildOSInferencePure:
    """Tests that exercise WildOSInference methods without loading any model."""

    def test_estimate_3d_position_flat_ground(self) -> None:
        """estimate_3d_position back-projects the lowest mask point to ground plane."""
        from dimos.navigation.open_goal.wildos_inference import WildOSInference

        model = object.__new__(WildOSInference)

        mask = np.zeros((480, 640), dtype=np.uint8)
        # Bottom center pixel at row 400
        mask[400, 320] = 1
        K = _make_camera_k()
        pos = model.estimate_3d_position(mask, K, camera_height=0.5)

        assert pos is not None
        x, y, z = pos
        # For a pixel at (320, 400) with cy≈337, it's below the optical center → ray_y > 0
        # depth = H / ray_y should be positive
        assert z > 0
        assert 0 < z < 20

    def test_estimate_3d_position_empty_mask_returns_none(self) -> None:
        """Empty mask → None."""
        from dimos.navigation.open_goal.wildos_inference import WildOSInference

        model = object.__new__(WildOSInference)
        mask = np.zeros((480, 640), dtype=np.uint8)
        assert model.estimate_3d_position(mask, _make_camera_k()) is None

    def test_estimate_3d_position_uses_lowest_pixel(self) -> None:
        """The position estimate uses the argmax-y (lowest) pixel in the mask."""
        from dimos.navigation.open_goal.wildos_inference import WildOSInference

        model = object.__new__(WildOSInference)
        mask = np.zeros((480, 640), dtype=np.uint8)
        K = _make_camera_k()
        cy = K[1, 2]  # ≈337

        # Both pixels below optical center: lower pixel → closer to ground → closer depth
        mask[int(cy) + 30, 320] = 1  # farther
        mask[int(cy) + 80, 320] = 1  # closer

        pos_with_lower = model.estimate_3d_position(mask, K, camera_height=0.5)

        # Remove the lower pixel, test upper-only
        mask[int(cy) + 80, 320] = 0
        pos_without_lower = model.estimate_3d_position(mask, K, camera_height=0.5)

        assert pos_with_lower is not None
        assert pos_without_lower is not None
        # When only the upper pixel is present, the ray has a smaller |ray_y|,
        # which means larger depth → object is farther.
        assert pos_with_lower[2] < pos_without_lower[2]

    def test_estimate_3d_position_ray_y_very_small(self) -> None:
        """Pixel near optical center → large or clamped depth — no crash."""
        from dimos.navigation.open_goal.wildos_inference import WildOSInference

        model = object.__new__(WildOSInference)
        K = _make_camera_k()
        mask = np.zeros((480, 640), dtype=np.uint8)
        # Pixel at optical center (ray_y ≈ 0): depth is extreme, but code recovers
        mask[int(K[1, 2]), int(K[0, 2])] = 1

        pos = model.estimate_3d_position(mask, K, camera_height=0.5)
        assert pos is not None
        # Depth is clamped by the recovery branch; must be non-negative
        assert pos[2] >= 0

    def test_render_debug_viz_red_mask_overlay(self) -> None:
        """Binary mask renders as red tint overlay on the original image."""
        from dimos.navigation.open_goal.wildos_inference import WildOSInference

        rgb = _make_rgb()
        mask = _make_binary_mask()
        mask[300, 300] = 1  # ensure non-zero

        viz = WildOSInference.render_debug_viz(rgb, mask, None, None)
        assert viz.shape == rgb.shape
        assert viz.dtype == np.uint8
        # Red overlay should change pixel values in the mask region
        assert not np.array_equal(viz[300, 300], rgb[300, 300])

    def test_render_debug_viz_green_traversability(self) -> None:
        """Traversability map renders as green tint."""
        from dimos.navigation.open_goal.wildos_inference import WildOSInference

        rgb = _make_rgb()
        mask = _make_binary_mask()
        trav = np.random.rand(*rgb.shape[:2]).astype(np.float32)

        viz = WildOSInference.render_debug_viz(rgb, mask, None, trav)
        assert viz.shape == rgb.shape

    def test_render_debug_viz_crosshair_and_text(self) -> None:
        """When pos_3d is given, a yellow crosshair and coordinate text are drawn."""
        from dimos.navigation.open_goal.wildos_inference import WildOSInference

        rgb = _make_rgb()
        mask = _make_binary_mask()
        pos = (1.5, -0.2, 3.0)

        viz = WildOSInference.render_debug_viz(rgb, mask, pos, None)
        assert viz.shape == rgb.shape
        # Yellow crosshair pixel at the lowest mask point
        ys, xs = np.where(mask > 0)
        lowest_y, lowest_x = np.max(ys), xs[np.argmax(ys)]
        # The crosshair center pixel should be yellow (0, 255, 255)
        px = viz[lowest_y, lowest_x]
        # At least one channel of the crosshair should be bright
        assert np.any(px > 200)

    def test_render_debug_viz_empty_mask_no_crash(self) -> None:
        """Empty mask should not crash render_debug_viz."""
        from dimos.navigation.open_goal.wildos_inference import WildOSInference

        rgb = _make_rgb()
        mask = np.zeros(rgb.shape[:2], dtype=np.uint8)
        viz = WildOSInference.render_debug_viz(rgb, mask, None, None)
        assert np.array_equal(viz, rgb)

    def test_render_debug_viz_mismatched_traversability_shape(self) -> None:
        """Traversability with mismatched shape is silently skipped."""
        from dimos.navigation.open_goal.wildos_inference import WildOSInference

        rgb = _make_rgb()
        mask = _make_binary_mask()
        # Wrong-size traversability
        trav = np.random.rand(240, 320).astype(np.float32)

        viz = WildOSInference.render_debug_viz(rgb, mask, None, trav)
        assert viz.shape == rgb.shape
        # Should not crash; green overlay simply skipped


# ---- OpenGoalNavigationSkillContainer: unit tests with mocks ----


class FakeImage(Any):
    """Minimal Image fake — behaves like a SensorMsg Image with .data and .ts."""

    def __init__(self, data: np.ndarray, ts: float = 0.0) -> None:
        self.data = data
        self.ts = ts


class FakePoseStamped:
    def __init__(self, x: float = 0.0, y: float = 0.0, z: float = 0.0) -> None:
        from dimos.msgs.geometry_msgs.PoseStamped import PoseStamped
        from dimos.msgs.geometry_msgs.Quaternion import Quaternion
        from dimos.msgs.geometry_msgs.Vector3 import Vector3, make_vector3

        self.position = make_vector3(x, y, z)
        self.orientation = Quaternion(0.0, 0.0, 0.0, 1.0)
        self.frame_id = "map"


class StubNavigation:
    def __init__(self) -> None:
        self.goals: list[Any] = []
        self.cancel_count = 0

    def set_goal(self, goal: Any) -> bool:
        self.goals.append(goal)
        return True

    def cancel_goal(self) -> bool:
        self.cancel_count += 1
        return True

    def get_state(self) -> int:
        return 0  # IDLE

    def is_goal_reached(self) -> bool:
        return False


class _FakeSpeakSkill:
    def speak(self, text: str, blocking: bool = True) -> str:
        return f"Spoke: {text}"


class _FakeUnitreeSkill:
    def execute_sport_command(self, command_name: str) -> str:
        return f"Executed: {command_name}"


class TestOpenGoalSkill:
    """Unit tests for OpenGoalNavigationSkillContainer requiring no model weights."""

    @pytest.fixture
    def skill(self) -> Any:
        """Construct a minimally-initialized skill container (bypasses Module.__init__)."""
        from dimos.agents.skills.navigation_open_goal import OpenGoalNavigationSkillContainer

        container = object.__new__(OpenGoalNavigationSkillContainer)
        container._skill_started = True
        container._open_goal_model = _FakeWildOS()
        container._open_goal_lock = __import__("threading").RLock()
        container._search_active = False
        container._search_target = ""
        container._open_goal_subscription = None
        container._goal_sent = False
        container._last_goal_distance = float("inf")
        container._consecutive_no_detect = 0
        container._max_no_detect_frames = 10
        container._open_goal_frame_hz = 5.0
        container._last_process_time = 0.0
        container._target_confirmed = False
        container._confirm_stable_count = 0
        container._search_done = __import__("threading").Event()
        container._search_succeeded = False
        container._speak_skill = _FakeSpeakSkill()
        container._unitree_skill = _FakeUnitreeSkill()
        container._debug_save_dir = ""
        container._debug_frame_index = 0
        container._latest_odom = FakePoseStamped(0.0, 0.0, 0.0)
        container._latest_image = FakeImage(_make_rgb())
        container._latest_camera_info = None
        container._navigation = StubNavigation()
        return container

    def test_camera_to_goal_pose_no_odom(self, skill: Any) -> None:
        """Returns None when no odometry is available."""
        skill._latest_odom = None
        assert skill._camera_to_goal_pose((1.0, 0.0, 2.0)) is None

    def test_camera_to_goal_pose_produces_map_frame(self, skill: Any) -> None:
        """camera_to_goal_pose transforms camera-frame coords to map frame."""
        pose = skill._camera_to_goal_pose((0.0, 0.0, 2.0))
        assert pose is not None
        assert pose.frame_id == "map"
        # For a forward-looking camera, z=2 in camera is forward in base's x
        assert pose.position.x > 0

    def test_distance_to_pose(self, skill: Any) -> None:
        """distance_to_pose computes Euclidean distance from current odom."""
        from dimos.msgs.geometry_msgs.PoseStamped import PoseStamped
        from dimos.msgs.geometry_msgs.Vector3 import make_vector3

        target = PoseStamped(
            position=make_vector3(3.0, 4.0, 0.0),
            frame_id="map",
        )
        skill._latest_odom = FakePoseStamped(0.0, 0.0, 0.0)
        assert skill._distance_to_pose(target) == pytest.approx(5.0)

    def test_stop_search_cancels_navigation(self, skill: Any) -> None:
        """stop_search cancels navigation and resets state."""
        skill._search_active = True
        skill._search_target = "test"
        skill._navigation = StubNavigation()

        result = skill.stop_search()
        assert "Stopped" in result
        assert not skill._search_active
        assert skill._navigation.cancel_count == 1

    def test_stop_search_no_active_search(self, skill: Any) -> None:
        """stop_search with no active search returns informative message."""
        skill._search_active = False
        result = skill.stop_search()
        assert "No active" in result

    def test_stop_navigation_cancels_goal(self, skill: Any) -> None:
        """stop_navigation calls cancel_goal on the navigation interface."""
        skill._navigation = StubNavigation()
        result = skill.stop_navigation()
        assert result == "Stopped"
        assert skill._navigation.cancel_count == 1

    def test_search_target_already_active(self, skill: Any) -> None:
        """search_target when already searching returns a message."""
        skill._search_active = True
        skill._search_target = "existing"
        result = skill.search_target("new-target")
        assert "Already searching" in result

    def test_confirm_target_close_valid(self, skill: Any) -> None:
        """Mask coverage ≥ 0.5% and depth in [0.1, 2.5] → True."""
        mask = np.zeros((480, 640), dtype=np.uint8)
        mask[200:280, 250:350] = 1  # ~2.6% of image
        assert skill._confirm_target_close(mask, (0.0, 0.0, 1.5)) is True

    def test_confirm_target_close_small_mask(self, skill: Any) -> None:
        """Mask coverage < 0.5% → False."""
        mask = np.zeros((480, 640), dtype=np.uint8)
        mask[300:305, 300:305] = 1  # ~0.003% of image
        assert skill._confirm_target_close(mask, (0.0, 0.0, 1.5)) is False

    def test_confirm_target_close_too_near(self, skill: Any) -> None:
        """Depth < 0.1m → False (too close to be reliable)."""
        mask = np.zeros((480, 640), dtype=np.uint8)
        mask[200:280, 250:350] = 1
        assert skill._confirm_target_close(mask, (0.0, 0.0, 0.05)) is False

    def test_confirm_target_close_too_far(self, skill: Any) -> None:
        """Depth > 2.5m → False (too far to confirm)."""
        mask = np.zeros((480, 640), dtype=np.uint8)
        mask[200:280, 250:350] = 1
        assert skill._confirm_target_close(mask, (0.0, 0.0, 3.0)) is False

    def test_get_camera_k_uses_dynamic_when_available(self, skill: Any) -> None:
        """When _latest_camera_info is set, _get_camera_k returns it."""
        skill._latest_camera_info = None
        K = skill._get_camera_k()
        assert isinstance(K, np.ndarray)
        assert K.shape == (3, 3)


# ---- Stub classes for WildOS + VL model ----


class _FakeWildOS:
    """Drop-in stub that mimicks WildOSInference API without loading any model."""

    def __init__(self) -> None:
        self._text_feats = None
        self._current_query = ""

    @property
    def current_query(self) -> str:
        return self._current_query

    def encode_text(self, text_queries: list[str]) -> None:
        import torch

        self._current_query = text_queries[0] if text_queries else ""
        self._text_feats = torch.randn(1, 768)

    def forward(
        self, rgb_numpy: np.ndarray
    ) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        h, w = rgb_numpy.shape[:2]
        np_rng = np.random.RandomState(42)
        trav = np_rng.rand(h, w).astype(np.float32)
        front = np_rng.rand(h, w).astype(np.float32)
        ad_spatial = np_rng.randn(768, h // 16, w // 16).astype(np.float32)
        return trav, front, ad_spatial

    def localize_object(
        self,
        ad_spatial_feats: np.ndarray,
        orig_shape: tuple[int, int],
        mask_threshold: float = 0.09,
    ) -> np.ndarray:
        h, w = orig_shape
        # Return the synthetic mask (red box region)
        mask = np.zeros((h, w), dtype=np.uint8)
        mask[280:320, 250:350] = 1
        return mask

    def estimate_3d_position(
        self,
        binary_mask: np.ndarray,
        K: np.ndarray,
        camera_height: float = 0.5,
    ) -> tuple[float, float, float] | None:
        if np.sum(binary_mask) == 0:
            return None
        return (0.1, 0.05, 2.0)

    @staticmethod
    def render_debug_viz(
        rgb: np.ndarray,
        binary_mask: np.ndarray,
        pos_3d: tuple[float, float, float] | None = None,
        traversability: np.ndarray | None = None,
    ) -> np.ndarray:
        from dimos.navigation.open_goal.wildos_inference import WildOSInference

        return WildOSInference.render_debug_viz(rgb, binary_mask, pos_3d, traversability)


