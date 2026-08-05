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

"""Shared logic for sending natural language commands and waiting for responses."""

from __future__ import annotations

from collections.abc import Callable
import json
import queue
import threading
import time
from typing import Any

from dimos.core.transport import pZenohTransport
from dimos.core.transport_factory import make_transport
from dimos.utils.logging_config import setup_logger

logger = setup_logger()


def _print_langchain_msg(msg: object, quiet: bool, write: Callable[[str], None]) -> None:
    """Pretty-print a LangChain message."""
    from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

    if isinstance(msg, AIMessage):
        content = getattr(msg, "content", "") or ""
        tool_calls = (
            getattr(msg, "tool_calls", None) or msg.additional_kwargs.get("tool_calls", [])  # type: ignore[union-attr]
        )
        if content:
            write(f"\n{content}")
        if tool_calls:
            for tc in tool_calls:
                name = tc.get("name", "?")
                args = tc.get("args", {})
                args_str = json.dumps(args, ensure_ascii=False)
                if len(args_str) > 200:
                    args_str = args_str[:200] + "..."
                write(f"  -> {name}({args_str})")
    elif isinstance(msg, ToolMessage):
        content = str(getattr(msg, "content", ""))
        if not quiet and content:
            write(f"  <- {content}")
    elif isinstance(msg, HumanMessage):
        content = str(getattr(msg, "content", ""))
        if content:
            write(f"  [echo] {content}")
    elif isinstance(msg, SystemMessage):
        content = str(getattr(msg, "content", ""))
        if not quiet and content:
            write(f"  [system] {content}")
    else:
        if not quiet:
            write(f"  [{type(msg).__name__}] {str(msg)[:300]}")


def tell_robot(
    message: str,
    timeout: float = 60.0,
    quiet: bool = False,
    write: Callable[[str], None] | None = None,
) -> int:
    """Send a natural language message to the robot agent and wait for the response.

    Publishes to /human_input and subscribes to /agent + /agent_idle to
    stream responses synchronously.

    Args:
        message: Natural language message to send.
        timeout: Max seconds to wait for a response.
        quiet: Suppress non-essential output.
        write: Callable for output (defaults to built-in print).

    Returns:
        Number of response messages received (0 = no agent responded).
    """
    if write is None:
        write = print  # type: ignore[assignment]

    if not message.strip():
        write("Error: message cannot be empty")
        return -1

    response_queue: queue.Queue[tuple[str, object]] = queue.Queue()
    done = threading.Event()

    # These streams carry pickled Python objects.  Let the transport factory
    # select pZenoh or pLCM so ``dimos tell`` uses the same configured backend
    # as the running blueprint.
    human: Any = make_transport("/human_input")
    agent: Any = make_transport("/agent")
    idle: Any = make_transport("/agent_idle")

    def _on_agent(msg: object) -> None:
        response_queue.put(("agent", msg))

    def _on_idle(is_idle: bool) -> None:
        if is_idle:
            response_queue.put(("idle", True))

    try:
        human.start()
        agent.start()
        idle.start()
    except Exception as exc:
        write(f"Error: cannot connect to transport — is DimOS running? ({exc})")
        return -1

    unsub_agent = agent.subscribe(_on_agent)
    unsub_idle = idle.subscribe(_on_idle)

    # A standalone Zenoh peer needs a brief scouting window before its first
    # publication.  Publishing immediately after opening the session can lose
    # the one-shot human command before the agent peer is discovered.
    if isinstance(human, pZenohTransport):
        time.sleep(1.0)

    if not quiet:
        write(f"Sending: {message[:200]}")
        write("---")

    human.publish(message)

    deadline = time.monotonic() + timeout
    response_count = 0
    try:
        while not done.is_set():
            try:
                msg_type, msg = response_queue.get(timeout=0.3)
            except queue.Empty:
                if time.monotonic() > deadline:
                    if not quiet:
                        write("---")
                        write("(timeout waiting for agent response)")
                    break
                continue

            if msg_type == "idle":
                done.set()
                break
            if msg_type == "agent":
                _print_langchain_msg(msg, quiet, write)
                response_count += 1
    finally:
        unsub_agent()
        unsub_idle()
        human.stop()
        agent.stop()
        idle.stop()

    if response_count == 0 and not quiet:
        write("(no response from agent — is an agent module deployed?)")

    return response_count
