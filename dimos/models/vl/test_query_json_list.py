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
import pytest

from dimos.models.vl.base import VlmJsonList, VlModel
from dimos.msgs.sensor_msgs.Image import Image


class _StubVl(VlModel):
    def __init__(self, payload: dict | list) -> None:  # type: ignore[type-arg]
        super().__init__()
        self._payload = payload

    def query(self, image: Image, query: str, **kwargs: Any) -> str:  # type: ignore[no-untyped-def]
        return "unused"

    def query_json(self, image: Image, query: str) -> dict | list:  # type: ignore[type-arg, override]
        return self._payload

    def start(self) -> None:
        return None

    def stop(self) -> None:
        return None


def _rgb() -> Image:
    return Image.from_numpy(np.zeros((8, 8, 3), dtype=np.uint8))


def test_vlm_json_list_rejects_dict() -> None:
    with pytest.raises(TypeError, match="JSON list"):
        VlmJsonList.require({"label": [1, 2, 3, 4]}, what="query_detections")


def test_query_detections_does_not_iterate_a_dict() -> None:
    detections = _StubVl({"not": "a-list"}).query_detections(_rgb(), "box")
    assert detections.detections == []
