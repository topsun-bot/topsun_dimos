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

"""VLM 本地 -> 云端 fallback 包装类.

本地 VLM 服务不可用时自动切换到云端 API, 冷却期内不再重复探测本地.
"""

from __future__ import annotations

import time
from typing import Any

from dimos.models.vl.base import VlModel, VlModelConfig
from dimos.models.vl.openai import OpenAIVlModel
from dimos.msgs.sensor_msgs.Image import Image
from dimos.utils.logging_config import setup_logger

logger = setup_logger()


class FallbackVlModel(VlModel):
    """本地 VLM 不可用时自动 fallback 到云端的包装类.

    调用 query / query_batch 时先尝试 local_model, 失败则切到 cloud_model.
    local 失败后进入冷却期 (默认 60s), 冷却期内直接走 cloud, 避免重复探测.
    """

    def __init__(
        self,
        local_model: OpenAIVlModel,
        cloud_model: OpenAIVlModel,
        cooldown_seconds: float = 60.0,
    ) -> None:
        # 不走 Configurable.__init__ (它期望 config kwargs), 手动设 config 避免属性缺失
        self.config = VlModelConfig()
        self._local = local_model
        self._cloud = cloud_model
        self._cooldown = cooldown_seconds
        self._local_dead_until: float = 0.0

    @property
    def _use_local(self) -> bool:
        """本地是否可用 (冷却期已过)."""
        return time.monotonic() >= self._local_dead_until

    def _mark_local_dead(self) -> None:
        """标记本地不可用, 设置冷却期."""
        self._local_dead_until = time.monotonic() + self._cooldown

    def query(
        self,
        image: Image,
        query: str,
        response_format: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> str:
        if self._use_local:
            try:
                return self._local.query(image, query, response_format, **kwargs)
            except Exception as e:
                logger.warning(
                    "[VLM fallback] local query FAILED: %s: %s -> switching to cloud",
                    type(e).__name__,
                    e,
                )
                self._mark_local_dead()
        return self._cloud.query(image, query, response_format, **kwargs)

    def query_batch(
        self,
        images: list[Image],
        query: str,
        response_format: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> list[str]:
        if self._use_local:
            try:
                return self._local.query_batch(images, query, response_format, **kwargs)
            except Exception as e:
                logger.warning(
                    "[VLM fallback] local query_batch FAILED: %s: %s -> switching to cloud",
                    type(e).__name__,
                    e,
                )
                self._mark_local_dead()
        return self._cloud.query_batch(images, query, response_format, **kwargs)

    def stop(self) -> None:
        """释放本地和云端两个模型的客户端."""
        for m in (self._local, self._cloud):
            try:
                m.stop()
            except Exception:
                pass
