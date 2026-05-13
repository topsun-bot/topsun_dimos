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

from __future__ import annotations

import threading
import time
from typing import Any

from unitree_webrtc_connect.constants import RTC_TOPIC

from dimos.msgs.geometry_msgs.Twist import Twist
from dimos.robot.unitree.go2.connection import (
    GO2_RISE_SIT_API_ID,
    GO2_SIT_API_ID,
    ConnectionConfig,
    GO2Connection,
)


class FakeGo2Connection:
    def __init__(self) -> None:
        self.requests: list[tuple[str, dict[str, Any]]] = []
        self.moves: list[tuple[Twist, float]] = []
        self.balance_stand_count = 0

    def publish_request(self, topic: str, data: dict[str, Any]) -> dict[str, str]:
        self.requests.append((topic, data))
        return {"status": "ok"}

    def move(self, twist: Twist, duration: float = 0.0) -> bool:
        self.moves.append((twist, duration))
        return True

    def balance_stand(self) -> bool:
        self.balance_stand_count += 1
        return True


def make_connection_under_test() -> GO2Connection:
    connection = GO2Connection.__new__(GO2Connection)
    connection.config = ConnectionConfig(
        ip="fake",
        idle_rest_after_startup_s=0.05,
        idle_rest_poll_s=0.005,
        idle_rest_resume_s=0.0,
    )
    connection.connection = FakeGo2Connection()
    connection._last_action_at = time.monotonic()
    connection._idle_rest_stop = threading.Event()
    connection._idle_rest_lock = threading.Lock()
    connection._idle_resting = False
    connection._idle_rest_thread = None
    return connection


def wait_for_request(fake: FakeGo2Connection, api_id: int, timeout_s: float = 1.0) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if (RTC_TOPIC["SPORT_MOD"], {"api_id": api_id}) in fake.requests:
            return True
        time.sleep(0.005)
    return False


def test_idle_rest_sends_sit_after_startup_timeout() -> None:
    connection = make_connection_under_test()
    fake = connection.connection
    assert isinstance(fake, FakeGo2Connection)

    connection._start_idle_rest_monitor()
    try:
        assert wait_for_request(fake, GO2_SIT_API_ID)
    finally:
        connection._stop_idle_rest_monitor()


def test_mark_action_resets_idle_rest_timer() -> None:
    connection = make_connection_under_test()
    fake = connection.connection
    assert isinstance(fake, FakeGo2Connection)

    connection._start_idle_rest_monitor()
    try:
        time.sleep(0.03)
        connection._mark_action()
        time.sleep(0.03)
        idle_sit_request = (RTC_TOPIC["SPORT_MOD"], {"api_id": GO2_SIT_API_ID})
        assert idle_sit_request not in fake.requests
        assert wait_for_request(fake, GO2_SIT_API_ID)
    finally:
        connection._stop_idle_rest_monitor()


def test_motion_resumes_from_idle_rest_before_move() -> None:
    connection = make_connection_under_test()
    fake = connection.connection
    assert isinstance(fake, FakeGo2Connection)
    connection._idle_resting = True

    twist = Twist(linear=(1.0, 0.0, 0.0), angular=(0.0, 0.0, 0.0))

    assert connection.move(twist)

    rise_sit_request = (RTC_TOPIC["SPORT_MOD"], {"api_id": GO2_RISE_SIT_API_ID})
    assert fake.requests == [rise_sit_request]
    assert fake.balance_stand_count == 1
    assert fake.moves == [(twist, 0.0)]
    assert not connection._idle_resting
