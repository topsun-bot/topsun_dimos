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

import os
from typing import Any

import dashscope
from dashscope import MultiModalConversation
import numpy as np

from dimos.models.vl.base import VlModel, VlModelConfig
from dimos.msgs.sensor_msgs.Image import Image
from dimos.utils.logging_config import setup_logger

logger = setup_logger()


class DashScopeVlModelConfig(VlModelConfig):
    model_name: str = "qwen3.6-plus"
    api_key: str | None = None
    base_url: str = "https://dashscope.aliyuncs.com/api/v1"
    auto_resize: tuple[int, int] = (1024, 1024)  # Limit image size for speed


class DashScopeVlModel(VlModel):
    """VLM backed by Alibaba DashScope (百炼) native multimodal API.

    Configure via environment variables or config::

        DASHSCOPE_API_KEY      — API key (required)
        DIMOS_VLM_BASE_URL     — override base URL (region-specific)
        DIMOS_VLM_MODEL_NAME   — override model name

    The default base URL is ``https://dashscope.aliyuncs.com/api/v1``.
    Different regions may require different URLs.
    """

    config: DashScopeVlModelConfig

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        # Native DashScope API endpoint (not compatible-mode)
        dashscope.base_http_api_url = self.config.base_url
        if os.getenv("DIMOS_VLM_MODEL_NAME"):
            self.config.model_name = os.getenv("DIMOS_VLM_MODEL_NAME") or self.config.model_name

    def query(
        self,
        image: Image | np.ndarray,
        query: str,
        **kwargs: Any,
    ) -> str:
        if isinstance(image, np.ndarray):
            import warnings

            warnings.warn(
                "DashScopeVlModel.query should receive standard dimos Image type",
                DeprecationWarning,
                stacklevel=2,
            )
            image = Image.from_numpy(image)

        image, _ = self._prepare_image(image)

        return self._call_multimodal([image], query)

    def _call_multimodal(self, images: list[Image], query: str) -> str:
        api_key = self.config.api_key or os.getenv("DASHSCOPE_API_KEY")
        if not api_key:
            raise ValueError("DASHSCOPE_API_KEY environment variable must be set")

        content: list[dict[str, str]] = []
        for image in images:
            prepared, _ = self._prepare_image(image)
            img_b64 = prepared.to_base64()
            content.append({"image": f"data:image/jpeg;base64,{img_b64}"})
        content.append({"text": query})

        messages = [{"role": "user", "content": content}]

        response = MultiModalConversation.call(
            api_key=api_key,
            model=self.config.model_name,
            messages=messages,
        )

        if getattr(response, "status_code", None) not in (None, 200):
            msg = getattr(response, "message", "") or str(response)
            raise RuntimeError(
                f"DashScope API error: status_code={response.status_code} message={msg}"
            )

        if response.output and response.output.choices:
            raw = response.output.choices[0].message.content
            text = self._extract_message_text(raw)
            if text.strip():
                return text
        msg = getattr(response, "message", "") or "empty output"
        raise RuntimeError(f"DashScope returned no text: {msg}")

    @staticmethod
    def _extract_message_text(content: Any) -> str:
        if content is None:
            return ""
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts: list[str] = []
            for item in content:
                if isinstance(item, dict):
                    parts.append(str(item.get("text") or ""))
                elif isinstance(item, str):
                    parts.append(item)
            return "".join(parts)
        return str(content)

    def query_batch(
        self,
        images: list[Image],
        query: str,
        **kwargs: Any,
    ) -> list[str]:
        """Send all images in one multimodal request (same semantics as OpenAIVlModel)."""
        if not images:
            return []
        response_text = self._call_multimodal(images, query)
        return [response_text] * len(images)

    def stop(self) -> None:
        if "_ready" in self.__dict__:
            del self.__dict__["_ready"]
