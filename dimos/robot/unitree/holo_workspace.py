#!/usr/bin/env python3
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

"""Optional Horizon HoloAgent / HoloMotion workspace helpers.

DimOS already ships Unitree Go2/G1 agentic blueprints. The Horizon forks stay
external — clone them beside this repo when you need that stack. This module
does not vendor those trees and does not start robots.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import os
from pathlib import Path
import shlex
import sys

from dimos.constants import DIMOS_PROJECT_ROOT


@dataclass(frozen=True)
class ExternalRepo:
    """A git repo that must stay outside this DimOS tree."""

    name: str
    url: str
    default_branch: str
    dirname: str
    env_root: str


@dataclass(frozen=True)
class DemoCommand:
    """A DimOS command a new user can copy-paste."""

    name: str
    command: str
    notes: str
    requires_hardware: bool = False


@dataclass(frozen=True)
class RepoStatus:
    """Whether an optional external clone is present on disk."""

    repo: ExternalRepo
    path: Path
    present: bool
    detail: str


class HoloWorkspace:
    """Static helpers for DimOS agent demos and optional Holo clones."""

    HOLOAGENT = ExternalRepo(
        name="HoloAgent",
        url="https://github.com/yixinzhangagent/HoloAgent",
        default_branch="main",
        dirname="HoloAgent",
        env_root="HOLOAGENT_ROOT",
    )
    HOLOMOTION = ExternalRepo(
        name="HoloMotion",
        url="https://github.com/yixinzhangagent/HoloMotion",
        default_branch="master",
        dirname="HoloMotion",
        env_root="HOLOMOTION_ROOT",
    )
    REPOS: tuple[ExternalRepo, ...] = (HOLOAGENT, HOLOMOTION)

    PLACEHOLDER_ROBOT_IP = "192.168.123.161"

    @staticmethod
    def default_dest() -> Path:
        """Parent of this checkout (sibling clone dest), unless that parent is ``/``.

        Override with ``HOLO_WORKSPACE``. Cloud / container checkouts often live
        at ``/workspace``; cloning into ``/`` is never useful. A checkout
        directly under ``$HOME`` still uses home as the sibling dest
        (``~/HoloAgent``, ``~/HoloMotion``).
        """
        override = os.environ.get("HOLO_WORKSPACE")
        if override:
            return Path(override).expanduser().resolve()
        parent = DIMOS_PROJECT_ROOT.parent.resolve()
        if parent == Path("/"):
            return DIMOS_PROJECT_ROOT.resolve()
        return parent

    @staticmethod
    def dimos_demo_commands() -> tuple[DemoCommand, ...]:
        ip = HoloWorkspace.PLACEHOLDER_ROBOT_IP
        return (
            DemoCommand(
                name="go2-agentic-replay",
                command="dimos --replay run unitree-go2-agentic",
                notes=(
                    "Go2 LLM agent + skills + MCP on recorded data. "
                    "Needs OPENAI_API_KEY. First replay may download LFS data."
                ),
            ),
            DemoCommand(
                name="go2-agentic-hardware",
                command=f"dimos run unitree-go2-agentic --robot-ip {ip}",
                notes="Real Go2. Replace the IP. Needs OPENAI_API_KEY.",
                requires_hardware=True,
            ),
            DemoCommand(
                name="g1-agentic-sim",
                command="dimos --simulation run unitree-g1-agentic-sim",
                notes=("G1 MuJoCo sim + gpt-5.6-luna (G1 prompt) + skills. Needs OPENAI_API_KEY."),
            ),
            DemoCommand(
                name="g1-agentic-hardware",
                command=f"dimos run unitree-g1-agentic --robot-ip {ip}",
                notes="Real G1. Replace the IP. Needs OPENAI_API_KEY.",
                requires_hardware=True,
            ),
        )

    @staticmethod
    def resolve_repo_path(repo: ExternalRepo, dest: Path) -> Path:
        env_value = os.environ.get(repo.env_root)
        if env_value:
            return Path(env_value).expanduser().resolve()
        return (dest / repo.dirname).resolve()

    @staticmethod
    def is_present(path: Path) -> bool:
        return path.is_dir() and (path / "README.md").is_file()

    @staticmethod
    def status(dest: Path) -> tuple[RepoStatus, ...]:
        rows: list[RepoStatus] = []
        for repo in HoloWorkspace.REPOS:
            path = HoloWorkspace.resolve_repo_path(repo, dest)
            if HoloWorkspace.is_present(path):
                rows.append(
                    RepoStatus(
                        repo=repo,
                        path=path,
                        present=True,
                        detail="README.md found",
                    )
                )
            elif path.exists():
                rows.append(
                    RepoStatus(
                        repo=repo,
                        path=path,
                        present=False,
                        detail="path exists but README.md is missing",
                    )
                )
            else:
                rows.append(
                    RepoStatus(
                        repo=repo,
                        path=path,
                        present=False,
                        detail="not cloned",
                    )
                )
        return tuple(rows)

    @staticmethod
    def clone_commands(
        dest: Path, repos: tuple[ExternalRepo, ...] | None = None
    ) -> tuple[str, ...]:
        selected = HoloWorkspace.REPOS if repos is None else repos
        commands: list[str] = []
        for repo in selected:
            path = HoloWorkspace.resolve_repo_path(repo, dest)
            commands.append(f"git clone {shlex.quote(f'{repo.url}.git')} {shlex.quote(str(path))}")
        return tuple(commands)

    @staticmethod
    def format_list_demos() -> str:
        lines = [
            "DimOS agent demos (reuse these; do not invent a parallel stack):",
            "",
        ]
        for demo in HoloWorkspace.dimos_demo_commands():
            hw = " [hardware]" if demo.requires_hardware else ""
            lines.append(f"# {demo.name}{hw}")
            lines.append(demo.command)
            lines.append(f"# {demo.notes}")
            lines.append("")
        lines.extend(
            [
                "After an agentic blueprint is running:",
                "dimos status",
                'dimos agent-send "say hello"',
                "dimos mcp list-tools",
                "dimos stop",
                "",
                "List every blueprint: dimos list",
                "Agent-oriented notes: AGENTS.md",
            ]
        )
        return "\n".join(lines)

    @staticmethod
    def format_clone(dest: Path) -> str:
        lines = [
            f"# Optional clones into {dest}",
            "# These are Horizon forks, not DimOS blueprints.",
            "",
            *HoloWorkspace.clone_commands(dest),
            "",
            "# Then follow the fork docs (ROS 2 / Docker), not `dimos run`.",
            "# HoloAgent: docs/user_guide/Intruduction.md and docs/user_guide/README_agent.md",
            "# HoloMotion: docs/realworld_deployment.md  (docker + `holomotion check`)",
        ]
        return "\n".join(lines)

    @staticmethod
    def format_status(dest: Path) -> str:
        lines = [f"Holo workspace dest: {dest}", ""]
        for row in HoloWorkspace.status(dest):
            mark = "present" if row.present else "missing"
            lines.append(f"{row.repo.name}: {mark}")
            lines.append(f"  url: {row.repo.url}")
            lines.append(f"  path: {row.path}")
            lines.append(f"  detail: {row.detail}")
        return "\n".join(lines)

    @staticmethod
    def format_next_steps(dest: Path) -> str:
        rows = HoloWorkspace.status(dest)
        missing = [row for row in rows if not row.present]
        lines = [
            "1. Run a DimOS agent demo from this repo (see list-demos).",
            "2. HoloAgent / HoloMotion are optional external clones.",
        ]
        if missing:
            lines.append("3. Clone the missing forks:")
            lines.extend(
                f"   {cmd}"
                for cmd in HoloWorkspace.clone_commands(
                    dest, repos=tuple(row.repo for row in missing)
                )
            )
        else:
            lines.append("3. Both forks are present. Next:")
        lines.extend(
            [
                "4. HoloAgent (ROS 2 Humble): bash scripts/build.sh, then",
                "   python3 agentic_robot/agentOS/sandbox_test/test_single_robot_long_instruction.py",
                "5. HoloMotion (G1 Orin Docker): holomotion check  # no robot action",
                "   then holomotion offline / holomotion teleop after the check passes.",
                "6. Full notes: docs/usage/holo_integration.md",
            ]
        )
        return "\n".join(lines)

    @staticmethod
    def build_parser() -> argparse.ArgumentParser:
        parser = argparse.ArgumentParser(
            prog="holo_bridge",
            description=(
                "Print DimOS Go2/G1 agent demo commands and optional "
                "HoloAgent/HoloMotion clone helpers. Does not start robots."
            ),
        )
        parser.add_argument(
            "--dest",
            type=Path,
            default=None,
            help=(
                "Directory that should contain HoloAgent/ and HoloMotion/ "
                f"(default: {HoloWorkspace.default_dest()})"
            ),
        )
        parser.add_argument(
            "command",
            nargs="?",
            choices=("list-demos", "print-clone", "status", "next-steps"),
            default="list-demos",
            help="What to print (default: list-demos)",
        )
        return parser

    @staticmethod
    def run(argv: list[str] | None = None) -> int:
        args = HoloWorkspace.build_parser().parse_args(argv)
        dest = (args.dest or HoloWorkspace.default_dest()).expanduser().resolve()
        command = str(args.command)
        if command == "list-demos":
            text = HoloWorkspace.format_list_demos()
        elif command == "print-clone":
            text = HoloWorkspace.format_clone(dest)
        elif command == "status":
            text = HoloWorkspace.format_status(dest)
        else:
            text = HoloWorkspace.format_next_steps(dest)
        sys.stdout.write(text + "\n")
        return 0


def main(argv: list[str] | None = None) -> int:
    return HoloWorkspace.run(argv)


if __name__ == "__main__":
    raise SystemExit(main())
