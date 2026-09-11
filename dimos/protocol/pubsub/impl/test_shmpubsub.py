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

"""Shared-memory pubsub: self-unsubscribe from the fanout thread."""

from collections.abc import Iterator
import threading
import uuid

import pytest

from dimos.protocol.pubsub.impl.shmpubsub import SharedMemoryPubSubBase


@pytest.fixture
def shm_pair() -> Iterator[tuple[SharedMemoryPubSubBase, SharedMemoryPubSubBase, str]]:
    topic = f"/shm_self_unsub_{uuid.uuid4().hex}"
    publisher = SharedMemoryPubSubBase(prefer="cpu", default_capacity=4096)
    subscriber = SharedMemoryPubSubBase(prefer="cpu", default_capacity=4096)
    publisher.start()
    subscriber.start()
    try:
        yield publisher, subscriber, topic
    finally:
        subscriber.stop()
        publisher.stop()


def test_fanout_callback_can_unsubscribe_and_resubscribe(
    shm_pair: tuple[SharedMemoryPubSubBase, SharedMemoryPubSubBase, str],
) -> None:
    """A remote frame's callback may tear down its own subscription.

    Local ``publish`` delivers synchronously on the publisher thread, so the
    contract that matters is the fanout path: one SHM instance publishes,
    the other reads on its fanout thread. Joining that thread from the
    callback used to raise ``RuntimeError``.
    """
    publisher, subscriber, topic = shm_pair
    first = threading.Event()
    second = threading.Event()
    unsubscribe: list[object] = []
    received: list[bytes] = []

    def one_shot(message: bytes, _topic: str) -> None:
        received.append(message)
        unsub = unsubscribe[0]
        assert callable(unsub)
        unsub()
        first.set()

    unsubscribe.append(subscriber.subscribe(topic, one_shot))
    publisher.publish(topic, b"first")
    assert first.wait(timeout=5.0), "fanout self-unsubscribe deadlocked or raised"

    def after(message: bytes, _topic: str) -> None:
        received.append(message)
        second.set()

    subscriber.subscribe(topic, after)
    publisher.publish(topic, b"second")
    assert second.wait(timeout=5.0), "resubscribe after self-unsubscribe never delivered"
    assert received == [b"first", b"second"]
