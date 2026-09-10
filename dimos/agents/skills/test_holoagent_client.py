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
from unittest.mock import MagicMock

import pytest
import requests

from dimos.agents.skills.holoagent_client import (
    HoloAgentBridgeClient,
    HoloAgentBridgeContract,
    HoloAgentBridgeError,
)


def test_format_semantic_cmd_uses_unknown_for_blanks() -> None:
    assert (
        HoloAgentBridgeContract.format_semantic_cmd("", "", "coffee machine")
        == "unknown,unknown,coffee machine"
    )
    assert (
        HoloAgentBridgeContract.format_semantic_cmd("1F", "pantry", "coffee machine")
        == "1F,pantry,coffee machine"
    )


def test_format_semantic_cmd_rejects_blank_object() -> None:
    with pytest.raises(HoloAgentBridgeError, match="object_name"):
        HoloAgentBridgeContract.format_semantic_cmd("1F", "pantry", "  ")


def test_format_relative_cmd() -> None:
    assert HoloAgentBridgeContract.format_relative_cmd(1.0, 0.0, 90.0) == "1.0,0.0,90.0"


def test_format_relative_cmd_rejects_invalid() -> None:
    with pytest.raises(HoloAgentBridgeError, match="finite"):
        HoloAgentBridgeContract.format_relative_cmd(float("nan"), 0.0, 0.0)
    with pytest.raises(HoloAgentBridgeError, match="all zero"):
        HoloAgentBridgeContract.format_relative_cmd(0.0, 0.0, 0.0)
    with pytest.raises(HoloAgentBridgeError, match="displacement"):
        HoloAgentBridgeContract.format_relative_cmd(10.0, 0.0, 0.0)
    with pytest.raises(HoloAgentBridgeError, match="rotation"):
        HoloAgentBridgeContract.format_relative_cmd(0.0, 0.0, 270.0)


def test_semantic_nav_posts_cmd_contract() -> None:
    session = MagicMock()
    response = MagicMock()
    response.content = b'{"success": true}'
    response.json.return_value = {"success": True}
    session.request.return_value = response

    client = HoloAgentBridgeClient("http://127.0.0.1:8000", session=session)
    result = client.semantic_nav("1F", "pantry", "coffee machine")

    session.request.assert_called_once_with(
        method="POST",
        url="http://127.0.0.1:8000/api/semantic_nav",
        json={"cmd": "1F,pantry,coffee machine"},
        timeout=10.0,
    )
    assert result == {"success": True}


def test_relative_nav_posts_cmd_contract() -> None:
    session = MagicMock()
    response = MagicMock()
    response.content = b'{"success": true}'
    response.json.return_value = {"success": True}
    session.request.return_value = response

    client = HoloAgentBridgeClient("http://bridge.local:8000/", session=session)
    client.relative_nav(1.0, -0.2, 90.0)

    session.request.assert_called_once_with(
        method="POST",
        url="http://bridge.local:8000/api/relative_nav",
        json={"cmd": "1.0,-0.2,90.0"},
        timeout=10.0,
    )


def test_path_endpoints() -> None:
    session = MagicMock()
    response = MagicMock()
    response.content = b'{"success": true}'
    response.json.return_value = {"success": True}
    session.request.return_value = response
    client = HoloAgentBridgeClient("http://127.0.0.1:8000", session=session)

    client.health()
    client.stop_navigation()
    client.navigation_signal("one_point_1")
    client.arm_skill("wave_above_head")

    urls = [call.kwargs["url"] for call in session.request.call_args_list]
    assert urls == [
        "http://127.0.0.1:8000/health",
        "http://127.0.0.1:8000/api/navigation/stop",
        "http://127.0.0.1:8000/api/navigation/one_point_1",
        "http://127.0.0.1:8000/api/arm/wave_above_head",
    ]


def test_empty_path_rejected() -> None:
    client = HoloAgentBridgeClient("http://127.0.0.1:8000", session=MagicMock())
    with pytest.raises(HoloAgentBridgeError, match="non-empty"):
        client.arm_skill("  ")
    with pytest.raises(HoloAgentBridgeError, match="non-empty"):
        client.navigation_signal("")


@pytest.mark.parametrize("bad", ["../stop", "a/b", "foo?x=1", "bar#frag", "has space"])
def test_unsafe_path_token_rejected(bad: str) -> None:
    client = HoloAgentBridgeClient("http://127.0.0.1:8000", session=MagicMock())
    with pytest.raises(HoloAgentBridgeError, match="single token"):
        client.arm_skill(bad)
    with pytest.raises(HoloAgentBridgeError, match="single token"):
        client.navigation_signal(bad)


def test_http_error_is_wrapped() -> None:
    session = MagicMock()
    session.request.side_effect = requests.ConnectionError("down")
    client = HoloAgentBridgeClient("http://127.0.0.1:8000", session=session)

    with pytest.raises(HoloAgentBridgeError, match="GET http://127.0.0.1:8000/health failed"):
        client.health()


def test_from_global_config_uses_passed_config() -> None:
    from dimos.core.global_config import GlobalConfig

    client = HoloAgentBridgeClient.from_global_config(
        GlobalConfig(holoagent_url="http://10.9.8.7:8000/")
    )
    assert client.base_url == "http://10.9.8.7:8000"


def test_non_json_body_is_wrapped() -> None:
    session = MagicMock()
    response = MagicMock()
    response.content = b"ok"
    response.text = "ok"
    response.json.side_effect = ValueError("not json")
    session.request.return_value = response

    result: dict[str, Any] = HoloAgentBridgeClient(
        "http://127.0.0.1:8000", session=session
    ).stop_navigation()
    assert result == {"success": True, "text": "ok"}


def test_requests_is_core_runtime_dependency() -> None:
    """HoloAgent client imports requests at module load; it must not be extra-only."""
    from pathlib import Path

    pyproject = Path(__file__).resolve().parents[3] / "pyproject.toml"
    project_section = pyproject.read_text(encoding="utf-8").split(
        "[project.optional-dependencies]", 1
    )[0]
    assert '"requests>=2.28"' in project_section
