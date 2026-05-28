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

from typing import Any

import numpy as np

from dimos.msgs.sensor_msgs.Image import Image
from dimos.navigation.visual.query import (
    _label_matches_query,
    _sanitize_description,
    get_object_bbox_from_image,
    parse_object_bbox_from_vlm_response,
    parse_object_detection_from_vlm_response,
    vlm_object_present_in_view,
)


def _img(w: int = 1000, h: int = 800) -> Image:
    return Image.from_numpy(np.zeros((h, w, 3), dtype=np.uint8))


class _StubVlModel:
    """Returns canned responses in order; one per ``query()`` call."""

    def __init__(self, *responses: str) -> None:
        self._responses = list(responses)
        self.calls: list[tuple[Image, str]] = []

    def query(self, image: Image, prompt: str, **_kw: Any) -> str:
        self.calls.append((image, prompt))
        return self._responses.pop(0) if self._responses else ""


def test_parse_qwen_bbox_2d_array() -> None:
    result = [
        {"bbox_2d": [428, 333, 522, 488], "label": "灭火器"},
        {"bbox_2d": [548, 336, 642, 486], "label": "灭火器"},
    ]
    bbox = parse_object_bbox_from_vlm_response(result, "灭火器", _img())
    assert bbox is not None
    assert bbox == (428.0, 333.0, 522.0, 488.0)


def test_parse_legacy_bbox_object() -> None:
    result = {"name": "fire extinguisher", "bbox": [10, 20, 100, 200]}
    bbox = parse_object_bbox_from_vlm_response(result, "fire", _img(640, 480))
    assert bbox == (10.0, 20.0, 100.0, 200.0)


def test_parse_picks_label_match() -> None:
    result = [
        {"bbox_2d": [0, 0, 100, 100], "label": "chair"},
        {"bbox_2d": [200, 200, 300, 300], "label": "灭火器"},
    ]
    bbox = parse_object_bbox_from_vlm_response(result, "灭火器", _img(1000, 1000))
    assert bbox is not None
    assert bbox[0] == 200.0


def test_parse_rejects_similar_chinese_furniture_label() -> None:
    result = [
        {"bbox_2d": [0, 0, 100, 100], "label": "沙发"},
        {"bbox_2d": [100, 100, 200, 200], "label": "椅子"},
    ]
    bbox = parse_object_bbox_from_vlm_response(
        result,
        "凳子, 外观特征: 淡绿色塑料高脚凳, 四条腿设计",
        _img(1000, 1000),
    )
    assert bbox is None


def test_parse_accepts_stool_variant_for_chinese_query() -> None:
    result = [
        {"bbox_2d": [0, 0, 100, 100], "label": "沙发"},
        {"bbox_2d": [200, 200, 300, 300], "label": "高脚凳"},
    ]
    bbox = parse_object_bbox_from_vlm_response(
        result,
        "凳子, 外观特征: 淡绿色塑料高脚凳, 四条腿设计",
        _img(1000, 1000),
    )
    assert bbox is not None
    assert bbox[0] == 200.0


def test_parse_respects_explicit_false_match() -> None:
    result = {
        "name": "凳子",
        "description": "looks like a sofa, not the requested stool",
        "match": False,
        "bbox": [100, 100, 200, 200],
    }
    bbox = parse_object_bbox_from_vlm_response(
        result,
        "凳子, 外观特征: 淡绿色塑料高脚凳, 四条腿设计",
        _img(1000, 1000),
    )
    assert bbox is None


def test_parse_preserves_zero_confidence() -> None:
    result = {
        "name": "凳子",
        "confidence": 0.0,
        "match": True,
        "bbox": [100, 100, 200, 200],
    }
    detection = parse_object_detection_from_vlm_response(
        result,
        "凳子",
        _img(1000, 1000),
    )
    assert detection is not None
    assert detection.confidence == 0.0


def test_parse_normalized_fraction() -> None:
    result = {"bbox": [0.1, 0.2, 0.5, 0.6]}
    bbox = parse_object_bbox_from_vlm_response(result, "", _img(200, 100))
    assert bbox == (20.0, 20.0, 100.0, 60.0)


def test_label_match_substring() -> None:
    """Substring match works in either direction (covers VLM label variants)."""
    assert _label_matches_query("水桶", "水桶") is True
    assert _label_matches_query("蓝色水桶", "水桶") is True
    assert _label_matches_query("桶", "水桶") is True
    assert _label_matches_query("office chair", "chair") is True
    assert _label_matches_query("chair office", "office chair") is True
    assert _label_matches_query("red-chair", "chair red") is True


def test_label_match_rejects_chinese_character_overlap() -> None:
    """Regression: ``垃圾桶`` must NOT match a ``水桶`` query.

    The previous implementation fell back to per-character set intersection
    for Chinese, so any pair sharing a single character (here: ``桶``) would
    match. That caused ``verify_object_in_view`` to confidently report a
    bucket as still present whenever a trash can was in frame.
    """
    assert _label_matches_query("垃圾桶", "水桶") is False
    assert _label_matches_query("书桌", "会议桌") is False
    assert _label_matches_query("电视", "显示器") is False
    # ASCII word-overlap path still works for English.
    assert _label_matches_query("computer", "chair") is False


def test_parse_filters_out_non_matching_chinese_label() -> None:
    """Even if VLM returns a bbox tagged as ``垃圾桶`` while we asked for
    ``水桶``, we must not accept it."""
    result = [{"bbox_2d": [0, 0, 100, 100], "label": "垃圾桶"}]
    bbox = parse_object_bbox_from_vlm_response(result, "水桶", _img())
    assert bbox is None


def test_get_object_bbox_returns_none_when_present_false() -> None:
    """``{"present": false}`` is the canonical "not visible" answer."""
    model = _StubVlModel('{"present": false}')
    bbox = get_object_bbox_from_image(model, _img(), "水桶")
    assert bbox is None
    assert len(model.calls) == 1


def test_get_object_bbox_returns_bbox_when_present() -> None:
    model = _StubVlModel('{"present": true, "name": "水桶", "bbox": [10, 20, 110, 220]}')
    bbox = get_object_bbox_from_image(model, _img(), "水桶")
    assert bbox == (10.0, 20.0, 110.0, 220.0)


def test_sanitize_description_strips_views_suffix() -> None:
    """Stored landmark ``state`` strings carry a ``"(views [...])"`` suffix
    that is meaningless to the VLM and noisy in the prompt."""
    assert _sanitize_description("桌上的透明矿泉水瓶 (views [0, 5])") == "桌上的透明矿泉水瓶"
    assert _sanitize_description("a fire extinguisher") == "a fire extinguisher"
    assert _sanitize_description(None) == ""
    assert _sanitize_description("") == ""


def test_presence_check_returns_true_on_yes() -> None:
    model = _StubVlModel("yes")
    assert vlm_object_present_in_view(model, _img(), "水桶") is True
    assert len(model.calls) == 1
    # Single call only — no bbox-then-ROI two-step.
    _img_arg, prompt = model.calls[0]
    assert "水桶" in prompt


def test_presence_check_returns_false_on_no() -> None:
    model = _StubVlModel("no")
    assert vlm_object_present_in_view(model, _img(), "水桶") is False


def test_presence_check_returns_false_when_response_contains_no() -> None:
    """Defensive: a hedged answer like 'no, that is a 垃圾桶' must read False."""
    model = _StubVlModel("no, that is a 垃圾桶")
    assert vlm_object_present_in_view(model, _img(), "水桶") is False


def test_presence_check_forwards_description_into_prompt() -> None:
    """The stored landmark description rides along on the same VLM call,
    helping the model disambiguate same-radical confusions like 水瓶 vs 水桶."""
    model = _StubVlModel("yes")
    vlm_object_present_in_view(
        model,
        _img(),
        "水瓶",
        description="桌上的透明矿泉水瓶 (views [0, 5])",
    )
    _img_arg, prompt = model.calls[0]
    assert "桌上的透明矿泉水瓶" in prompt
    # The "(views [...])" stash-suffix gets stripped before being shown.
    assert "(views" not in prompt


def test_presence_check_handles_query_failure_as_false() -> None:
    class _Boom:
        def query(self, *_a: Any, **_kw: Any) -> str:
            raise RuntimeError("API exploded")

    assert vlm_object_present_in_view(_Boom(), _img(), "水桶") is False
