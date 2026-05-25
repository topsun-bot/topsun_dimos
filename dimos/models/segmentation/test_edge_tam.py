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

from unittest.mock import patch

import numpy as np

from dimos.models.segmentation.edge_tam import EdgeTAMProcessor
from dimos.msgs.sensor_msgs.Image import Image


def test_edge_tam_gracefully_degrades_without_cuda() -> None:
    image = Image.from_numpy(np.zeros((64, 64, 3), dtype=np.uint8))

    with patch("dimos.models.segmentation.edge_tam.torch.cuda.is_available", return_value=False):
        tracker = EdgeTAMProcessor()

    assert tracker.available is False
    assert len(tracker.init_track(image=image, box=np.array([1, 2, 3, 4], dtype=np.float32))) == 0
    assert len(tracker.process_image(image)) == 0
    tracker.stop()
