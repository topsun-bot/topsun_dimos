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

"""Fast SpatialMemory persistence tests that do not require CLIP LFS assets."""

from collections.abc import Callable, Iterator

import chromadb
import numpy as np
import pytest

from dimos.perception.experimental.spatial_perception import SpatialMemory
from dimos.perception.experimental.visual_memory import VisualMemory
from dimos.protocol.rpc.pubsubrpc import LCMRPC
from dimos.types.robot_location import RobotLocation


class _FakeEmbeddingProvider:
    def __init__(self, *args: object, **kwargs: object) -> None:
        pass

    def get_embedding(self, image: np.ndarray) -> np.ndarray:
        mean = float(image.mean()) / 255.0
        return np.asarray([mean, 1.0 - mean, 0.25, 0.75], dtype=np.float32)

    def get_text_embedding(self, text: str) -> np.ndarray:
        seed = float(sum(text.encode("utf-8")) % 100) / 100.0
        return np.asarray([seed, 1.0 - seed, 0.25, 0.75], dtype=np.float32)


@pytest.fixture()
def spatial_memory_factory(mocker, tmp_path) -> Iterator[Callable[..., SpatialMemory]]:
    """Build SpatialMemory with real in-memory Chroma and deterministic embeddings."""
    mocker.patch("dimos.core.module.get_loop", return_value=(None, None))
    mocker.patch.object(LCMRPC, "__init__", return_value=None)
    mocker.patch.object(LCMRPC, "serve_module_rpc")
    mocker.patch.object(LCMRPC, "start")
    mocker.patch.object(LCMRPC, "stop")
    mocker.patch(
        "dimos.perception.experimental.spatial_perception.ImageEmbeddingProvider",
        _FakeEmbeddingProvider,
    )
    modules: list[SpatialMemory] = []
    chroma_client = chromadb.PersistentClient(path=str(tmp_path / "chroma"))
    visual_memory = VisualMemory(output_dir=str(tmp_path))

    def create(**kwargs: object) -> SpatialMemory:
        module = SpatialMemory(
            collection_name="memory_merge_test",
            chroma_client=chroma_client,
            db_path=None,
            visual_memory=visual_memory,
            visual_memory_path=None,
            output_dir=str(tmp_path),
            **kwargs,
        )
        modules.append(module)
        return module

    yield create

    for module in modules:
        module.dispose()


def _location(name: str, location_id: str) -> RobotLocation:
    return RobotLocation(
        name=name,
        position=(1.0, 2.0, 0.0),
        rotation=(0.0, 0.0, 0.3),
        location_id=location_id,
    )


def test_room_image_is_available_immediately_after_tag(spatial_memory_factory) -> None:
    memory = spatial_memory_factory()
    image = np.full((32, 48, 3), 80, dtype=np.uint8)
    office = _location("办公室", "office-front")

    assert memory.tag_location_with_image(office, image) is True

    assert memory.room_collection.count() == 1
    assert memory.get_room_images() == [
        {
            "name": "办公室",
            "count": 1,
            "images": [{"location_id": "office-front", "timestamp": office.timestamp}],
        }
    ]
    restored = memory.get_room_image("office-front")
    assert restored is not None
    assert restored.shape == image.shape
    assert np.mean(np.abs(restored.astype(float) - image.astype(float))) < 2.0

    match = memory.query_location_by_image(image)
    assert match is not None
    assert match.name == "办公室"
    assert match.location_id == "office-front"
    assert match.metadata["distance"] == pytest.approx(0.0, abs=1e-6)


def test_room_image_fifo_evicts_chroma_and_pixels_together(spatial_memory_factory) -> None:
    memory = spatial_memory_factory(max_room_images=1)
    first = _location("办公室", "office-first")
    second = _location("会议室", "meeting-second")

    assert memory.tag_location_with_image(first, np.full((16, 16, 3), 20, dtype=np.uint8))
    assert memory.tag_location_with_image(second, np.full((16, 16, 3), 220, dtype=np.uint8))

    assert memory.room_collection.get(include=[])["ids"] == ["meeting-second"]
    assert memory.get_room_image("office-first") is None
    assert memory.get_room_image("meeting-second") is not None


def test_restart_rebuilds_room_fifo_without_pruning_room_pixels(spatial_memory_factory) -> None:
    first_run = spatial_memory_factory(max_room_images=3)
    old = _location("办公室", "office-old")
    latest = _location("会议室", "meeting-latest")
    assert first_run.tag_location_with_image(old, np.full((16, 16, 3), 20, dtype=np.uint8))
    assert first_run.tag_location_with_image(latest, np.full((16, 16, 3), 220, dtype=np.uint8))

    restarted = spatial_memory_factory(max_room_images=1)

    assert restarted.room_collection.get(include=[])["ids"] == ["meeting-latest"]
    assert restarted.get_room_image("office-old") is None
    assert restarted.get_room_image("meeting-latest") is not None
