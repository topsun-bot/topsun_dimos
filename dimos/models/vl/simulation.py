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

"""Deterministic VLM responses for end-to-end MuJoCo orchestration tests."""

import json
import os
import re
from typing import Any

from dimos.models.vl.base import VlModel, VlModelConfig
from dimos.msgs.sensor_msgs.Image import Image


class SimulationVlModelConfig(VlModelConfig):
    """Configuration for the MuJoCo-only deterministic visual target."""

    model_name: str = "deterministic-mujoco-vlm"
    object_name: str = "灭火器"
    bbox_0to1000: tuple[int, int, int, int] = (400, 250, 600, 800)


class SimulationVlModel(VlModel):
    """Return protocol-correct detections without calling a remote model."""

    config: SimulationVlModelConfig

    def __init__(self, **kwargs: Any) -> None:
        kwargs.setdefault(
            "object_name",
            os.getenv("DIMOS_SIMULATION_VLM_OBJECT_NAME", "灭火器"),
        )
        super().__init__(**kwargs)

    def query(self, image: Image, query: str, **kwargs: Any) -> str:
        """Return the response shape requested by navigation's VLM prompt."""
        del image, kwargs
        name = self._requested_name(query)
        x1, y1, x2, y2 = self.config.bbox_0to1000

        if "仅输出一行" in query or "不要JSON" in query:
            return f"{name},{x1},{y1},{x2},{y2};"

        if "找到「" in query or "every matching instance" in query:
            return json.dumps(
                {"name": name, "bbox": [x1, y1, x2, y2]},
                ensure_ascii=False,
            )

        return json.dumps(
            [
                {
                    "name": name,
                    "description": "MuJoCo 确定性视觉目标",
                    "bbox": [x1, y1, x2, y2],
                    "image_indices": [0],
                }
            ],
            ensure_ascii=False,
        )

    def query_batch(
        self,
        images: list[Image],
        query: str,
        **kwargs: Any,
    ) -> list[str]:
        """Return one shared panorama result for every supplied image."""
        if not images:
            return []
        response = self.query(images[0], query, **kwargs)
        return [response] * len(images)

    def stop(self) -> None:
        """Release the stateless simulation model."""

    def _requested_name(self, query: str) -> str:
        match = re.search(r"找到「([^」]+)」", query)
        if match:
            return match.group(1).strip() or self.config.object_name
        return self.config.object_name
