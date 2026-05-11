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

"""End-to-end CLI tests using ``typer.testing.CliRunner``.

These tests cover the *error paths* of the MCP-talking commands (which the
``slow``-marked tests in ``dimos/core/test_mcp_integration.py`` never exercise
because they always have a real server) plus the previously untested
``list`` and ``show-config`` commands.

HTTP traffic is mocked with ``requests-mock`` so tests run in <50 ms each and
don't depend on any running daemon or registry state.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest
import requests
import requests_mock as requests_mock_pkg
from typer.testing import CliRunner

from dimos.core import run_registry
from dimos.core.global_config import global_config
from dimos.robot.cli.dimos import main

if TYPE_CHECKING:
    from pathlib import Path

MCP_URL = f"http://localhost:{global_config.mcp_port}/mcp"


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _isolated_registry(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Redirect the run registry to a tmp dir so tests can't see real runs."""
    monkeypatch.setattr(run_registry, "REGISTRY_DIR", tmp_path)


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


def _jsonrpc_ok(result: object) -> dict[str, object]:
    return {"jsonrpc": "2.0", "id": "x", "result": result}


def _jsonrpc_err(message: str, code: int = -32000) -> dict[str, object]:
    return {"jsonrpc": "2.0", "id": "x", "error": {"code": code, "message": message}}


def _text_content(text: str) -> dict[str, object]:
    return {"content": [{"type": "text", "text": text}]}


# ---------------------------------------------------------------------------
# `dimos list`
# ---------------------------------------------------------------------------


class TestList:
    def test_list_outputs_blueprints_sorted(
        self, runner: CliRunner, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Blueprints should be printed in alphabetical order."""
        from dimos.robot import all_blueprints as all_blueprints_mod

        # Sentinel values are fine — the command only reads the dict keys.
        fake = {"zeta-robot": object(), "alpha-robot": object(), "mu-robot": object()}
        monkeypatch.setattr(all_blueprints_mod, "all_blueprints", fake)

        result = runner.invoke(main, ["list"])
        assert result.exit_code == 0
        lines = [line for line in result.output.splitlines() if line.strip()]
        assert lines == ["alpha-robot", "mu-robot", "zeta-robot"]

    def test_list_hides_demo_blueprints(
        self, runner: CliRunner, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Names starting with ``demo-`` are reserved for internal demos and
        must not show up in the user-facing list."""
        from dimos.robot import all_blueprints as all_blueprints_mod

        fake = {
            "real-robot": object(),
            "demo-hidden": object(),
            "demo-also-hidden": object(),
        }
        monkeypatch.setattr(all_blueprints_mod, "all_blueprints", fake)

        result = runner.invoke(main, ["list"])
        assert result.exit_code == 0
        assert "real-robot" in result.output
        assert "demo-hidden" not in result.output
        assert "demo-also-hidden" not in result.output


# ---------------------------------------------------------------------------
# `dimos show-config`
# ---------------------------------------------------------------------------


class TestShowConfig:
    def test_show_config_prints_all_fields(self, runner: CliRunner) -> None:
        """Every field on GlobalConfig should appear on its own ``key: value`` line."""
        result = runner.invoke(main, ["show-config"])
        assert result.exit_code == 0
        # Spot-check several stable fields rather than asserting on the full dump
        # (the full dump would couple this test to every GlobalConfig change).
        for field in ("robot_ip", "simulation", "replay", "viewer", "mcp_port"):
            assert f"{field}:" in result.output, f"missing field {field!r} in output"


# ---------------------------------------------------------------------------
# `dimos mcp list-tools` — error paths
# ---------------------------------------------------------------------------


class TestMcpListToolsErrors:
    def test_connection_error_when_no_server(
        self, runner: CliRunner, requests_mock: requests_mock_pkg.Mocker
    ) -> None:
        """If the MCP server is unreachable, exit 1 with a helpful message."""
        requests_mock.post(MCP_URL, exc=requests.ConnectionError)

        result = runner.invoke(main, ["mcp", "list-tools"])
        assert result.exit_code == 1
        assert "no running MCP server" in result.output

    def test_jsonrpc_error_surfaced(
        self, runner: CliRunner, requests_mock: requests_mock_pkg.Mocker
    ) -> None:
        """JSON-RPC error responses should be surfaced as a non-zero exit."""
        requests_mock.post(MCP_URL, json=_jsonrpc_err("internal explosion"))

        result = runner.invoke(main, ["mcp", "list-tools"])
        assert result.exit_code == 1
        assert "internal explosion" in result.output

    def test_success_prints_tools_as_json(
        self, runner: CliRunner, requests_mock: requests_mock_pkg.Mocker
    ) -> None:
        """On success, the tool list should be pretty-printed JSON."""
        requests_mock.post(MCP_URL, json=_jsonrpc_ok({"tools": [{"name": "echo"}]}))

        result = runner.invoke(main, ["mcp", "list-tools"])
        assert result.exit_code == 0
        # Must be valid JSON we can round-trip
        assert json.loads(result.output) == [{"name": "echo"}]


# ---------------------------------------------------------------------------
# `dimos mcp call`
# ---------------------------------------------------------------------------


class TestMcpCall:
    def test_call_with_kv_args(
        self, runner: CliRunner, requests_mock: requests_mock_pkg.Mocker
    ) -> None:
        """`--arg key=value` should be parsed and forwarded as the tool args."""
        captured: list[dict] = []

        def _handler(request, context):
            captured.append(request.json())
            return _jsonrpc_ok(_text_content("ok"))

        requests_mock.post(MCP_URL, json=_handler)

        result = runner.invoke(
            main,
            ["mcp", "call", "echo", "--arg", "message=hi", "--arg", "count=3"],
        )
        assert result.exit_code == 0
        assert result.output.strip() == "ok"
        # The server saw the right arguments (int auto-decoded from JSON, str preserved)
        sent_args = captured[0]["params"]["arguments"]
        assert sent_args == {"message": "hi", "count": 3}

    def test_call_with_json_args(
        self, runner: CliRunner, requests_mock: requests_mock_pkg.Mocker
    ) -> None:
        """`--json-args` should take precedence over `--arg` and parse the JSON."""
        captured: list[dict] = []

        def _handler(request, context):
            captured.append(request.json())
            return _jsonrpc_ok(_text_content("done"))

        requests_mock.post(MCP_URL, json=_handler)

        result = runner.invoke(
            main,
            ["mcp", "call", "move", "--json-args", '{"x": 0.5, "duration": 2.0}'],
        )
        assert result.exit_code == 0
        sent_args = captured[0]["params"]["arguments"]
        assert sent_args == {"x": 0.5, "duration": 2.0}

    def test_call_bad_json_args_exits_with_error(
        self, runner: CliRunner, requests_mock: requests_mock_pkg.Mocker
    ) -> None:
        """Malformed `--json-args` should not even try to reach the server."""
        # No matcher needed — the command should fail before any HTTP call,
        # so requests_mock should see zero requests.
        result = runner.invoke(main, ["mcp", "call", "move", "--json-args", "{not_json"])
        assert result.exit_code == 1
        assert "invalid JSON" in result.output
        assert requests_mock.call_count == 0

    def test_call_no_server(
        self, runner: CliRunner, requests_mock: requests_mock_pkg.Mocker
    ) -> None:
        requests_mock.post(MCP_URL, exc=requests.ConnectionError)
        result = runner.invoke(main, ["mcp", "call", "echo", "--arg", "msg=hi"])
        assert result.exit_code == 1
        assert "no running MCP server" in result.output

    def test_call_jsonrpc_error(
        self, runner: CliRunner, requests_mock: requests_mock_pkg.Mocker
    ) -> None:
        requests_mock.post(MCP_URL, json=_jsonrpc_err("tool not found"))
        result = runner.invoke(main, ["mcp", "call", "nope"])
        assert result.exit_code == 1
        assert "tool not found" in result.output

    def test_call_empty_content_shows_placeholder(
        self, runner: CliRunner, requests_mock: requests_mock_pkg.Mocker
    ) -> None:
        """A successful call with no content items should print ``(no output)``."""
        requests_mock.post(MCP_URL, json=_jsonrpc_ok({"content": []}))
        result = runner.invoke(main, ["mcp", "call", "echo"])
        assert result.exit_code == 0
        assert "(no output)" in result.output


# ---------------------------------------------------------------------------
# `dimos mcp status` / `dimos mcp modules` — share the same call_tool_text path
# ---------------------------------------------------------------------------


class TestMcpStatusModules:
    def test_status_pretty_prints_json(
        self, runner: CliRunner, requests_mock: requests_mock_pkg.Mocker
    ) -> None:
        """`mcp status` should pretty-print server_status JSON."""
        payload = json.dumps({"pid": 12345, "modules": ["A", "B"]})
        requests_mock.post(MCP_URL, json=_jsonrpc_ok(_text_content(payload)))

        result = runner.invoke(main, ["mcp", "status"])
        assert result.exit_code == 0
        assert json.loads(result.output) == {"pid": 12345, "modules": ["A", "B"]}

    def test_status_falls_back_to_raw_when_not_json(
        self, runner: CliRunner, requests_mock: requests_mock_pkg.Mocker
    ) -> None:
        """If the server text payload isn't valid JSON, print it as-is."""
        requests_mock.post(MCP_URL, json=_jsonrpc_ok(_text_content("plain text payload")))

        result = runner.invoke(main, ["mcp", "status"])
        assert result.exit_code == 0
        assert "plain text payload" in result.output

    def test_status_no_server(
        self, runner: CliRunner, requests_mock: requests_mock_pkg.Mocker
    ) -> None:
        requests_mock.post(MCP_URL, exc=requests.ConnectionError)
        result = runner.invoke(main, ["mcp", "status"])
        assert result.exit_code == 1
        assert "no running MCP server" in result.output

    def test_modules_pretty_prints_json(
        self, runner: CliRunner, requests_mock: requests_mock_pkg.Mocker
    ) -> None:
        payload = json.dumps({"my-mod": ["skill_a", "skill_b"]})
        requests_mock.post(MCP_URL, json=_jsonrpc_ok(_text_content(payload)))

        result = runner.invoke(main, ["mcp", "modules"])
        assert result.exit_code == 0
        assert json.loads(result.output) == {"my-mod": ["skill_a", "skill_b"]}

    def test_modules_no_server(
        self, runner: CliRunner, requests_mock: requests_mock_pkg.Mocker
    ) -> None:
        requests_mock.post(MCP_URL, exc=requests.ConnectionError)
        result = runner.invoke(main, ["mcp", "modules"])
        assert result.exit_code == 1
        assert "no running MCP server" in result.output


# ---------------------------------------------------------------------------
# `dimos agent-send`
# ---------------------------------------------------------------------------


class TestAgentSend:
    def test_agent_send_forwards_message(
        self, runner: CliRunner, requests_mock: requests_mock_pkg.Mocker
    ) -> None:
        """The CLI argument should be forwarded as the ``message`` tool arg."""
        captured: list[dict] = []

        def _handler(request, context):
            captured.append(request.json())
            return _jsonrpc_ok(_text_content("queued"))

        requests_mock.post(MCP_URL, json=_handler)

        result = runner.invoke(main, ["agent-send", "say hello"])
        assert result.exit_code == 0
        assert "queued" in result.output
        sent = captured[0]["params"]
        assert sent["name"] == "agent_send"
        assert sent["arguments"] == {"message": "say hello"}

    def test_agent_send_no_server(
        self, runner: CliRunner, requests_mock: requests_mock_pkg.Mocker
    ) -> None:
        requests_mock.post(MCP_URL, exc=requests.ConnectionError)
        result = runner.invoke(main, ["agent-send", "anything"])
        assert result.exit_code == 1
        assert "no running MCP server" in result.output

    def test_agent_send_jsonrpc_error(
        self, runner: CliRunner, requests_mock: requests_mock_pkg.Mocker
    ) -> None:
        requests_mock.post(MCP_URL, json=_jsonrpc_err("agent crashed"))
        result = runner.invoke(main, ["agent-send", "ping"])
        assert result.exit_code == 1
        assert "agent crashed" in result.output


# ---------------------------------------------------------------------------
# `dimos restart` — only the no-instance error path is safe to test
# (the success path would re-exec the test process via os.execvp)
# ---------------------------------------------------------------------------


class TestRestartErrorPaths:
    def test_restart_no_running_instance(self, runner: CliRunner) -> None:
        """With an empty registry, restart must fail with exit 1."""
        result = runner.invoke(main, ["restart"])
        assert result.exit_code == 1
        assert "No running DimOS instance" in result.output
