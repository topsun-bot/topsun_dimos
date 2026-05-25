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
Door detection module — YOLO proposals + CLIP verification.

Pipeline::

    camera frame
        │
        ▼
    YOLO detection ──► bbox proposals
        │
        ▼
    CLIP zero-shot: "door" vs "not door"  ──► filter
        │
        ▼
    CLIP zero-shot: "open" vs "closed"     ──► state
        │
        ▼
    fuse with odometry ──► SpatialRecord ──► SpatialLandmarkMemory
"""

from __future__ import annotations

import time
from typing import Any

import cv2
import numpy as np
from PIL import Image as PILImage
import torch

from dimos.perception.detection.detectors.yolo import Yolo2DDetector
from dimos.perception.detection.door.door_spatial_memory import SpatialLandmarkMemory
from dimos.types.spatial_record import RecordType, SpatialRecord

# CLIP zero-shot text prompts
_DOOR_PROMPTS = ["door", "french door", "sliding door", "gate"]
_NEGATIVE_PROMPTS = ["wall", "window", "cabinet", "bookshelf", "picture", "person"]
_OPEN_CLOSE_PROMPTS = ["a door that is open", "a door that is closed"]


class DoorDetector:
    """Detects doors in camera frames using YOLO proposals + CLIP verification.

    Args:
        clip_model: A :class:`dimos.models.embedding.clip.CLIPModel` instance.
        yolo_kwargs: Extra kwargs forwarded to ``Yolo2DDetector``.
        door_threshold: Minimum CLIP similarity score for "door" class.
        min_bbox_ratio: Minimum bbox area as fraction of total frame area.
    """

    def __init__(
        self,
        clip_model: Any,
        yolo_kwargs: dict[str, Any] | None = None,
        door_threshold: float = 0.22,
        min_bbox_ratio: float = 0.005,
    ) -> None:
        self._clip = clip_model
        self._yolo = Yolo2DDetector(**(yolo_kwargs or {}))
        self._door_threshold = door_threshold
        self._min_bbox_ratio = min_bbox_ratio

        # Pre-compute text embeddings at init time
        all_prompts = _DOOR_PROMPTS + _NEGATIVE_PROMPTS
        self._door_embs = self._encode_texts(all_prompts)
        self._door_is_positive = [True] * len(_DOOR_PROMPTS) + [False] * len(_NEGATIVE_PROMPTS)

        self._state_embs = self._encode_texts(_OPEN_CLOSE_PROMPTS)

    # ------------------------------------------------------------------
    # Internal: CLIP encoding helpers
    # ------------------------------------------------------------------

    def _encode_texts(self, texts: list[str]) -> torch.Tensor:
        inputs = self._clip._processor(
            text=texts, return_tensors="pt", padding=True, truncation=True
        ).to(self._clip.config.device)
        with torch.inference_mode():
            features = self._clip._model.get_text_features(**inputs)
            if self._clip.config.normalize:
                features = torch.nn.functional.normalize(features, dim=-1)
        return features

    def _encode_images(self, pil_images: list[PILImage.Image]) -> torch.Tensor:
        inputs = self._clip._processor(images=pil_images, return_tensors="pt").to(
            self._clip.config.device
        )
        with torch.inference_mode():
            features = self._clip._model.get_image_features(**inputs)
            if self._clip.config.normalize:
                features = torch.nn.functional.normalize(features, dim=-1)
        return features

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def detect(self, frame: np.ndarray, frame_id: str = "") -> list[dict[str, Any]]:
        """Run detection pipeline on a single RGB frame.

        Args:
            frame: RGB image (H, W, 3) as numpy array.
            frame_id: Optional identifier for the frame.

        Returns:
            List of detection dicts with keys:
                bbox, confidence, door_state, cropped_bytes.
        """
        h, w = frame.shape[:2]
        frame_area = h * w

        # 1. YOLO proposals
        yolo_results = self._yolo.model(frame, verbose=False)
        proposals: list[dict[str, Any]] = []
        for result in yolo_results:
            if result.boxes is None:
                continue
            for i in range(len(result.boxes.xyxy)):
                if float(result.boxes.conf[i].cpu()) < 0.25:
                    continue
                x1, y1, x2, y2 = result.boxes.xyxy[i].cpu().tolist()
                if (x2 - x1) * (y2 - y1) / frame_area < self._min_bbox_ratio:
                    continue
                cropped = frame[int(y1) : int(y2), int(x1) : int(x2)]
                if cropped.size == 0:
                    continue
                proposals.append({
                    "bbox": (x1, y1, x2, y2),
                    "cropped": cropped,
                })

        if not proposals:
            return []

        # 2. CLIP verification — classify each crop as door vs not-door
        pil_crops = [PILImage.fromarray(p["cropped"]) for p in proposals]
        image_embs = self._encode_images(pil_crops)  # (N, D)

        # Cosine similarity to door / non-door text prompts
        sims = image_embs @ self._door_embs.T  # (N, len(prompts))

        results: list[dict[str, Any]] = []
        for idx in range(len(proposals)):
            best_idx = int(sims[idx].argmax().cpu())
            score = float(sims[idx, best_idx].cpu())

            if not self._door_is_positive[best_idx] or score < self._door_threshold:
                continue

            # 3. Door state (open vs closed)
            state_sim = image_embs[idx] @ self._state_embs.T
            state_idx = int(state_sim.argmax().cpu())
            door_state = "open" if state_idx == 0 else "closed"

            # Encode cropped region as JPEG for saving to disk
            _, jpeg_bytes = cv2.imencode(
                ".jpg", proposals[idx]["cropped"], [cv2.IMWRITE_JPEG_QUALITY, 85]
            )

            results.append({
                "bbox": proposals[idx]["bbox"],
                "confidence": score,
                "door_state": door_state,
                "record_type": "door",
                "cropped_bytes": jpeg_bytes.tobytes(),
                "frame_id": frame_id,
            })

        return results

    def detect_and_record(
        self,
        frame: np.ndarray,
        memory: SpatialLandmarkMemory,
        robot_position: tuple[float, float, float],
        robot_rotation: tuple[float, float, float],
        frame_id: str = "",
    ) -> list[SpatialRecord]:
        """Detect landmarks, create SpatialRecords, and store in memory.

        Args:
            frame: RGB image (H, W, 3).
            memory: SpatialLandmarkMemory instance.
            robot_position: (x, y, z) in world frame.
            robot_rotation: (roll, pitch, yaw) in world frame.
            frame_id: Optional frame identifier.

        Returns:
            List of newly created or updated SpatialRecords.
        """
        import math as _math

        detections = self.detect(frame, frame_id)
        records: list[SpatialRecord] = []

        for det in detections:
            # Offset door position ~2m in the robot's facing direction
            yaw = robot_rotation[2]
            door_x = robot_position[0] + 2.0 * _math.cos(yaw)
            door_y = robot_position[1] + 2.0 * _math.sin(yaw)

            rec = SpatialRecord(
                name="door",
                record_type=RecordType.DOOR,
                position=(door_x, door_y, robot_position[2]),
                rotation=robot_rotation,
                state=det["door_state"],
                confidence=det["confidence"],
                timestamp=time.time(),
                last_seen=time.time(),
            )
            stored_id = memory.record(rec)

            if det["cropped_bytes"]:
                path = memory.save_snapshot(stored_id, det["cropped_bytes"])
                if path:
                    rec.image_snapshot_path = path

            records.append(rec)

        return records
