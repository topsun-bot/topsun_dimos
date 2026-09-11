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

Pure-Python test suite with no hardware or network. Covers connect() error propagation,
aes_128_key forwarding, CN-cloud region kwargs, and the UNITREE_AES_128_KEY env var
via GlobalConfig.
"""

import json
from pathlib import Path
from typing import Any
from unittest.mock import ANY, AsyncMock, MagicMock, call

import pytest
from unitree_webrtc_connect.constants import DATA_CHANNEL_TYPE, RTC_TOPIC, SPORT_CMD

from dimos.constants import DEFAULT_THREAD_JOIN_TIMEOUT
from dimos.core.global_config import GlobalConfig
from dimos.msgs.geometry_msgs.Twist import Twist
from dimos.msgs.geometry_msgs.Vector3 import Vector3
from dimos.robot.unitree import connection as conn_mod
from dimos.robot.unitree.connection import UnitreeWebRTCConnection


def _stub_driver(connect_exc: Exception | None = None) -> MagicMock:
    """A LegionConnection instance double covering everything connect() touches."""
    driver = MagicMock(name="LegionConnection-instance")
    driver.connect = AsyncMock(side_effect=connect_exc)
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
        conn.loop.call_soon_threadsafe(conn.loop.stop)
        conn.thread.join(timeout=DEFAULT_THREAD_JOIN_TIMEOUT)


def test_connect_success_completes_setup(built_connection: Any) -> None:
    """Happy path: constructor returns after the setup sequence ran."""
    _conn, driver = built_connection

    driver.connect.assert_awaited_once()
    driver.datachannel.pub_sub.publish_request_new.assert_awaited_once()


@pytest.mark.parametrize(
    ("connection_options", "expected_call"),
    [
        pytest.param(
            {},
            call(
                RTC_TOPIC["WIRELESS_CONTROLLER"],
                data={"lx": 0.4, "ly": 1.5, "rx": -0.8, "ry": 0},
            ),
            id="joystick",
        ),
        pytest.param(
            {"velocity_api": True},
            call(
                RTC_TOPIC["SPORT_MOD"],
                data={
                    "header": {
                        "identity": {
                            "id": ANY,
                            "api_id": SPORT_CMD["Move"],
                        }
                    },
                    "parameter": json.dumps({"x": 1.5, "y": -0.4, "z": 0.8}),
                },
                msg_type=DATA_CHANNEL_TYPE["REQUEST"],
            ),
            id="velocity",
        ),
    ],
)
def test_move_api_toggle_sends_selected_wire_command(
    monkeypatch: pytest.MonkeyPatch,
    connection_options: dict[str, bool],
    expected_call: Any,
) -> None:
    driver = _stub_driver()
    monkeypatch.setattr(conn_mod, "LegionConnection", MagicMock(return_value=driver))
    twist = Twist(
        linear=Vector3(1.5, -0.4, 0.0),
        angular=Vector3(0.0, 0.0, 0.8),
    )

    connection = UnitreeWebRTCConnection(ip="10.0.0.99", **connection_options)
    try:
        driver.datachannel.pub_sub.publish_without_callback.reset_mock()
        assert connection.move(twist)
        assert driver.datachannel.pub_sub.publish_without_callback.call_args == expected_call
    finally:
        connection.stop_movement()
        connection.loop.call_soon_threadsafe(connection.loop.stop)
        connection.thread.join(timeout=DEFAULT_THREAD_JOIN_TIMEOUT)


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


def test_aes_key_forwarded_when_provided(stub_legion: MagicMock) -> None:
    """A provided key is forwarded verbatim to the driver."""
    UnitreeWebRTCConnection(ip="192.168.123.161", aes_128_key="aa" * 16)
    assert _aes_kwarg(stub_legion) == "aa" * 16


def test_empty_string_key_forwarded_as_falsy(stub_legion: MagicMock) -> None:
    """Empty-string key stays falsy → the driver treats it as no key."""
    UnitreeWebRTCConnection(ip="192.168.123.161", aes_128_key="")
    assert not _aes_kwarg(stub_legion)


def test_global_config_reads_unitree_aes_128_key_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """The key enters via GlobalConfig, read from the UNITREE_AES_128_KEY env var."""
    monkeypatch.setenv("UNITREE_AES_128_KEY", "ee" * 16)
    assert GlobalConfig().unitree_aes_128_key == "ee" * 16


def test_normalize_unitree_aes_key_accepts_32_hex() -> None:
    assert (
        conn_mod._normalize_unitree_aes_key("ABCDEF0123456789ABCDEF0123456789")
        == "abcdef0123456789abcdef0123456789"
    )


def test_normalize_unitree_aes_key_rejects_invalid_value() -> None:
    with pytest.raises(ValueError, match="32 hex"):
        conn_mod._normalize_unitree_aes_key("not-a-key")


def test_legion_connection_kwargs_pass_aes_options_when_supported(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class NewLegionConnection:
        def __init__(
            self,
            connectionMethod: object,
            serialNumber: str | None = None,
            ip: str | None = None,
            username: str | None = None,
            password: str | None = None,
            aes_128_key: str | None = None,
            region: str = "global",
            device_type: str = "Go2",
        ) -> None:
            pass

    monkeypatch.setattr(conn_mod, "LegionConnection", NewLegionConnection)

    kwargs = conn_mod._legion_connection_kwargs(
        "192.168.123.161",
        "ABCDEF0123456789ABCDEF0123456789",
        "cn",
        "G1",
    )

    assert kwargs == {
        "ip": "192.168.123.161",
        "aes_128_key": "abcdef0123456789abcdef0123456789",
        "region": "cn",
        "device_type": "G1",
    }


def test_legion_connection_kwargs_reject_configured_key_when_package_is_too_old(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class OldLegionConnection:
        def __init__(
            self,
            connectionMethod: object,
            serialNumber: str | None = None,
            ip: str | None = None,
            username: str | None = None,
            password: str | None = None,
        ) -> None:
            pass

    monkeypatch.setattr(conn_mod, "LegionConnection", OldLegionConnection)

    with pytest.raises(RuntimeError, match="unitree-webrtc-connect>=2.1.2"):
        conn_mod._legion_connection_kwargs(
            "192.168.123.161",
            "abcdef0123456789abcdef0123456789",
            "global",
            "Go2",
        )


def test_global_config_reads_unitree_aes_128_key_dotenv(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("UNITREE_AES_128_KEY", raising=False)
    monkeypatch.delenv("UNITREE_AES_KEY", raising=False)
    monkeypatch.delenv("DIMOS_UNITREE_WEBRTC_AES_KEY", raising=False)
    (tmp_path / ".env").write_text(f"UNITREE_AES_128_KEY={'ab' * 16}\n")

    config = GlobalConfig()

    assert config.unitree_aes_128_key == "ab" * 16


def test_region_and_device_type_forwarded_with_key(stub_legion: MagicMock) -> None:
    UnitreeWebRTCConnection(
        ip="192.168.123.161",
        aes_128_key="aa" * 16,
        region="cn",
        device_type="G1",
    )
    assert stub_legion.call_args.kwargs["region"] == "cn"
    assert stub_legion.call_args.kwargs["device_type"] == "G1"
