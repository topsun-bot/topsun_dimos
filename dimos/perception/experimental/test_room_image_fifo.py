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

from dimos.perception.experimental.room_image_fifo import RoomImageFifo


class _Collection:
    def __init__(self, ids: list[str], timestamps: list[float]) -> None:
        self._ids = ids
        self._timestamps = timestamps

    def get(self, include: list[str]) -> dict[str, object]:
        assert "metadatas" in include
        return {
            "ids": list(self._ids),
            "metadatas": [{"timestamp": ts} for ts in self._timestamps],
        }


def test_room_image_fifo_rehydrates_oldest_first_and_enforces_cap() -> None:
    collection = _Collection(["newer", "oldest", "mid"], [3.0, 1.0, 2.0])
    ids = RoomImageFifo.ids_from_collection(collection)
    assert ids == ["oldest", "mid", "newer"]

    deleted: list[str] = []
    kept = RoomImageFifo.evict_overflow(ids, 2, delete=deleted.append)
    assert kept == ["mid", "newer"]
    assert deleted == ["oldest"]
