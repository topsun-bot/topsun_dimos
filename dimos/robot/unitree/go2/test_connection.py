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

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from dimos.robot.unitree.go2 import connection as go2_connection


@pytest.fixture
def stub_webrtc(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    stub = MagicMock(name="UnitreeWebRTCConnection")
    monkeypatch.setattr(go2_connection, "UnitreeWebRTCConnection", stub)
    return stub


def test_make_connection_webrtc_forwards_aes_128_key(stub_webrtc: MagicMock) -> None:
    cfg = SimpleNamespace(
        unitree_connection_type="webrtc",
        unitree_cloud_region="global",
        unitree_webrtc_connect_timeout_sec=30.0,
    )

    go2_connection.make_connection("192.168.123.161", cfg, aes_128_key="cafe" * 8)

    stub_webrtc.assert_called_once_with(
        "192.168.123.161",
        aes_128_key="cafe" * 8,
        region="global",
        device_type="Go2",
        connect_timeout_sec=30.0,
    )
