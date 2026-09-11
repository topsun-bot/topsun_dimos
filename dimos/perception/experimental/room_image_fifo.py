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

"""FIFO order for persisted room reference images."""

from __future__ import annotations

from typing import Any

from dimos.utils.logging_config import setup_logger

logger = setup_logger()


class RoomImageFifo:
    """Persist room-image FIFO order across Chroma reopen.

    ``_room_image_ids`` starts empty in ``SpatialMemory.__init__``. Without a
    rehydrate, old IDs are never evicted after a restart even when the
    collection is already at ``max_room_images``.
    """

    @staticmethod
    def ids_from_collection(collection: Any) -> list[str]:
        try:
            results = collection.get(include=["metadatas"])
        except Exception as exc:
            logger.warning("Could not rehydrate room-image FIFO from collection: %s", exc)
            return []
        if not results or not results.get("ids"):
            return []
        ids: list[str] = results["ids"]
        metadatas: list[dict[str, Any]] = results.get("metadatas") or [{}] * len(ids)
        ordered = sorted(
            zip(ids, metadatas, strict=False),
            key=lambda pair: float((pair[1] or {}).get("timestamp", 0.0)),
        )
        return [frame_id for frame_id, _ in ordered]

    @staticmethod
    def evict_overflow(
        ids: list[str],
        max_images: int,
        *,
        delete: Any,
    ) -> list[str]:
        if max_images <= 0:
            return ids
        while len(ids) > max_images:
            evict_id = ids.pop(0)
            delete(evict_id)
        return ids
