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

from typing import Any

import pytest

from dimos.core.global_config import GlobalConfig
import dimos.robot.unitree.connection as unitree_connection
from dimos.robot.unitree.connection import UnitreeWebRTCConnection


class _StubLegionConnection:
    calls: list[tuple[tuple[Any, ...], dict[str, Any]]] = []

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
        self.__class__.calls.append(
            (
                (connectionMethod,),
                {
                    "serialNumber": serialNumber,
                    "ip": ip,
                    "username": username,
                    "password": password,
                    "aes_128_key": aes_128_key,
                    "region": region,
                    "device_type": device_type,
                },
            )
        )


@pytest.fixture
def stub_legion(monkeypatch: pytest.MonkeyPatch) -> type[_StubLegionConnection]:
    monkeypatch.setattr(UnitreeWebRTCConnection, "connect", lambda self: None)
    monkeypatch.setattr(unitree_connection, "LegionConnection", _StubLegionConnection)
    _StubLegionConnection.calls.clear()
    return _StubLegionConnection


def _last_legion_kwargs(stub: type[_StubLegionConnection]) -> dict[str, Any]:
    return stub.calls[-1][1]


def test_normalize_unitree_aes_key_accepts_32_hex() -> None:
    assert (
        unitree_connection._normalize_unitree_aes_key("ABCDEF0123456789ABCDEF0123456789")
        == "abcdef0123456789abcdef0123456789"
    )


def test_normalize_unitree_aes_key_rejects_invalid_value() -> None:
    with pytest.raises(ValueError, match="32 hex"):
        unitree_connection._normalize_unitree_aes_key("not-a-key")


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

    monkeypatch.setattr(unitree_connection, "LegionConnection", NewLegionConnection)

    kwargs = unitree_connection._legion_connection_kwargs(
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

    monkeypatch.setattr(unitree_connection, "LegionConnection", OldLegionConnection)

    with pytest.raises(RuntimeError, match="unitree-webrtc-connect>=2.1.2"):
        unitree_connection._legion_connection_kwargs(
            "192.168.123.161",
            "abcdef0123456789abcdef0123456789",
            "global",
            "Go2",
        )


def test_global_config_reads_unitree_aes_128_key_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("UNITREE_AES_128_KEY", "abcdef0123456789abcdef0123456789")

    config = GlobalConfig()

    assert config.unitree_webrtc_aes_key == "abcdef0123456789abcdef0123456789"


def test_aes_key_omitted_when_neither_kwarg_nor_env(
    stub_legion: type[_StubLegionConnection],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("UNITREE_AES_128_KEY", raising=False)
    monkeypatch.delenv("UNITREE_AES_KEY", raising=False)
    monkeypatch.delenv("DIMOS_UNITREE_WEBRTC_AES_KEY", raising=False)

    UnitreeWebRTCConnection(ip="192.168.123.161")

    assert _last_legion_kwargs(stub_legion)["aes_128_key"] is None


def test_aes_key_from_explicit_kwarg(
    stub_legion: type[_StubLegionConnection],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("UNITREE_AES_128_KEY", raising=False)

    UnitreeWebRTCConnection(ip="192.168.123.161", aes_128_key="aa" * 16)

    assert _last_legion_kwargs(stub_legion)["aes_128_key"] == "aa" * 16


def test_aes_key_from_env_when_kwarg_none(
    stub_legion: type[_StubLegionConnection],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("UNITREE_AES_128_KEY", "bb" * 16)

    UnitreeWebRTCConnection(ip="192.168.123.161")

    assert _last_legion_kwargs(stub_legion)["aes_128_key"] == "bb" * 16


def test_explicit_kwarg_beats_env(
    stub_legion: type[_StubLegionConnection],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("UNITREE_AES_128_KEY", "bb" * 16)

    UnitreeWebRTCConnection(ip="192.168.123.161", aes_128_key="cc" * 16)

    assert _last_legion_kwargs(stub_legion)["aes_128_key"] == "cc" * 16


def test_empty_string_kwarg_uses_env_when_set(
    stub_legion: type[_StubLegionConnection],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("UNITREE_AES_128_KEY", "dd" * 16)

    UnitreeWebRTCConnection(ip="192.168.123.161", aes_128_key="")

    assert _last_legion_kwargs(stub_legion)["aes_128_key"] == "dd" * 16
