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
from dimos.robot.unitree.holo_workspace import (
    LAYER_AGENT,
    LAYER_MANIP,
    LAYER_NAV,
    LAYER_PERCEPTION,
    LAYER_SIM,
    DemoCommand,
    HoloWorkspace,
)

EXPECTED_FORK_URLS = {
    "HoloAgent": "https://github.com/yixinzhangagent/HoloAgent",
    "HoloMotion": "https://github.com/yixinzhangagent/HoloMotion",
    "GeoFlowSlam": "https://github.com/zhangyinxina-ui/GeoFlowSlam",
    "BIP3D": "https://github.com/zhangyinxina-ui/BIP3D",
    "RoboOrchardLab": "https://github.com/zhangyinxina-ui/RoboOrchardLab",
    "EmbodiedGen": "https://github.com/zhangyinxina-ui/EmbodiedGen",
    "RoboTransfer": "https://github.com/zhangyinxina-ui/RoboTransfer",
}


def test_agentic_and_nav_blueprints_are_registered() -> None:
    for name in (
        "unitree-go2",
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


def test_dimos_nav_demo_commands() -> None:
    demos = {demo.name: demo for demo in HoloWorkspace.dimos_nav_demo_commands()}
    assert set(demos) == {"go2-nav-replay", "nav-go2-nomad-replay"}
    assert demos["go2-nav-replay"].command == "dimos --replay run unitree-go2"
    assert "examples/nav-go2/go2_nomad_nav.py" in demos["nav-go2-nomad-replay"].command
    assert all(demo.kind == "nav" for demo in demos.values())


def test_seven_fork_urls_and_layers() -> None:
    assert [repo.name for repo in HoloWorkspace.REPOS] == list(EXPECTED_FORK_URLS)
    by_name = {repo.name: repo for repo in HoloWorkspace.REPOS}
    for name, url in EXPECTED_FORK_URLS.items():
        assert by_name[name].url == url
    assert by_name["HoloAgent"].layer == LAYER_AGENT
    assert by_name["HoloMotion"].layer == LAYER_MANIP
    assert by_name["GeoFlowSlam"].layer == LAYER_NAV
    assert by_name["BIP3D"].layer == LAYER_PERCEPTION
    assert by_name["RoboOrchardLab"].layer == LAYER_PERCEPTION
    assert by_name["EmbodiedGen"].layer == LAYER_SIM
    assert by_name["RoboTransfer"].layer == LAYER_SIM
    dest = Path("/tmp/holo-dest")
    commands = HoloWorkspace.clone_commands(dest)
    assert len(commands) == 7
    assert commands[0] == (
        "git clone https://github.com/yixinzhangagent/HoloAgent.git /tmp/holo-dest/HoloAgent"
    )
    assert any("GeoFlowSlam.git" in cmd for cmd in commands)
    assert HoloWorkspace.clone_urls() == tuple(f"{url}.git" for url in EXPECTED_FORK_URLS.values())


def test_out_of_scope_names_are_not_cloned() -> None:
    names = {repo.name for repo in HoloWorkspace.REPOS}
    for banned in HoloWorkspace.OUT_OF_SCOPE:
        assert banned not in names
    assert "SocialRobot" in HoloWorkspace.OUT_OF_SCOPE


def test_status_missing_and_present(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    dest = tmp_path / "workspace"
    dest.mkdir()
    missing = HoloWorkspace.status(dest)
    assert len(missing) == 7
    assert all(not row.present for row in missing)
    assert all(row.detail == "not cloned" for row in missing)

    agent = dest / "HoloAgent"
    agent.mkdir()
    (agent / "README.md").write_text("# HoloAgent\n")
    rows = {row.repo.name: row for row in HoloWorkspace.status(dest)}
    assert rows["HoloAgent"].present
    assert rows["HoloAgent"].detail == "README.md found"
    assert not rows["HoloMotion"].present
    assert not rows["GeoFlowSlam"].present

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
    assert "taxonomy" in help_text
    assert "Does not start robots" in help_text

    assert HoloWorkspace.run(["list-demos"]) == 0
    out = capsys.readouterr().out
    assert "dimos --replay run unitree-go2-agentic" in out
    assert "dimos --simulation run unitree-g1-agentic-sim" in out
    assert "dimos --replay run unitree-go2" in out
    assert "examples/nav-go2/go2_nomad_nav.py" in out
    assert "GeoFlowSlam" in out
    assert "dimos list" in out


def test_cli_status_clone_taxonomy_and_next(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    dest = tmp_path / "ext"
    dest.mkdir()
    assert HoloWorkspace.run(["--dest", str(dest), "status"]) == 0
    status_out = capsys.readouterr().out
    for name in EXPECTED_FORK_URLS:
        assert f"{name}" in status_out
        assert "missing" in status_out

    assert HoloWorkspace.run(["--dest", str(dest), "print-clone"]) == 0
    clone_out = capsys.readouterr().out
    for url in EXPECTED_FORK_URLS.values():
        assert f"git clone {url}.git" in clone_out
    assert "holomotion check" in clone_out
    assert "GeoFlowSlam" in clone_out

    assert HoloWorkspace.run(["taxonomy"]) == 0
    tax_out = capsys.readouterr().out
    assert "NAV" in tax_out and "GeoFlowSlam" in tax_out
    assert "Sparse4D" in tax_out
    assert "SocialRobot" in tax_out

    assert HoloWorkspace.run(["--dest", str(dest), "next-steps"]) == 0
    next_out = capsys.readouterr().out
    assert "docs/usage/holo_integration.md" in next_out
    assert "list-demos" in next_out
    assert "GeoFlowSlam" in next_out


def test_default_dest_is_not_filesystem_root(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("HOLO_WORKSPACE", raising=False)
    dest = HoloWorkspace.default_dest()
    assert dest != Path("/")
    assert dest.is_absolute()


def test_demo_command_type() -> None:
    demo = DemoCommand(name="x", command="dimos list", notes="dry")
    assert demo.command == "dimos list"
    assert not demo.requires_hardware
    assert demo.kind == "agent"
