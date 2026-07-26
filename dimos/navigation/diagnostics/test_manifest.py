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

import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from dimos.core.global_config import GlobalConfig
from dimos.navigation.diagnostics.manifest import (
    redact_argv,
    write_navigation_manifest,
)


def test_trace_off_writes_no_manifest_or_directory(tmp_path: Path) -> None:
    result = write_navigation_manifest(
        tmp_path,
        run_id="run-off",
        blueprint="unitree-go2",
        argv=["dimos", "run", "unitree-go2"],
        global_settings=GlobalConfig(navigation_trace_level="off"),
        resolved_blueprint_config={},
        repository=tmp_path,
    )

    assert result is None
    assert not (tmp_path / "navigation").exists()


def test_manifest_is_immutable_and_redacts_credentials(tmp_path: Path) -> None:
    config = GlobalConfig(
        navigation_trace_level="full",
        unitree_username="robot-owner",
        unitree_password="super-secret",
        unitree_aes_128_key="aes-secret",
    )
    argv = [
        "dimos",
        "--unitree-username",
        "robot-owner",
        "--unitree-password=super-secret",
        "--unitree-aes-128-key",
        "aes-secret",
        "run",
        "unitree-go2",
    ]

    path = write_navigation_manifest(
        tmp_path,
        run_id="run-full",
        blueprint="unitree-go2",
        argv=argv,
        global_settings=config,
        resolved_blueprint_config={"go2connection": {"map_file": None}},
        repository=tmp_path,
    )
    assert path is not None
    first = path.read_text(encoding="utf-8")

    second = write_navigation_manifest(
        tmp_path,
        run_id="different",
        blueprint="different",
        argv=["different"],
        global_settings=config,
        resolved_blueprint_config={},
        repository=tmp_path,
    )

    assert second == path
    assert path.read_text(encoding="utf-8") == first
    assert "super-secret" not in first
    assert "robot-owner" not in first
    assert "aes-secret" not in first
    payload = json.loads(first)
    assert payload["command"][2] == "<redacted>"
    assert payload["command"][3] == "--unitree-password=<redacted>"
    assert payload["global_config"]["unitree_username"] == "<redacted>"
    assert payload["notes"]["send_is_robot_execution_ack"] is False


def test_redact_argv_handles_split_and_equals_forms() -> None:
    assert redact_argv(["--token", "abc", "--api-key=def", "--safe", "visible"]) == [
        "--token",
        "<redacted>",
        "--api-key=<redacted>",
        "--safe",
        "visible",
    ]


def test_manifest_tolerates_non_json_blueprint_config_types(tmp_path: Path) -> None:
    class ConfigWithProtocolMeta(BaseModel):
        value: Any

    result = write_navigation_manifest(
        tmp_path,
        run_id="run-protocol",
        blueprint="unitree-go2",
        argv=["dimos"],
        global_settings=GlobalConfig(navigation_trace_level="full"),
        resolved_blueprint_config=ConfigWithProtocolMeta(value=BaseModel),
        repository=tmp_path,
    )

    assert result is not None
    payload = json.loads(result.read_text(encoding="utf-8"))
    assert "BaseModel" in payload["resolved_blueprint_config"]["value"]
