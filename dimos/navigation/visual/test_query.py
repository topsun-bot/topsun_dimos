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

import math

import numpy as np
import pytest

from dimos.msgs.sensor_msgs.Image import Image
from dimos.navigation.visual.query import (
    _scale_bbox_to_image,
    parse_object_bbox_from_vlm_response,
    yaw_offset_from_bbox,
)


def _img(w: int = 1000, h: int = 800) -> Image:
    return Image.from_numpy(np.zeros((h, w, 3), dtype=np.uint8))


def test_parse_qwen_bbox_2d_array() -> None:
    # 0-1000 归一化坐标, 1000x800 图像: x 不变, y 按 800/1000 缩放
    result = [
        {"bbox_2d": [428, 333, 522, 488], "label": "灭火器"},
        {"bbox_2d": [548, 336, 642, 486], "label": "灭火器"},
    ]
    bbox = parse_object_bbox_from_vlm_response(result, "灭火器", _img())
    assert bbox is not None
    assert bbox == pytest.approx((428.0, 333 * 0.8, 522.0, 488 * 0.8))


def test_parse_legacy_bbox_object() -> None:
    # <=1000 的坐标一律按 0-1000 归一化处理 (prompt 已明确要求该格式)
    result = {"name": "fire extinguisher", "bbox": [10, 20, 100, 200]}
    bbox = parse_object_bbox_from_vlm_response(result, "fire", _img(640, 480))
    assert bbox == pytest.approx((6.4, 9.6, 64.0, 96.0))


def test_parse_picks_label_match() -> None:
    result = [
        {"bbox_2d": [0, 0, 100, 100], "label": "chair"},
        {"bbox_2d": [200, 200, 300, 300], "label": "灭火器"},
    ]
    bbox = parse_object_bbox_from_vlm_response(result, "灭火器", _img(1000, 1000))
    assert bbox is not None
    assert bbox[0] == 200.0


def test_parse_normalized_fraction() -> None:
    result = {"bbox": [0.1, 0.2, 0.5, 0.6]}
    bbox = parse_object_bbox_from_vlm_response(result, "x", _img(200, 100))
    assert bbox == (20.0, 20.0, 100.0, 60.0)


# 尺度歧义回归测试: 1280x720 图像 + Qwen 0-1000 bbox


def test_scale_bbox_1280x720_qwen_0_1000() -> None:
    """回归: 0-1000 坐标落在 1280x720 像素范围内时, 不能被误判成像素坐标.

    真实案例: qwen3-vl-plus 对 1280x720 图像返回 bbox (706, 493, 759, 596),
    旧逻辑因 706<=1280 且 596<=720 走了像素直通, 导致角度算小近一半.
    """
    img = _img(1280, 720)
    bbox = _scale_bbox_to_image((706.0, 493.0, 759.0, 596.0), img)
    assert bbox == pytest.approx((706 * 1.28, 493 * 0.72, 759 * 1.28, 596 * 0.72))


def test_scale_bbox_1280x720_pixel_above_1000() -> None:
    # x 坐标超过 1000 且落在图像内 → 才允许像素直通
    img = _img(1280, 720)
    bbox = _scale_bbox_to_image((1050.0, 300.0, 1200.0, 500.0), img)
    assert bbox == (1050.0, 300.0, 1200.0, 500.0)


def test_scale_bbox_inferred_larger_range() -> None:
    # 超出图像范围的大坐标 (如 0-2000 归一化) → 按最大值推断缩放
    img = _img(1000, 800)
    bbox = _scale_bbox_to_image((500.0, 400.0, 2000.0, 1600.0), img)
    # scale = 2000 / 1000 = 2
    assert bbox == pytest.approx((250.0, 200.0, 1000.0, 800.0))


# yaw_offset_from_bbox 符号与 HFOV 测试: 目标在左 / 中 / 右


def test_yaw_offset_center() -> None:
    # bbox 中心 cx=500 (画面正中) → 偏航角为 0
    assert yaw_offset_from_bbox(450, 300, 550, 500, hfov_deg=69.0) == pytest.approx(0.0)


def test_yaw_offset_right_is_positive() -> None:
    # 目标在画面右侧 (cx>500) → 偏航角为正 (调用方需旋转 -offset 对准)
    offset = yaw_offset_from_bbox(700, 300, 800, 500, hfov_deg=69.0)
    assert offset > 0
    # cx=750, 偏离中心 250/500 → atan(0.5*tan(hfov/2))
    expected = math.atan(0.5 * math.tan(math.radians(69.0 / 2)))
    assert offset == pytest.approx(expected)


def test_yaw_offset_left_is_negative() -> None:
    # 目标在画面左侧 (cx<500) → 偏航角为负, 与右侧对称
    left = yaw_offset_from_bbox(200, 300, 300, 500, hfov_deg=69.0)
    right = yaw_offset_from_bbox(700, 300, 800, 500, hfov_deg=69.0)
    assert left < 0
    assert left == pytest.approx(-right)


def test_yaw_offset_image_edge_equals_half_hfov() -> None:
    # 画面最右边缘 (cx=1000) 的偏航角应等于 HFOV/2
    for hfov in (69.0, 90.0):
        offset = yaw_offset_from_bbox(1000, 0, 1000, 0, hfov_deg=hfov)
        assert math.degrees(offset) == pytest.approx(hfov / 2)


def test_yaw_offset_hfov_monotonic() -> None:
    # 同一 bbox, HFOV 越大偏航角越大 — 保证 69° 和 90° 混用会产生角度差
    o69 = yaw_offset_from_bbox(700, 300, 800, 500, hfov_deg=69.0)
    o90 = yaw_offset_from_bbox(700, 300, 800, 500, hfov_deg=90.0)
    assert o90 > o69
