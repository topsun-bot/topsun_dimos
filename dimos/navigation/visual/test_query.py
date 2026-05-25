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

import numpy as np

from dimos.msgs.sensor_msgs.Image import Image
from dimos.navigation.visual.query import parse_object_bbox_from_vlm_response


def _img(w: int = 1000, h: int = 800) -> Image:
    return Image.from_numpy(np.zeros((h, w, 3), dtype=np.uint8))


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


def test_parse_normalized_fraction() -> None:
    result = {"bbox": [0.1, 0.2, 0.5, 0.6]}
    bbox = parse_object_bbox_from_vlm_response(result, "x", _img(200, 100))
    assert bbox == (20.0, 20.0, 100.0, 60.0)
