# Copyright 2025-2026 Dimensional Inc.
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

from typing import Any

from dimos.models.qwen.bbox import BBox
from dimos.models.vl.base import VlModel
from dimos.msgs.sensor_msgs.Image import Image
from dimos.utils.generic import extract_json_from_llm_response


def _label_matches_query(label: str, query: str) -> bool:
    if not query:
        return True
    if not label:
        return False
    l, q = label.lower().strip(), query.lower().strip()
    if l == q or q in l or l in q:
        return True
    if not q.replace(" ", "").isascii():
        return bool(set(q) & set(l))
    return bool(set(q.split()) & set(l.split()))


def _parse_bbox4(raw: Any) -> tuple[float, float, float, float] | None:
    if raw is None:
        return None
    if isinstance(raw, dict):
        return _parse_bbox4(
            raw.get("bbox") or raw.get("bbox_2d") or raw.get("box") or raw.get("bounding_box")
        )
    if not isinstance(raw, (list, tuple)) or len(raw) != 4:
        return None
    try:
        x1, y1, x2, y2 = (float(v) for v in raw)
    except (TypeError, ValueError):
        return None
    if x2 <= x1 or y2 <= y1:
        return None
    return (x1, y1, x2, y2)


def _bbox_from_detection_item(item: dict[str, Any]) -> tuple[float, float, float, float] | None:
    for key in ("bbox", "bbox_2d", "box", "bounding_box"):
        if key in item:
            parsed = _parse_bbox4(item[key])
            if parsed is not None:
                return parsed
    return None


def _scale_bbox_to_image(
    bbox: tuple[float, float, float, float],
    image: Image,
) -> BBox:
    """Map model coords to pixel space (0-1 fraction, Qwen 0-1000, or absolute pixels)."""
    h, w = image.data.shape[:2]
    x1, y1, x2, y2 = bbox
    mx = max(x1, y1, x2, y2)

    if mx <= 1.0:
        return (x1 * w, y1 * h, x2 * w, y2 * h)

    # Already pixel coordinates when the box fits inside the frame
    if 0 <= x1 < x2 <= w + 1 and 0 <= y1 < y2 <= h + 1:
        return (x1, y1, x2, y2)

    # Qwen VL 0–1000 relative (when coords exceed image size)
    if mx <= 1000.0 and min(x1, y1, x2, y2) >= 0.0:
        return (x1 / 1000.0 * w, y1 / 1000.0 * h, x2 / 1000.0 * w, y2 / 1000.0 * h)

    return (x1, y1, x2, y2)


def parse_object_bbox_from_vlm_response(
    result: Any,
    object_description: str,
    image: Image,
) -> BBox | None:
    """Parse VLM JSON into a single pixel bbox (x1, y1, x2, y2).

    Supports:
    - ``{"bbox": [x1,y1,x2,y2], "name": "..."}``
    - ``[{"bbox_2d": [...], "label": "..."}, ...]`` (Qwen-style)
    """
    candidates: list[tuple[float, float, tuple[float, float, float, float]]] = []

    if isinstance(result, dict):
        bbox = _bbox_from_detection_item(result)
        if bbox is not None:
            name = str(result.get("name") or result.get("label") or "")
            score = 1.0 if _label_matches_query(name, object_description) else 0.0
            area = (bbox[2] - bbox[0]) * (bbox[3] - bbox[1])
            candidates.append((score, area, bbox))
    elif isinstance(result, list):
        for item in result:
            if not isinstance(item, dict):
                continue
            bbox = _bbox_from_detection_item(item)
            if bbox is None:
                continue
            label = str(item.get("label") or item.get("name") or "")
            score = 1.0 if _label_matches_query(label, object_description) else 0.0
            area = (bbox[2] - bbox[0]) * (bbox[3] - bbox[1])
            candidates.append((score, area, bbox))

    if not candidates:
        return None

    candidates.sort(key=lambda c: (-c[0], -c[1]))
    if candidates[0][0] <= 0.0:
        return None
    return _scale_bbox_to_image(candidates[0][2], image)


def get_object_bbox_from_image(
    vl_model: VlModel, image: Image, object_description: str
) -> BBox | None:
    prompt = (
        f"在这张图中找到「{object_description}」(可用中文或英文描述)。"
        "Return JSON for every matching instance. Prefer either format:\n"
        '1) A single object: {"name": "<中文或英文名>", "bbox": [x1, y1, x2, y2]}\n'
        '2) Multiple objects: [{"label": "...", "bbox_2d": [x1, y1, x2, y2]}, ...]\n'
        "Use top-left and bottom-right corners in image coordinates. "
        "If not found, return null."
    )

    try:
        response = vl_model.query(image, prompt)
    except Exception:
        return None

    result = extract_json_from_llm_response(response)
    if result is None:
        return None

    return parse_object_bbox_from_vlm_response(result, object_description, image)
