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

"""Unit tests for UnitreeWebRTCConnection.

Pure-Python — no hardware, no network. Covers connect() error propagation,
aes_128_key forwarding, and the UNITREE_AES_128_KEY env var via GlobalConfig.
"""

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock

from aiortc import MediaStreamError
import pytest
from unitree_webrtc_connect.constants import RTC_TOPIC, WebRTCConnectionMethod

from dimos.core.global_config import GlobalConfig
from dimos.msgs.geometry_msgs.Twist import Twist
from dimos.msgs.geometry_msgs.Vector3 import Vector3
from dimos.robot.unitree import connection as conn_mod
from dimos.robot.unitree.connection import UnitreeWebRTCConnection


def _stub_driver(connect_exc: Exception | None = None) -> MagicMock:
    """A LegionConnection instance double covering everything connect() touches."""
    driver = MagicMock(name="LegionConnection-instance")
    driver.connect = AsyncMock(side_effect=connect_exc)
    driver.disconnect = AsyncMock()
    driver.datachannel.disableTrafficSaving = AsyncMock()
    driver.datachannel.set_decoder = MagicMock()
    driver.datachannel.pub_sub.publish_request_new = AsyncMock()
    return driver


def test_connect_failure_propagates_to_caller(monkeypatch: pytest.MonkeyPatch) -> None:
    """A driver connect failure must raise from the constructor, not hang."""
    driver = _stub_driver(connect_exc=RuntimeError("aes_128_key required (data2=3)"))
    monkeypatch.setattr(conn_mod, "LegionConnection", MagicMock(return_value=driver))

    with pytest.raises(RuntimeError, match="aes_128_key required"):
        UnitreeWebRTCConnection(ip="10.0.0.99")


@pytest.fixture
def built_connection(monkeypatch: pytest.MonkeyPatch) -> Any:
    """A live UnitreeWebRTCConnection over a stubbed driver, torn down (loop
    stopped, thread joined) unconditionally so a failed assert can't leak it."""
    driver = _stub_driver()
    monkeypatch.setattr(conn_mod, "LegionConnection", MagicMock(return_value=driver))

    conn = UnitreeWebRTCConnection(ip="10.0.0.99")
    try:
        yield conn, driver
    finally:
        if conn.loop.is_running():
            conn.stop()


def test_connect_success_completes_setup(built_connection: Any) -> None:
    """Happy path: constructor returns after the setup sequence ran."""
    _conn, driver = built_connection

    driver.connect.assert_awaited_once()
    driver.datachannel.pub_sub.publish_request_new.assert_awaited_once()
    driver.datachannel.pub_sub.publish_without_callback.assert_any_call(
        RTC_TOPIC["WIRELESS_CONTROLLER"],
        data={"lx": 0, "ly": 0, "rx": 0, "ry": 0},
    )


def test_stop_disconnects_even_when_zero_velocity_send_fails(built_connection: Any) -> None:
    """A closed DataChannel must not prevent the aiortc disconnect coroutine."""
    conn, driver = built_connection
    driver.datachannel.pub_sub.publish_without_callback.side_effect = RuntimeError(
        "Data channel is not open"
    )

    conn.stop()

    driver.disconnect.assert_awaited_once()


def test_video_track_end_is_a_clean_terminal_state(built_connection: Any) -> None:
    """Remote media-track closure must not escape as an unhandled callback error."""
    conn, driver = built_connection
    observable = conn.raw_video_stream()
    track_callback = driver.video.add_track_callback.call_args.args[0]
    track = MagicMock()
    track.recv = AsyncMock(side_effect=MediaStreamError)

    asyncio.run(track_callback(track))

    subscription = observable.subscribe()
    subscription.dispose()


def test_trace_loop_heartbeat_records_delay_and_reschedules() -> None:
    connection = UnitreeWebRTCConnection.__new__(UnitreeWebRTCConnection)
    connection.loop = MagicMock()
    connection.loop.time.side_effect = [10.0, 10.102]
    connection.loop.is_running.return_value = True
    connection._trace_heartbeat_interval_sec = 0.1
    connection._trace_heartbeat_handle = None
    connection._navigation_trace = MagicMock()
    connection._navigation_trace.accepts.return_value = True

    connection._start_trace_loop_heartbeat()
    first_call = connection.loop.call_at.call_args_list[0]
    assert first_call.args[0] == pytest.approx(10.1)

    connection._trace_loop_heartbeat(10.1)

    event, fields = connection._navigation_trace.record.call_args.args
    assert event == "webrtc_loop_heartbeat"
    assert fields["delay_ns"] == pytest.approx(2_000_000, abs=1)
    assert connection.loop.call_at.call_args_list[1].args[0] == pytest.approx(10.202)


def test_move_uses_wireless_controller_joystick_backend(
    built_connection: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Navigation velocity must stay on jtlinux's simulated joystick channel."""
    conn, driver = built_connection
    timer = MagicMock()
    monkeypatch.setattr(conn_mod.threading, "Timer", MagicMock(return_value=timer))
    twist = Twist(
        linear=Vector3(0.4, -0.2, 0.0),
        angular=Vector3(0.0, 0.0, 0.3),
    )

    assert conn.move(twist) is True

    driver.datachannel.pub_sub.publish_without_callback.assert_any_call(
        RTC_TOPIC["WIRELESS_CONTROLLER"],
        data={"lx": 0.2, "ly": 0.4, "rx": -0.3, "ry": 0},
    )
    # SPORT_MOD was used once during connect for the motion-mode switch. A move
    # must not add another sport RPC request.
    driver.datachannel.pub_sub.publish_request_new.assert_awaited_once()
    timer.start.assert_called_once_with()


@pytest.fixture
def stub_legion(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    """Replace LegionConnection with a mock and no-op connect() so __init__
    stays inside the aes_128_key resolution without dialing out."""
    monkeypatch.setattr(UnitreeWebRTCConnection, "connect", lambda self: None)
    legion = MagicMock(name="LegionConnection")
    monkeypatch.setattr(conn_mod, "LegionConnection", legion)
    return legion


def _aes_kwarg(legion: MagicMock) -> Any:
    """The aes_128_key passed to LegionConnection, or None if absent."""
    return legion.call_args.kwargs.get("aes_128_key")


def test_no_key_forwards_falsy(stub_legion: MagicMock) -> None:
    """No key → a falsy value reaches the driver, which treats it as no key."""
    UnitreeWebRTCConnection(ip="192.168.123.161")
    assert not _aes_kwarg(stub_legion)
    assert stub_legion.call_args.args[0] == WebRTCConnectionMethod.LocalSTA


def test_aes_key_forwarded_when_provided(stub_legion: MagicMock) -> None:
    """A provided key is forwarded verbatim to the driver."""
    UnitreeWebRTCConnection(ip="192.168.123.161", aes_128_key="aa" * 16)
    assert _aes_kwarg(stub_legion) == "aa" * 16


def test_empty_string_key_forwarded_as_falsy(stub_legion: MagicMock) -> None:
    """Empty-string key stays falsy → the driver treats it as no key."""
    UnitreeWebRTCConnection(ip="192.168.123.161", aes_128_key="")
    assert not _aes_kwarg(stub_legion)


def test_remote_requires_credentials(stub_legion: MagicMock) -> None:
    """Remote construction fails fast without cloud credentials."""
    with pytest.raises(ValueError, match="unitree_username"):
        UnitreeWebRTCConnection(connection_method="remote")


def test_remote_uses_remote_method_and_shared_ice(
    stub_legion: MagicMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Remote path selects WebRTCConnectionMethod.Remote and applies ICE patch."""
    called: list[bool] = []
    monkeypatch.setattr(conn_mod, "_ensure_shared_ice_credentials", lambda: called.append(True))
    UnitreeWebRTCConnection(
        connection_method="remote",
        username="u",
        password="p",
        serial_number="SN",
        region="cn",
    )
    assert called == [True]
    assert stub_legion.call_args.args[0] == WebRTCConnectionMethod.Remote
    assert stub_legion.call_args.kwargs["serialNumber"] == "SN"


def test_global_config_reads_unitree_aes_128_key_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """The key enters via GlobalConfig, read from the UNITREE_AES_128_KEY env var."""
    monkeypatch.setenv("UNITREE_AES_128_KEY", "ee" * 16)
    assert GlobalConfig().unitree_aes_128_key == "ee" * 16


def test_global_config_reads_remote_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("UNITREE_WEBRTC_METHOD", "remote")
    monkeypatch.setenv("UNITREE_USERNAME", "15200000000")
    monkeypatch.setenv("UNITREE_SERIAL", "B42TEST")
    monkeypatch.setenv("UNITREE_REGION", "cn")
    g = GlobalConfig()
    assert g.unitree_webrtc_method == "remote"
    assert g.unitree_username == "15200000000"
    assert g.unitree_serial == "B42TEST"
    assert g.unitree_region == "cn"
