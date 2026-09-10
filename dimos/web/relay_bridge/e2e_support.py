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
from collections.abc import Callable, Sequence
import threading
import time
from typing import TYPE_CHECKING

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


def stop_module(module: RelayBridgeModule) -> None:
    """module.stop(), then reap the loop's to_thread executor threads.

    The framework never shuts down the loop's default executor, so tests that
    drive a to_thread path (spawn/stop of a relay child) would trip the
    conftest thread-leak check on the idle asyncio_* workers.

    Fixtures wrap this in ``try``/``finally`` so hand-wired LCM transports
    still stop if ``module.stop()`` raises. After a killed relay child the
    loop can still be running; ``_reap_loop`` shuts it down from the loop
    thread instead of calling ``run_until_complete`` here.
    """
    loop: asyncio.AbstractEventLoop | None = getattr(module, "_loop", None)
    loop_thread: threading.Thread | None = getattr(module, "_loop_thread", None)
    try:
        module.stop()
    finally:
        _reap_loop(loop, loop_thread)


def _reap_loop(
    loop: asyncio.AbstractEventLoop | None, loop_thread: threading.Thread | None
) -> None:
    """Shut the default executor and join leftover run_forever threads.

    ``Module.stop`` can return while the loop is still running (join timeout
    after a killed relay child). ``run_until_complete`` then raises
    "This event loop is already running"; schedule executor shutdown on the
    loop thread instead, then ``loop.stop()``.
    """
    if loop is not None and not loop.is_closed():

        def _cancel_tasks() -> None:
            for task in asyncio.all_tasks(loop):
                task.cancel()

        try:
            if loop.is_running():
                loop.call_soon_threadsafe(_cancel_tasks)
                future = asyncio.run_coroutine_threadsafe(loop.shutdown_default_executor(), loop)
                future.result(timeout=5.0)
                loop.call_soon_threadsafe(loop.stop)
            else:
                loop.run_until_complete(loop.shutdown_default_executor())
        except (RuntimeError, TimeoutError):
            if loop.is_running():
                loop.call_soon_threadsafe(loop.stop)
    if loop_thread is not None and loop_thread.is_alive():
        loop_thread.join(timeout=5.0)


async def next_control(client: RelayClient, timeout: float) -> Msg | DataFrame | None:
    try:
        return await asyncio.wait_for(client._session.control_msgs.get(), timeout)
    except asyncio.TimeoutError:
        return None


async def attach_viewer(
    viewer: RelayClient, robot_id: str, chs: Sequence[str], timeout: float = 10.0
) -> None:
    """Watch a robot and subscribe channels after the manifest confirms the watch."""
    deadline = time.monotonic() + timeout
    while True:
        viewer.send_control(Watch(robotId=robot_id))
        msg = await next_control(viewer, 0.5)
        while msg is not None and not isinstance(msg, Manifest):
            msg = await next_control(viewer, 0.5)
        if isinstance(msg, Manifest):
            break
        if time.monotonic() >= deadline:
            raise TimeoutError(f"no manifest for {robot_id} within {timeout} s")
    for ch in chs:
        viewer.send_control(Sub(ch=ch))


async def arm_teleop(viewer: RelayClient, timeout: float = 10.0) -> None:
    """Acquire the robot's teleop lease (retrying: the Python viewer's control
    plane is datagrams, so the teleop_started ack can be lost)."""
    deadline = time.monotonic() + timeout
    while True:
        viewer.send_control(TeleopStart())
        msg = await next_control(viewer, 0.5)
        while msg is not None and not isinstance(msg, TeleopStarted):
            msg = await next_control(viewer, 0.5)
        if isinstance(msg, TeleopStarted):
            return
        if time.monotonic() >= deadline:
            raise TimeoutError(f"teleop lease not granted within {timeout} s")


async def wait_subs(
    robot: RelayClient, chs: set[str], timeout: float = 10.0, *, exact: bool = False
) -> None:
    """Wait for a subscription snapshot covering, or exactly matching, ``chs``."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        msg = await next_control(robot, deadline - time.monotonic())
        if not isinstance(msg, Subs):
            continue
        if set(msg.chs) == chs if exact else set(msg.chs) >= chs:
            return
    raise TimeoutError(f"no subs snapshot covering {chs} within {timeout} s")


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
