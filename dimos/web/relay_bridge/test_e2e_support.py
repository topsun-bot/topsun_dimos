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

"""Teardown contract for relay e2e fixtures (no Deno / no real bridge)."""

import asyncio
from collections.abc import Iterator
from contextlib import contextmanager
import threading
from typing import TYPE_CHECKING, cast

import pytest

from dimos.web.relay_bridge.e2e_support import stop_module

if TYPE_CHECKING:
    from dimos.web.relay_bridge.relay_bridge_module import RelayBridgeModule


class _StubBridge:
    """Duck-typed stand-in: stop_module only needs stop/_loop/_loop_thread."""

    def __init__(self, loop: asyncio.AbstractEventLoop, thread: threading.Thread) -> None:
        self._loop = loop
        self._loop_thread = thread

    def stop(self) -> None:
        # Leave the loop running, matching Module.stop() after a 2s join
        # timeout (killed relay child, leftover tasks).
        return


@contextmanager
def _orphaned_loop() -> Iterator[tuple[asyncio.AbstractEventLoop, threading.Thread]]:
    loop = asyncio.new_event_loop()
    thread = threading.Thread(target=loop.run_forever, name="run_forever", daemon=True)
    thread.start()
    try:
        yield loop, thread
    finally:
        if loop.is_running():
            loop.call_soon_threadsafe(loop.stop)
        if thread.is_alive():
            thread.join(timeout=5.0)
        if not loop.is_closed():
            loop.close()


def test_stop_module_reaps_still_running_loop() -> None:
    with _orphaned_loop() as (loop, thread):
        stop_module(cast("RelayBridgeModule", _StubBridge(loop, thread)))
        assert not thread.is_alive()
        assert not loop.is_running()


def test_stop_module_still_reaps_when_module_stop_raises() -> None:
    class _Raising(_StubBridge):
        def stop(self) -> None:
            raise RuntimeError("relay child already dead")

    with _orphaned_loop() as (loop, thread):
        with pytest.raises(RuntimeError, match="relay child already dead"):
            stop_module(cast("RelayBridgeModule", _Raising(loop, thread)))
        assert not thread.is_alive()
        assert not loop.is_running()
