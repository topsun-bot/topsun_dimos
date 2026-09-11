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

from dimos.web.relay_bridge.e2e_support import RelayE2E

if TYPE_CHECKING:
    from dimos.web.relay_bridge.relay_bridge_module import RelayBridgeModule


class _StubBridge:
    """Duck-typed stand-in: RelayE2E.stop only needs stop/_loop/_loop_thread."""

    def __init__(self, loop: asyncio.AbstractEventLoop, thread: threading.Thread) -> None:
        self._loop = loop
        self._loop_thread = thread

    def stop(self) -> None:
        # Leave the loop running, matching Module.stop() after a 2s join
        # timeout (killed relay child, leftover tasks).
        return


class _Transport:
    def __init__(self) -> None:
        self.stopped = False

    def stop(self) -> None:
        self.stopped = True


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


class TestRelayE2E:
    def test_stop_reaps_still_running_loop(self) -> None:
        with _orphaned_loop() as (loop, thread):
            RelayE2E.stop(cast("RelayBridgeModule", _StubBridge(loop, thread)))
            assert not thread.is_alive()
            assert not loop.is_running()

    def test_stop_reaps_when_module_stop_raises(self) -> None:
        class _Raising(_StubBridge):
            def stop(self) -> None:
                raise RuntimeError("relay child already dead")

        with _orphaned_loop() as (loop, thread):
            transport = _Transport()
            with pytest.raises(RuntimeError, match="relay child already dead"):
                RelayE2E.stop(cast("RelayBridgeModule", _Raising(loop, thread)), (transport,))
            assert not thread.is_alive()
            assert not loop.is_running()
            assert transport.stopped

    def test_stop_still_stops_transports_when_module_is_missing(self) -> None:
        transport = _Transport()
        RelayE2E.stop(None, (transport,))
        assert transport.stopped

    def test_require_deno_skips_without_starting_threads(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("dimos.web.relay_bridge.e2e_support.find_deno", lambda: None)
        before = {t.ident for t in threading.enumerate() if t.is_alive()}
        with pytest.raises(pytest.skip.Exception, match="deno is not available"):
            RelayE2E.require_deno()
        leaked = [
            t.name
            for t in threading.enumerate()
            if t.is_alive() and t.ident not in before and t.name != "MainThread"
        ]
        assert leaked == []

    def test_stop_runs_after_start_skips(self) -> None:
        """Fixture shape: construct (loop live), start skips, finally must reap."""
        with _orphaned_loop() as (loop, thread):
            transport = _Transport()

            class _SkipStart(_StubBridge):
                def start(self) -> None:
                    raise pytest.skip.Exception("deno is not available")

            module = _SkipStart(loop, thread)
            try:
                module.start()
            except pytest.skip.Exception:
                RelayE2E.stop(cast("RelayBridgeModule", module), (transport,))
            assert not thread.is_alive()
            assert transport.stopped
