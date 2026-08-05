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

from collections.abc import Callable
from typing import Any

import pytest

from dimos.robot.cli import tell as tell_module


class _FakeTransport:
    def __init__(self, topic: str, transports: dict[str, "_FakeTransport"]) -> None:
        self.topic = topic
        self.transports = transports
        self.callbacks: list[Callable[[Any], None]] = []
        self.started = False
        self.stopped = False

    def start(self) -> None:
        self.started = True

    def stop(self) -> None:
        self.stopped = True

    def subscribe(self, callback: Callable[[Any], None]) -> Callable[[], None]:
        self.callbacks.append(callback)
        return lambda: self.callbacks.remove(callback)

    def publish(self, message: str) -> None:
        assert message == "find the chair"
        for callback in list(self.transports["/agent"].callbacks):
            callback("agent response")
        for callback in list(self.transports["/agent_idle"].callbacks):
            callback(True)


def test_tell_robot_uses_configured_transport_factory(monkeypatch: pytest.MonkeyPatch) -> None:
    transports: dict[str, _FakeTransport] = {}

    def make_fake_transport(topic: str) -> _FakeTransport:
        transport = _FakeTransport(topic, transports)
        transports[topic] = transport
        return transport

    monkeypatch.setattr(tell_module, "make_transport", make_fake_transport)

    result = tell_module.tell_robot("find the chair", quiet=True)

    assert result == 1
    assert set(transports) == {"/human_input", "/agent", "/agent_idle"}
    assert all(transport.started for transport in transports.values())
    assert all(transport.stopped for transport in transports.values())
