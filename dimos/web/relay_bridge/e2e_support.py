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

"""Shared helpers for relay bridge tests."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
import threading
import time
from typing import TYPE_CHECKING, Any

import pytest

from dimos.utils.deno import find_deno
from dimos.web.relay_bridge.protocol import (
    DataFrame,
    Manifest,
    Msg,
    Sub,
    Subs,
    TeleopStart,
    TeleopStarted,
    Watch,
)
from dimos.web.relay_bridge.wt_client import RelayClient

if TYPE_CHECKING:
    from dimos.web.relay_bridge.relay_bridge_module import RelayBridgeModule
    from dimos.web.relay_bridge.relay_process import RelayProcess


class RelayE2E:
    """Lifecycle and wire helpers for relay e2e tests."""

    @staticmethod
    def require_deno() -> str:
        """Skip before any module/transport threads exist when Deno is missing."""
        path = find_deno()
        if path is None:
            pytest.skip("deno is not available")
        return path

    @staticmethod
    def stop(
        module: RelayBridgeModule | None = None,
        transports: Sequence[Any] = (),
        relay: RelayProcess | None = None,
    ) -> None:
        """Stop a bridge, then reap leftover loop / LCM / relay resources.

        ``Module.stop`` can return while the loop is still running (2s join
        timeout after a killed child). ``run_until_complete`` from this thread
        then raises "This event loop is already running". Reap from the loop
        thread instead. Always stop hand-wired transports and an external
        relay so a skip or failed start cannot leak ``_lcm_loop`` threads.
        """
        loop: asyncio.AbstractEventLoop | None = (
            getattr(module, "_loop", None) if module is not None else None
        )
        loop_thread: threading.Thread | None = (
            getattr(module, "_loop_thread", None) if module is not None else None
        )
        try:
            if module is not None:
                module.stop()
        finally:
            RelayE2E._reap_loop(loop, loop_thread)
            for transport in transports:
                transport.stop()
            if relay is not None:
                relay.stop()

    @staticmethod
    def _reap_loop(
        loop: asyncio.AbstractEventLoop | None, loop_thread: threading.Thread | None
    ) -> None:
        if loop is not None and not loop.is_closed():

            def _cancel_tasks() -> None:
                for task in asyncio.all_tasks(loop):
                    task.cancel()

            try:
                if loop.is_running():
                    loop.call_soon_threadsafe(_cancel_tasks)
                    future = asyncio.run_coroutine_threadsafe(
                        loop.shutdown_default_executor(), loop
                    )
                    future.result(timeout=5.0)
                    loop.call_soon_threadsafe(loop.stop)
                else:
                    loop.run_until_complete(loop.shutdown_default_executor())
            except (RuntimeError, TimeoutError):
                if loop.is_running():
                    loop.call_soon_threadsafe(loop.stop)
        if loop_thread is not None and loop_thread.is_alive():
            loop_thread.join(timeout=5.0)

    @staticmethod
    @contextmanager
    def local_bridge(robot_id: str, *, teleop: bool = False) -> Iterator[RelayBridgeModule]:
        """Spawn a local Deno relay + hand-wired LCM transports.

        Deno is required *before* ``RelayBridgeModule()`` so a skip cannot
        leak the module loop thread. Start lives inside the ``try`` so a
        later ``ensure_deno`` skip still runs ``stop``.
        """
        from dimos.core.transport import pLCMTransport
        from dimos.web.relay_bridge.relay_bridge_module import (
            RelayBridgeConfig,
            RelayBridgeModule,
            default_manifest,
        )

        RelayE2E.require_deno()
        module: RelayBridgeModule | None = None
        transports: tuple[Any, ...] = ()
        try:
            if teleop:
                manifest = default_manifest(
                    RelayBridgeConfig(),
                    ("color_image", "odom", "global_costmap", "tele_cmd_vel"),
                )
                module = RelayBridgeModule(
                    local_port=0,
                    open_browser=False,
                    web_build=False,
                    robot_id=robot_id,
                    manifest=manifest,
                )
            else:
                module = RelayBridgeModule(
                    local_port=0, open_browser=False, web_build=False, robot_id=robot_id
                )
            transports = (
                pLCMTransport("/rb_e2e/odom"),
                pLCMTransport("/rb_e2e/color_image"),
                pLCMTransport("/rb_e2e/global_costmap"),
            )
            for transport in transports:
                transport.start()
            module.odom.transport = transports[0]
            module.color_image.transport = transports[1]
            module.global_costmap.transport = transports[2]
            module.start()
            yield module
        finally:
            RelayE2E.stop(module, transports)

    @staticmethod
    @contextmanager
    def external_relay_bridge(
        robot_id: str,
    ) -> Iterator[tuple[RelayProcess, RelayBridgeModule]]:
        """Teleop bridge attached to an already-spawned relay (no child watchdog)."""
        from dimos.web.relay_bridge.relay_bridge_module import (
            RelayBridgeConfig,
            RelayBridgeModule,
            default_manifest,
        )
        from dimos.web.relay_bridge.relay_process import RelayProcess

        RelayE2E.require_deno()
        relay: RelayProcess | None = None
        module: RelayBridgeModule | None = None
        try:
            relay = RelayProcess()
            ready = relay.start()
            manifest = default_manifest(RelayBridgeConfig(), ("tele_cmd_vel",))
            module = RelayBridgeModule(
                relay_url=ready.open_url,
                open_browser=False,
                web_build=False,
                robot_id=robot_id,
                manifest=manifest,
            )
            module.start()
            yield relay, module
        finally:
            RelayE2E.stop(module, relay=relay)

    @staticmethod
    async def next_control(client: RelayClient, timeout: float) -> Msg | DataFrame | None:
        try:
            return await asyncio.wait_for(client._session.control_msgs.get(), timeout)
        except asyncio.TimeoutError:
            return None

    @staticmethod
    async def attach_viewer(
        viewer: RelayClient, robot_id: str, chs: Sequence[str], timeout: float = 10.0
    ) -> None:
        """Watch a robot and subscribe channels after the manifest confirms the watch."""
        deadline = time.monotonic() + timeout
        while True:
            viewer.send_control(Watch(robotId=robot_id))
            msg = await RelayE2E.next_control(viewer, 0.5)
            while msg is not None and not isinstance(msg, Manifest):
                msg = await RelayE2E.next_control(viewer, 0.5)
            if isinstance(msg, Manifest):
                break
            if time.monotonic() >= deadline:
                raise TimeoutError(f"no manifest for {robot_id} within {timeout} s")
        for ch in chs:
            viewer.send_control(Sub(ch=ch))

    @staticmethod
    async def arm_teleop(viewer: RelayClient, timeout: float = 10.0) -> None:
        """Acquire the robot's teleop lease (retrying: the Python viewer's control
        plane is datagrams, so the teleop_started ack can be lost)."""
        deadline = time.monotonic() + timeout
        while True:
            viewer.send_control(TeleopStart())
            msg = await RelayE2E.next_control(viewer, 0.5)
            while msg is not None and not isinstance(msg, TeleopStarted):
                msg = await RelayE2E.next_control(viewer, 0.5)
            if isinstance(msg, TeleopStarted):
                return
            if time.monotonic() >= deadline:
                raise TimeoutError(f"teleop lease not granted within {timeout} s")

    @staticmethod
    async def wait_subs(
        robot: RelayClient, chs: set[str], timeout: float = 10.0, *, exact: bool = False
    ) -> None:
        """Wait for a subscription snapshot covering, or exactly matching, ``chs``."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            msg = await RelayE2E.next_control(robot, deadline - time.monotonic())
            if not isinstance(msg, Subs):
                continue
            if set(msg.chs) == chs if exact else set(msg.chs) >= chs:
                return
        raise TimeoutError(f"no subs snapshot covering {chs} within {timeout} s")

    @staticmethod
    async def collect_until(
        viewer: RelayClient,
        done: Callable[[list[DataFrame]], bool],
        timeout: float = 10.0,
    ) -> list[DataFrame]:
        """Consume viewer frames until ``done`` succeeds or the timeout expires."""
        frames: list[DataFrame] = []

        async def consume() -> None:
            async for frame in viewer.frames():
                frames.append(frame)
                if done(frames):
                    return

        try:
            await asyncio.wait_for(consume(), timeout)
        except asyncio.TimeoutError:
            pass
        return frames


# Existing unit tests import these names; they are the class methods.
stop_module = RelayE2E.stop
next_control = RelayE2E.next_control
attach_viewer = RelayE2E.attach_viewer
arm_teleop = RelayE2E.arm_teleop
wait_subs = RelayE2E.wait_subs
collect_until = RelayE2E.collect_until
