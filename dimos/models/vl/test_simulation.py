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

import json

import numpy as np

from dimos.models.vl.simulation import SimulationVlModel
from dimos.msgs.sensor_msgs.Image import Image
from dimos.navigation.visual.query import get_object_bbox_from_image, parse_simple_bbox_line


def _image() -> Image:
    return Image.from_numpy(np.zeros((720, 1280, 3), dtype=np.uint8))


def test_simulation_vlm_supports_compact_room_scan_format() -> None:
    model = SimulationVlModel(object_name="灭火器")

    result = model.query(_image(), "仅输出一行, 不要JSON")

    assert parse_simple_bbox_line(result) == [{"name": "灭火器", "bbox": [400, 250, 600, 800]}]


def test_simulation_vlm_supports_query_bbox_format() -> None:
    model = SimulationVlModel(object_name="灭火器")

    bbox = get_object_bbox_from_image(model, _image(), "灭火器")

    assert bbox == (512.0, 180.0, 768.0, 576.0)


def test_simulation_vlm_supports_panorama_json_format() -> None:
    model = SimulationVlModel(object_name="灭火器")

    response = model.query_batch([_image(), _image()], "Return ONLY a JSON array")

    assert len(response) == 2
    assert json.loads(response[0])[0]["name"] == "灭火器"
