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

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np

from dimos.utils.data import get_data

if TYPE_CHECKING:
    import torch


_WILDOS_DATA_DIR: Path | None = None


def _get_wildos_data_dir() -> Path:
    """Lazy-resolve the models_wildos data directory. Raises if not found."""
    global _WILDOS_DATA_DIR
    if _WILDOS_DATA_DIR is not None:
        return _WILDOS_DATA_DIR
    _WILDOS_DATA_DIR = get_data("models_wildos")
    return _WILDOS_DATA_DIR


class WildOSInference:
    """Encapsulates the WildOS ExploRFMInference model for open-vocabulary object search.

    Loads the RADIO backbone, traversability head, frontier head, and SigLIP2
    text encoder. Supports per-frame inference on numpy RGB images and
    ground-plane-based 3D position estimation from object masks.
    """

    def __init__(
        self,
        ckpt_dir: str | None = None,
        model_version: str = "c-radio_v3-b",
        model_precision: str = "FP16",
        device: str | None = None,
    ) -> None:
        import torch
        from .explorfm import ExploRFMInference

        _ckpt_dir = Path(ckpt_dir) if ckpt_dir else _get_wildos_data_dir()
        self._device = device or ("cuda" if torch.cuda.is_available() else "cpu")
        self.model_precision = model_precision

        # Prefer a local .pth.tar over downloading from HuggingFace.
        _radio_ckpt = _ckpt_dir / f"{model_version}_half.pth.tar"
        _model_version: str = str(_radio_ckpt) if _radio_ckpt.is_file() else model_version

        self._model = ExploRFMInference(
            frontier_ckpt=str(_ckpt_dir / "frontier_head.ckpt"),
            traversability_ckpt=str(_ckpt_dir / "trav_head.ckpt"),
            model_version=_model_version,
            adaptor_version="siglip2",
            adaptor_ckpt_path=str(_ckpt_dir),
            use_naclip=True,
            use_summary_for_spatial=True,
            radio_dim=768,
            static_scale_factor=0.75,
            model_precision=model_precision,
            device=self._device,
        )

        self._text_feats: torch.Tensor | None = None
        self._current_query: str = ""

    @property
    def device(self) -> str:
        return self._device

    def encode_text(self, text_queries: list[str]) -> np.ndarray:
        """Encode text queries via SigLIP2 text encoder. Call once per target.

        Returns L2-normalized text embeddings as numpy array (num_queries, D).
        """
        import torch

        self._current_query = text_queries[0] if text_queries else ""
        with torch.no_grad():
            text_feats = self._model.forward_on_text(text_queries)
        self._text_feats = text_feats
        return text_feats.cpu().numpy()

    def forward(
        self, rgb_numpy: np.ndarray
    ) -> tuple[np.ndarray, np.ndarray, np.ndarray | None]:
        """Run per-frame inference on a single RGB image.

        Args:
            rgb_numpy: (H, W, 3) uint8 numpy array in RGB order.

        Returns:
            Tuple of (traversability, frontiers, ad_spatial_features):
            - traversability: (H, W) float32 in [0, 1], 1 = traversable
            - frontiers: (H, W) float32 in [0, 1], 1 = frontier pixel
            - ad_spatial_features: (D, h, w) float32 or None (no adaptor)
        """
        import torch

        img_norm = rgb_numpy.astype(np.float32) / 255.0
        tensor = torch.from_numpy(img_norm).permute(2, 0, 1).unsqueeze(0)  # (1, 3, H, W)

        with torch.no_grad():
            traversability, frontiers, ad_spatial = self._model.forward(tensor)

        trav_np = traversability.squeeze(0).squeeze(0).cpu().numpy()
        front_np = frontiers.squeeze(0).squeeze(0).cpu().numpy()

        ad_np = None
        if ad_spatial is not None:
            ad_np = ad_spatial.squeeze(0).cpu().numpy()

        return trav_np, front_np, ad_np

    def localize_object(
        self,
        ad_spatial_feats: np.ndarray,
        orig_shape: tuple[int, int],
        mask_threshold: float = 0.09,
    ) -> np.ndarray:
        """Compute text-image similarity → binary object mask.

        Args:
            ad_spatial_feats: (D, h, w) SigLIP2-adapted spatial features.
            orig_shape: (H, W) original image shape.
            mask_threshold: Cosine similarity threshold for binary mask.

        Returns:
            (H, W) uint8 binary mask. 1 = object pixel.
        """
        import torch
        import torch.nn.functional as F

        if self._text_feats is None:
            raise RuntimeError("No text features encoded. Call encode_text() first.")

        spatial = torch.from_numpy(ad_spatial_feats).unsqueeze(0).to(self._device)  # (1, D, h, w)
        if self._model.model_precision.is_fp16():
            spatial = spatial.half()

        spatial = spatial / spatial.norm(dim=1, keepdim=True)

        text_feats_fp = self._text_feats.to(spatial.device).to(spatial.dtype)

        sim = torch.einsum("qc,bchw->bqhw", text_feats_fp, spatial)  # (1, Q, h, w)
        sim = F.interpolate(sim, size=orig_shape, mode="nearest")

        sim_np = sim.squeeze(0).squeeze(0).cpu().numpy()  # (H, W)
        binary_mask = (sim_np > mask_threshold).astype(np.uint8)
        return binary_mask

    def estimate_3d_position(
        self,
        binary_mask: np.ndarray,
        K: np.ndarray,
        camera_height: float = 0.5,
    ) -> tuple[float, float, float] | None:
        """Estimate 3D world position from object mask via ground-plane intersection.

        Assumes the object lies on the ground plane (z = -camera_height in world
        frame). The camera is assumed to be pointing forward with its optical
        axis roughly horizontal.

        Args:
            binary_mask: (H, W) uint8 binary mask from localize_object().
            K: (3, 3) camera intrinsics matrix.
            camera_height: Height of camera above ground in meters.

        Returns:
            (x, y, z=0) in world frame (camera optical frame), or None if mask empty.
        """
        ys, xs = np.where(binary_mask > 0)
        if len(xs) == 0:
            return None

        # Use the lowest point in the mask (closest to ground)
        lowest_idx = np.argmax(ys)
        u, v = xs[lowest_idx], ys[lowest_idx]

        # Back-project to bearing ray in camera frame
        fx, fy, cx, cy = K[0, 0], K[1, 1], K[0, 2], K[1, 2]
        ray_x = (u - cx) / fx
        ray_y = (v - cy) / fy
        ray_z = 1.0

        # For camera optical frame: z forward, x right, y down
        # Ground plane in camera frame: z = camera_height at y direction
        # Actually in the optical frame: x=right, y=down, z=forward
        # The ground plane is y = camera_height at the camera's position
        # Ray: (ray_x * t, ray_y * t, ray_z * t)
        # Ground: the ray's y component at t should hit the ground
        # But we need the ground plane equation.
        # In camera optical frame, looking roughly horizontal:
        # Ground plane: the camera is at height h above ground
        # A pixel at (u,v) with v > cy is below the horizon → looks at ground
        # Intersection: at distance d, the point is d*(ray_x, ray_y, 1)/|ray|
        # The y component (down) at distance d = d*ray_y/|ray|
        # When d*ray_y/|ray| = h (camera_height), we hit ground
        # So d = h * |ray| / ray_y
        # Then ground point = d * (ray_x, ray_y, 1) / |ray| = h * (ray_x/ray_y, 1, 1/ray_y)

        if abs(ray_y) < 1e-6:
            # Ray is horizontal, use default depth
            depth = 3.0
        else:
            depth = camera_height / ray_y

        if depth <= 0 or depth > 50:
            # Unreasonable depth, use center of mass with default depth
            center_u = np.mean(xs)
            center_v = np.mean(ys)
            ray_x = (center_u - cx) / fx
            ray_y = (center_v - cy) / fy
            if abs(ray_y) < 1e-6:
                depth = 3.0
            else:
                depth = camera_height / ray_y
            depth = max(0.5, min(depth, 20.0))

        x_cam = ray_x * depth
        y_cam = ray_y * depth
        z_cam = depth

        return (float(x_cam), float(y_cam), float(z_cam))

    @property
    def current_query(self) -> str:
        return self._current_query

    @staticmethod
    def render_debug_viz(
        rgb: np.ndarray,
        binary_mask: np.ndarray,
        pos_3d: tuple[float, float, float] | None = None,
        traversability: np.ndarray | None = None,
    ) -> np.ndarray:
        """Render debug visualization overlays onto a copy of the input image.

        Args:
            rgb: (H, W, 3) uint8 RGB image.
            binary_mask: (H, W) uint8 binary object mask.
            pos_3d: Optional (x, y, z) in camera frame — draws a crosshair at the
                    projected pixel of the estimated 3D position.
            traversability: Optional (H, W) float32 traversability map — blended
                            as a green overlay.

        Returns:
            (H, W, 3) uint8 RGB image with overlays drawn.
        """
        import cv2

        viz = rgb.copy()

        # --- Binary mask overlay (red tint) ---
        if binary_mask is not None and np.sum(binary_mask) > 0:
            mask_bool = binary_mask > 0
            overlay = viz.copy()
            overlay[mask_bool] = (0, 0, 255)
            viz = cv2.addWeighted(viz, 0.6, overlay, 0.4, 0)

            # Draw mask contours
            contours, _ = cv2.findContours(
                binary_mask.astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
            )
            cv2.drawContours(viz, contours, -1, (255, 0, 0), 2)

        # --- Traversability overlay (green tint) ---
        if traversability is not None and traversability.shape[:2] == rgb.shape[:2]:
            trav_viz = (traversability * 255).astype(np.uint8)
            trav_color = np.zeros_like(viz)
            trav_color[:, :, 1] = trav_viz  # Green channel
            viz = cv2.addWeighted(viz, 0.7, trav_color, 0.3, 0)

        # --- Crosshair at estimated 3D position ---
        if pos_3d is not None and binary_mask is not None and np.sum(binary_mask) > 0:
            ys, xs = np.where(binary_mask > 0)
            if len(xs) > 0:
                # Project: use the lowest (closest-to-ground) pixel
                idx = np.argmax(ys)
                cx_mark, cy_mark = xs[idx], ys[idx]
                color = (0, 255, 255)  # Yellow crosshair
                cv2.drawMarker(viz, (cx_mark, cy_mark), color, cv2.MARKER_CROSS, 30, 2)
                cv2.putText(
                    viz,
                    f"({pos_3d[0]:.1f},{pos_3d[1]:.1f},{pos_3d[2]:.1f})m",
                    (cx_mark + 15, cy_mark - 10),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.5,
                    color,
                    1,
                )

        return viz
