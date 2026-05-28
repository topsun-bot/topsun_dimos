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

from dataclasses import dataclass
import re
from typing import Any

from dimos.models.qwen.bbox import BBox
from dimos.models.vl.base import VlModel
from dimos.msgs.sensor_msgs.Image import Image
from dimos.utils.generic import extract_json_from_llm_response
from dimos.utils.logging_config import setup_logger

logger = setup_logger()


@dataclass(frozen=True)
class ObjectDetectionResult:
    bbox: BBox
    name: str
    description: str
    confidence: float | None
    match: bool | None


def _clean_label_text(text: str) -> str:
    return (
        text.lower()
        .strip()
        .replace(" ", "")
        .replace("_", "")
        .replace("-", "")
        .replace("\uff0c", ",")
        .replace("\uff1a", ":")
    )


def _query_target_terms(query: str) -> list[str]:
    q = _clean_label_text(query)
    if not q:
        return []
    head = q.split(",", 1)[0].split(":", 1)[0]
    terms = [head] if head else []

    # If the target is a common Chinese noun like 凳子/椅子, also allow labels
    # such as 高脚凳/办公椅 without accepting unrelated words that only share 子.
    if len(head) >= 2 and head.endswith("子"):
        stem = head[:-1]
        if stem:
            terms.append(stem)
    return terms


def _label_matches_query(label: str, query: str) -> bool:
    """Return True if VLM-returned ``label`` plausibly names the queried object.

    Matching rules:
      * Exact match (case-insensitive).
      * Either string is a substring of the other (covers ``"水桶"`` vs
        ``"蓝色水桶"``, ``"chair"`` vs ``"office chair"``).
      * For ASCII queries, any shared whitespace-delimited word counts.

    For Chinese / mixed queries we deliberately do NOT fall back to a
    per-character set intersection. That fallback used to let ``"垃圾桶"``
    match a query for ``"水桶"`` via the shared ``"桶"`` character, which
    causes high-recall false positives in ``verify_object_in_view`` /
    ``_visual_acquire_object`` (the model is asked "find 水桶" while the
    real object in frame is a 垃圾桶, and we accept the wrong label).
    """
    if not query:
        return True
    if not label:
        return False
    raw_label = label.lower().strip()
    raw_query = query.lower().strip()
    l = _clean_label_text(label)
    q = _clean_label_text(query)
    if l == q or q in l or l in q:
        return True
    if not q.isascii():
        return any(term and (term in l or l in term) for term in _query_target_terms(query))
    query_tokens = set(re.findall(r"[a-z0-9]+", raw_query))
    label_tokens = set(re.findall(r"[a-z0-9]+", raw_label))
    return bool(query_tokens & label_tokens)


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


def _detection_name(item: dict[str, Any]) -> str:
    return str(item.get("name") or item.get("label") or item.get("class") or "")


def _detection_description(item: dict[str, Any]) -> str:
    raw = (
        item.get("description")
        or item.get("reason")
        or item.get("visual_features")
        or item.get("attributes")
        or ""
    )
    if isinstance(raw, (list, tuple)):
        return ", ".join(str(v) for v in raw)
    return str(raw)


def _detection_confidence(item: dict[str, Any]) -> float | None:
    if "confidence" in item:
        raw = item["confidence"]
    else:
        raw = item.get("score")
    if raw is None:
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def _detection_match(item: dict[str, Any]) -> bool | None:
    if "match" in item:
        raw = item["match"]
    else:
        raw = item.get("is_match")
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, str):
        normalized = raw.lower().strip()
        if normalized in {"true", "yes", "match", "matched"}:
            return True
        if normalized in {"false", "no", "not_match", "not matched"}:
            return False
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


def _candidate_debug_summary(
    item: dict[str, Any],
    bbox: tuple[float, float, float, float],
    score: float,
) -> str:
    return (
        f"name={_detection_name(item)!r}, "
        f"description={_detection_description(item)!r}, "
        f"confidence={_detection_confidence(item)}, "
        f"match={_detection_match(item)}, "
        f"score={score:.1f}, "
        f"bbox={bbox}"
    )


def parse_object_detection_from_vlm_response(
    result: Any,
    object_description: str,
    image: Image,
) -> ObjectDetectionResult | None:
    """Parse VLM JSON into a detection result with bbox and debug metadata.

    Supports:
    - ``{"bbox": [x1,y1,x2,y2], "name": "...", "confidence": 0.9}``
    - ``[{"bbox_2d": [...], "label": "..."}, ...]`` (Qwen-style)
    """
    candidates: list[
        tuple[float, float, float, tuple[float, float, float, float], dict[str, Any]]
    ] = []
    rejected_debug: list[str] = []

    if isinstance(result, dict):
        bbox = _bbox_from_detection_item(result)
        if bbox is not None:
            name = _detection_name(result)
            match = _detection_match(result)
            if match is False:
                score = 0.0
            elif match is True:
                score = 1.0
            elif name:
                score = 1.0 if _label_matches_query(name, object_description) else 0.0
            else:
                # A bare bbox cannot prove it matches a target with visual attributes.
                score = 1.0 if not object_description.strip() else 0.0
            area = (bbox[2] - bbox[0]) * (bbox[3] - bbox[1])
            confidence = _detection_confidence(result)
            candidates.append(
                (score, confidence if confidence is not None else -1.0, area, bbox, result)
            )
            if score <= 0.0:
                rejected_debug.append(_candidate_debug_summary(result, bbox, score))
    elif isinstance(result, list):
        for item in result:
            if not isinstance(item, dict):
                continue
            bbox = _bbox_from_detection_item(item)
            if bbox is None:
                continue
            label = _detection_name(item)
            match = _detection_match(item)
            if match is False:
                score = 0.0
            elif match is True:
                score = 1.0
            else:
                score = 1.0 if _label_matches_query(label, object_description) else 0.0
            area = (bbox[2] - bbox[0]) * (bbox[3] - bbox[1])
            confidence = _detection_confidence(item)
            candidates.append(
                (score, confidence if confidence is not None else -1.0, area, bbox, item)
            )
            if score <= 0.0:
                rejected_debug.append(_candidate_debug_summary(item, bbox, score))

    if not candidates:
        logger.warning(
            "[VLM bbox] VLM returned JSON for query=%r but no bbox candidates were parsed: %r",
            object_description,
            result,
        )
        return None

    candidates.sort(key=lambda c: (-c[0], -c[1], -c[2]))
    if candidates[0][0] <= 0.0:
        logger.warning(
            "[VLM bbox] VLM returned detections for query=%r but all were rejected: %s",
            object_description,
            "; ".join(rejected_debug) if rejected_debug else repr(result),
        )
        return None
    _score, _confidence_sort, _area, bbox, item = candidates[0]
    return ObjectDetectionResult(
        bbox=_scale_bbox_to_image(bbox, image),
        name=_detection_name(item),
        description=_detection_description(item),
        confidence=_detection_confidence(item),
        match=_detection_match(item),
    )


def parse_object_bbox_from_vlm_response(
    result: Any,
    object_description: str,
    image: Image,
) -> BBox | None:
    detection = parse_object_detection_from_vlm_response(result, object_description, image)
    return detection.bbox if detection is not None else None


def get_object_detection_from_image(
    vl_model: VlModel, image: Image, object_description: str
) -> ObjectDetectionResult | None:
    prompt = (
        f"任务：判断这张图中是否清晰可见「{object_description}」并定位它。\n"
        "判定原则：\n"
        f"  • 必须是名字与「{object_description}」一致的物体本身才算可见。\n"
        "  • 如果描述中包含颜色、形状、材质、腿/轮/层架等外观特征，"
        "必须同时匹配这些特征。\n"
        "  • 名字相近但实际不同的物体（例如把「垃圾桶」当成「水桶」、"
        "「书桌」当成「会议桌」、「显示器」当成「电视」）一律不算可见。\n"
        "  • 不要把沙发、长椅、普通椅子、桌子等相似家具误当成目标。\n"
        "  • 不在画面中、被严重遮挡、或不确定，都返回不存在。\n"
        f"  • bbox 内的物体必须能被人不犹豫地标注成「{object_description}」。\n"
        "返回严格 JSON（不要任何额外解释）：\n"
        '  在画面中：{"present": true, "name": "<具体物体名>", '
        '"description": "<颜色/形状/材质/结构等匹配依据>", '
        '"confidence": 0.0-1.0, "match": true, "bbox": [x1, y1, x2, y2]}\n'
        '  不在画面中：{"present": false}\n'
        "坐标用图像左上角为原点的左上、右下角像素坐标。"
    )
    logger.debug("[VLM bbox] prompt=%s", prompt)

    try:
        response = vl_model.query(image, prompt)
    except Exception:
        return None

    result = extract_json_from_llm_response(response)
    if result is None:
        return None

    if isinstance(result, dict) and result.get("present") is False:
        logger.warning(
            "[VLM bbox] VLM returned present=false for query=%r: %r",
            object_description,
            result,
        )
        return None

    detection = parse_object_detection_from_vlm_response(result, object_description, image)
    if detection is not None:
        logger.info(
            "[VLM bbox] detection name=%r description=%r confidence=%s match=%s bbox=%s",
            detection.name,
            detection.description,
            detection.confidence,
            detection.match,
            detection.bbox,
        )
    else:
        logger.info("[VLM bbox] no matching detection parsed for query=%r", object_description)
    return detection


def get_object_bbox_from_image(
    vl_model: VlModel, image: Image, object_description: str
) -> BBox | None:
    """Locate ``object_description`` in ``image``; return pixel bbox or None.

    The prompt is intentionally non-leading: the VLM is asked first to decide
    presence and is explicitly instructed to return null when the object is
    absent or ambiguous. A directive-style prompt ("find X") biases models
    like Qwen-VL into hallucinating bboxes for similar-but-different objects
    (e.g. labelling a 垃圾桶 as 水桶).
    """
    detection = get_object_detection_from_image(vl_model, image, object_description)
    return detection.bbox if detection is not None else None


_VIEWS_SUFFIX_RE = re.compile(r"\s*\(views\s*\[[^\]]*\]\)\s*$")


def _sanitize_description(description: str | None) -> str:
    """Strip storage-only suffixes (e.g. ``"(views [0, 5])"``) from a stored
    landmark description before showing it to the VLM."""
    if not description:
        return ""
    return _VIEWS_SUFFIX_RE.sub("", description).strip()


def vlm_object_present_in_view(
    vl_model: VlModel,
    image: Image,
    object_description: str,
    *,
    description: str | None = None,
) -> bool:
    """Single-call yes/no presence check.

    Faster than :func:`get_object_bbox_from_image` (no bbox math, no tracker
    setup) and intended for "is X currently visible?" questions where the
    bbox isn't needed (e.g. :meth:`verify_object_in_view`).

    Args:
        object_description: Object name (typically Chinese, e.g. ``"水桶"``).
        description: Optional landmark description that helps disambiguate
            similar objects, e.g. ``"桌上的透明矿泉水瓶"`` for a 水瓶 record.
            Stored ``state`` strings often have a ``"(views [...])"`` suffix
            from VLM panorama indexing — that suffix is stripped before being
            shown to the model.

    Returns:
        True iff the VLM answers ``"yes"`` (case-insensitive). Any error,
        empty response, or response containing ``"no"`` returns False.
    """
    extra = _sanitize_description(description)
    extra_line = f"\n参考描述：{extra}" if extra else ""
    prompt = (
        f"这张图里有「{object_description}」吗？{extra_line}\n"
        "判定原则：\n"
        f"  • 必须是名字与「{object_description}」一致的物体本身才算有。\n"
        "  • 名字相近但实际不同的物体（例如「垃圾桶」≠「水桶」、"
        "「书桌」≠「会议桌」、「显示器」≠「电视」、"
        "「水瓶」≠「水桶」）一律算「没有」。\n"
        "  • 不在画面中、被严重遮挡、或不确定，都按「没有」算。\n"
        "只回 'yes' 或 'no'，不要解释。"
    )
    try:
        response = vl_model.query(image, prompt)
    except Exception:
        return False
    if not isinstance(response, str):
        return False
    answer = response.strip().lower()
    if not answer:
        return False
    return "yes" in answer and "no" not in answer
