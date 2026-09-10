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

from pathlib import Path

import pytest

from dimos.robot.all_blueprints import all_blueprints
from dimos.robot.unitree.holo_workspace import DemoCommand, HoloWorkspace


def test_agentic_blueprints_are_registered() -> None:
    for name in (
        "unitree-go2-agentic",
        "unitree-g1-agentic-sim",
        "unitree-g1-agentic",
    ):
        assert name in all_blueprints


def test_dimos_demo_commands_cover_go2_and_g1() -> None:
    demos = {demo.name: demo for demo in HoloWorkspace.dimos_demo_commands()}
    assert set(demos) == {
        "go2-agentic-replay",
        "go2-agentic-hardware",
        "g1-agentic-sim",
        "g1-agentic-hardware",
    }
    assert demos["go2-agentic-replay"].command == "dimos --replay run unitree-go2-agentic"
    assert demos["g1-agentic-sim"].command == "dimos --simulation run unitree-g1-agentic-sim"
    assert "--robot-ip 192.168.123.161" in demos["go2-agentic-hardware"].command
    assert "--robot-ip 192.168.123.161" in demos["g1-agentic-hardware"].command
    assert demos["go2-agentic-hardware"].requires_hardware
    assert not demos["go2-agentic-replay"].requires_hardware


def test_fork_urls_point_at_team_clones() -> None:
    assert HoloWorkspace.HOLOAGENT.url == "https://github.com/yixinzhangagent/HoloAgent"
    assert HoloWorkspace.HOLOMOTION.url == "https://github.com/yixinzhangagent/HoloMotion"
    dest = Path("/tmp/holo-dest")
    commands = HoloWorkspace.clone_commands(dest)
    assert commands == (
        "git clone https://github.com/yixinzhangagent/HoloAgent.git /tmp/holo-dest/HoloAgent",
        "git clone https://github.com/yixinzhangagent/HoloMotion.git /tmp/holo-dest/HoloMotion",
    )


def test_status_missing_and_present(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    dest = tmp_path / "workspace"
    dest.mkdir()
    missing = HoloWorkspace.status(dest)
    assert [row.present for row in missing] == [False, False]
    assert all(row.detail == "not cloned" for row in missing)

    agent = dest / "HoloAgent"
    agent.mkdir()
    (agent / "README.md").write_text("# HoloAgent\n")
    present, still_missing = HoloWorkspace.status(dest)
    assert present.present
    assert present.detail == "README.md found"
    assert not still_missing.present

    monkeypatch.setenv("HOLOMOTION_ROOT", str(tmp_path / "custom-motion"))
    custom = tmp_path / "custom-motion"
    custom.mkdir()
    (custom / "README.md").write_text("# HoloMotion\n")
    rows = {row.repo.name: row for row in HoloWorkspace.status(dest)}
    assert rows["HoloMotion"].path == custom.resolve()
    assert rows["HoloMotion"].present


def test_cli_help_and_list_demos(capsys: pytest.CaptureFixture[str]) -> None:
    help_text = HoloWorkspace.build_parser().format_help()
    assert "list-demos" in help_text
    assert "Does not start robots" in help_text

    assert HoloWorkspace.run(["list-demos"]) == 0
    out = capsys.readouterr().out
    assert "dimos --replay run unitree-go2-agentic" in out
    assert "dimos --simulation run unitree-g1-agentic-sim" in out
    assert "dimos list" in out


def test_cli_status_and_clone(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    dest = tmp_path / "ext"
    dest.mkdir()
    assert HoloWorkspace.run(["--dest", str(dest), "status"]) == 0
    status_out = capsys.readouterr().out
    assert "HoloAgent: missing" in status_out
    assert "HoloMotion: missing" in status_out

    assert HoloWorkspace.run(["--dest", str(dest), "print-clone"]) == 0
    clone_out = capsys.readouterr().out
    assert "git clone https://github.com/yixinzhangagent/HoloAgent.git" in clone_out
    assert "holomotion check" in clone_out

    assert HoloWorkspace.run(["--dest", str(dest), "next-steps"]) == 0
    next_out = capsys.readouterr().out
    assert "docs/usage/holo_integration.md" in next_out
    assert "list-demos" in next_out


def test_default_dest_is_not_filesystem_root(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("HOLO_WORKSPACE", raising=False)
    dest = HoloWorkspace.default_dest()
    assert dest != Path("/")
    assert dest.is_absolute()


def test_demo_command_type() -> None:
    demo = DemoCommand(name="x", command="dimos list", notes="dry")
    assert demo.command == "dimos list"
    assert not demo.requires_hardware
