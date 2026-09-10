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

"""Optional Horizon workspace helpers next to DimOS Go2/G1 demos.

DimOS already ships Unitree agentic and nav blueprints. The seven team forks
stay external — clone them beside this repo when you need that stack. This
module does not vendor those trees and does not start robots.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import os
from pathlib import Path
import sys

from dimos.constants import DIMOS_PROJECT_ROOT

# NAV / MANIP / PERCEPTION / SIM (+ AGENT for the Embodied AgentOS).
LAYER_NAV = "NAV"
LAYER_MANIP = "MANIP"
LAYER_PERCEPTION = "PERCEPTION"
LAYER_SIM = "SIM"
LAYER_AGENT = "AGENT"


@dataclass(frozen=True)
class ExternalRepo:
    """A git repo that must stay outside this DimOS tree."""

    name: str
    url: str
    default_branch: str
    dirname: str
    env_root: str
    layer: str
    note: str
    note_zh: str
    first_pointer: str


@dataclass(frozen=True)
class DemoCommand:
    """A DimOS command a new user can copy-paste."""

    name: str
    command: str
    notes: str
    requires_hardware: bool = False
    kind: str = "agent"


@dataclass(frozen=True)
class RepoStatus:
    """Whether an optional external clone is present on disk."""

    repo: ExternalRepo
    path: Path
    present: bool
    detail: str


class HoloWorkspace:
    """Static helpers for DimOS nav/agent demos and optional Horizon clones."""

    HOLOAGENT = ExternalRepo(
        name="HoloAgent",
        url="https://github.com/yixinzhangagent/HoloAgent",
        default_branch="main",
        dirname="HoloAgent",
        env_root="HOLOAGENT_ROOT",
        layer=LAYER_AGENT,
        note=(
            "Embodied AgentOS + FSR-VLN nav + skills. Optional ROS 2 path "
            "beside unitree-go2-agentic / unitree-g1-agentic(-sim)."
        ),
        note_zh="AgentOS + FSR-VLN 导航与技能; 可选外部导航/智能体路径.",
        first_pointer=(
            "docs/user_guide/Intruduction.md "
            "(upstream filename spelling) and docs/user_guide/README_agent.md"
        ),
    )
    HOLOMOTION = ExternalRepo(
        name="HoloMotion",
        url="https://github.com/yixinzhangagent/HoloMotion",
        default_branch="master",
        dirname="HoloMotion",
        env_root="HOLOMOTION_ROOT",
        layer=LAYER_MANIP,
        note=(
            "G1 whole-body motion (Orin Docker). Optional beside "
            "unitree-g1-agentic(-sim); not invoked by dimos run."
        ),
        note_zh="G1 全身运动策略; 可选, 不由 dimos run 启动.",
        first_pointer="docs/realworld_deployment.md then holomotion check",
    )
    GEOFLOWSLAM = ExternalRepo(
        name="GeoFlowSlam",
        url="https://github.com/zhangyinxina-ui/GeoFlowSlam",
        default_branch="master",
        dirname="GeoFlowSlam",
        env_root="GEOFLOWSLAM_ROOT",
        layer=LAYER_NAV,
        note=(
            "Primary NAV add: IROS 2025 tightly-coupled RGBD-inertial + "
            "legged-odometry SLAM. Optional beside unitree-go2 and examples/nav-go2."
        ),
        note_zh="主 NAV 增量: 腿式 RGBD-惯性 + 里程计融合 SLAM.",
        first_pointer="README.md, ./build.sh, then script/run_orbslam/run_rgbd_vi_g1.py",
    )
    BIP3D = ExternalRepo(
        name="BIP3D",
        url="https://github.com/zhangyinxina-ui/BIP3D",
        default_branch="main",
        dirname="BIP3D",
        env_root="BIP3D_ROOT",
        layer=LAYER_PERCEPTION,
        note=(
            "2D<->3D embodied detection/grounding. Standalone snapshot; "
            "active work moved to RoboOrchardLab projects/bip3d_grounding."
        ),
        note_zh="2D<->3D 具身感知; 后续代码已迁入 RoboOrchardLab.",
        first_pointer="docs/quick_start.md",
    )
    ROBOORCHARDLAB = ExternalRepo(
        name="RoboOrchardLab",
        url="https://github.com/zhangyinxina-ui/RoboOrchardLab",
        default_branch="master",
        dirname="RoboOrchardLab",
        env_root="ROBOORCHARDLAB_ROOT",
        layer=LAYER_PERCEPTION,
        note=(
            "Embodied training lab (not a dimos run blueprint). "
            "BIP3D lives under projects/bip3d_grounding."
        ),
        note_zh="具身训练实验室; BIP3D 在 projects/bip3d_grounding.",
        first_pointer="README.md and projects/bip3d_grounding/",
    )
    EMBODIEDGEN = ExternalRepo(
        name="EmbodiedGen",
        url="https://github.com/zhangyinxina-ui/EmbodiedGen",
        default_branch="master",
        dirname="EmbodiedGen",
        env_root="EMBODIEDGEN_ROOT",
        layer=LAYER_SIM,
        note=(
            "Sim-ready 3D world generation. Optional data/worlds path; "
            "does not replace dimos --simulation MuJoCo blueprints."
        ),
        note_zh="仿真就绪 3D 世界生成; 不替代 DimOS MuJoCo.",
        first_pointer="README.md and ./install.sh",
    )
    ROBOTRANSFER = ExternalRepo(
        name="RoboTransfer",
        url="https://github.com/zhangyinxina-ui/RoboTransfer",
        default_branch="main",
        dirname="RoboTransfer",
        env_root="ROBOTRANSFER_ROOT",
        layer=LAYER_SIM,
        note=(
            "Geometry-consistent video diffusion for visual policy-transfer "
            "data synth. Training/data only — not a robot runtime."
        ),
        note_zh="视觉策略迁移的几何一致视频数据合成.",
        first_pointer="README.md then uv run main.py",
    )
    REPOS: tuple[ExternalRepo, ...] = (
        HOLOAGENT,
        HOLOMOTION,
        GEOFLOWSLAM,
        BIP3D,
        ROBOORCHARDLAB,
        EMBODIEDGEN,
        ROBOTRANSFER,
    )
    # Not integrated. SocialRobot is legacy-only.
    OUT_OF_SCOPE: tuple[str, ...] = (
        "Sparse4D",
        "GUMP",
        "CARLA",
        "nuplan",
        "leaderboard",
        "OE-Skills",
        "x2 bootprint",
        "SocialRobot",
    )

    PLACEHOLDER_ROBOT_IP = "192.168.123.161"

    @staticmethod
    def default_dest() -> Path:
        """Sibling of this checkout, or the repo root if the parent is ``/``.

        Override with ``HOLO_WORKSPACE``. Cloud / container checkouts often live
        at ``/workspace``; cloning into ``/`` is never useful.
        """
        override = os.environ.get("HOLO_WORKSPACE")
        if override:
            return Path(override).expanduser().resolve()
        parent = DIMOS_PROJECT_ROOT.parent.resolve()
        if parent in {Path("/"), Path.home()}:
            return DIMOS_PROJECT_ROOT.resolve()
        return parent

    @staticmethod
    def dimos_nav_demo_commands() -> tuple[DemoCommand, ...]:
        """In-tree Go2 nav paths (no Horizon clone required)."""
        return (
            DemoCommand(
                name="go2-nav-replay",
                command="dimos --replay run unitree-go2",
                notes=(
                    "In-tree Go2 SLAM + costmap + A*. No LLM. First replay may download LFS data."
                ),
                kind="nav",
            ),
            DemoCommand(
                name="nav-go2-nomad-replay",
                command=("uv run python examples/nav-go2/go2_nomad_nav.py --replay --viewer rerun"),
                notes=(
                    "NoMaD vision-nav example (script entry, not `dimos run`). "
                    "See examples/nav-go2/README.md."
                ),
                kind="nav",
            ),
        )

    @staticmethod
    def dimos_demo_commands() -> tuple[DemoCommand, ...]:
        """Agentic Go2/G1 commands (primary runnable path)."""
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
                notes="G1 MuJoCo sim + GPT-4o (G1 prompt) + skills. Needs OPENAI_API_KEY.",
            ),
            DemoCommand(
                name="g1-agentic-hardware",
                command=f"dimos run unitree-g1-agentic --robot-ip {ip}",
                notes="Real G1. Replace the IP. Needs OPENAI_API_KEY.",
                requires_hardware=True,
            ),
        )

    @staticmethod
    def all_demo_commands() -> tuple[DemoCommand, ...]:
        return (
            *HoloWorkspace.dimos_nav_demo_commands(),
            *HoloWorkspace.dimos_demo_commands(),
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
    def clone_commands(dest: Path) -> tuple[str, ...]:
        commands: list[str] = []
        for repo in HoloWorkspace.REPOS:
            path = HoloWorkspace.resolve_repo_path(repo, dest)
            commands.append(f"git clone {repo.url}.git {path}")
        return tuple(commands)

    @staticmethod
    def clone_urls() -> tuple[str, ...]:
        return tuple(f"{repo.url}.git" for repo in HoloWorkspace.REPOS)

    @staticmethod
    def format_list_demos() -> str:
        lines = [
            "DimOS nav + agent demos (reuse these; do not invent a parallel stack):",
            "",
            "# --- nav (in-tree; no Horizon clone) ---",
            "",
        ]
        for demo in HoloWorkspace.dimos_nav_demo_commands():
            lines.append(f"# {demo.name}")
            lines.append(demo.command)
            lines.append(f"# {demo.notes}")
            lines.append("")
        lines.extend(["# --- agent ---", ""])
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
                "Optional external NAV (clone, not dimos run): GeoFlowSlam, HoloAgent FSR-VLN.",
                "List every blueprint: dimos list",
                "Agent-oriented notes: AGENTS.md",
                "Taxonomy: uv run python scripts/holo_bridge.py taxonomy",
            ]
        )
        return "\n".join(lines)

    @staticmethod
    def format_taxonomy() -> str:
        lines = [
            "Horizon forks stay external. Layers: NAV / MANIP / PERCEPTION / SIM / AGENT.",
            "DimOS blueprints remain the runnable Go2/G1 path.",
            "",
        ]
        for repo in HoloWorkspace.REPOS:
            lines.append(f"{repo.layer}\t{repo.name}")
            lines.append(f"  url: {repo.url}")
            lines.append(f"  {repo.note}")
            lines.append(f"  中文: {repo.note_zh}")
            lines.append(f"  first: {repo.first_pointer}")
            lines.append("")
        lines.append(
            "Out of scope (do not integrate): " + ", ".join(HoloWorkspace.OUT_OF_SCOPE) + "."
        )
        lines.append("SocialRobot is legacy-only — one-line note, no clone helper.")
        return "\n".join(lines)

    @staticmethod
    def format_clone(dest: Path) -> str:
        lines = [
            f"# Optional clones into {dest}",
            "# These are Horizon forks, not DimOS blueprints. Do not vendor them.",
            "",
            *HoloWorkspace.clone_commands(dest),
            "",
            "# Then follow the fork docs, not `dimos run`.",
        ]
        for repo in HoloWorkspace.REPOS:
            lines.append(f"# {repo.name} [{repo.layer}]: {repo.first_pointer}")
        return "\n".join(lines)

    @staticmethod
    def format_status(dest: Path) -> str:
        lines = [f"Holo workspace dest: {dest}", ""]
        for row in HoloWorkspace.status(dest):
            mark = "present" if row.present else "missing"
            lines.append(f"{row.repo.name} [{row.repo.layer}]: {mark}")
            lines.append(f"  url: {row.repo.url}")
            lines.append(f"  path: {row.path}")
            lines.append(f"  detail: {row.detail}")
        return "\n".join(lines)

    @staticmethod
    def format_next_steps(dest: Path) -> str:
        rows = HoloWorkspace.status(dest)
        missing = [row for row in rows if not row.present]
        lines = [
            "1. Run a DimOS nav or agent demo from this repo (see list-demos).",
            "2. Horizon forks are optional external clones — do not vendor them.",
            "   Primary extra NAV: GeoFlowSlam (RGBD-inertial SLAM).",
            "   Optional agent/nav: HoloAgent (FSR-VLN). Optional G1 MANIP: HoloMotion.",
        ]
        if missing:
            lines.append("3. Clone any missing forks (all seven URLs):")
            lines.extend(f"   {cmd}" for cmd in HoloWorkspace.clone_commands(dest))
        else:
            lines.append("3. All seven forks are present. Next:")
        lines.extend(
            [
                "4. Keep Horizon ROS 2 / Docker / C++ envs out of the DimOS .venv.",
                (
                    "5. GeoFlowSlam: README.md -> ./build.sh -> "
                    "script/run_orbslam/run_rgbd_vi_g1.py (not dimos run)."
                ),
                "6. HoloAgent (ROS 2 Humble): bash scripts/build.sh, then",
                (
                    "   python3 agentic_robot/agentOS/sandbox_test/"
                    "test_single_robot_long_instruction.py"
                ),
                "7. HoloMotion (G1 Orin Docker): holomotion check  # no robot action",
                "8. Full notes: docs/usage/holo_integration.md",
            ]
        )
        return "\n".join(lines)

    @staticmethod
    def build_parser() -> argparse.ArgumentParser:
        parser = argparse.ArgumentParser(
            prog="holo_bridge",
            description=(
                "Print DimOS Go2/G1 nav/agent demo commands and optional "
                "Horizon fork clone helpers (seven repos). Does not start robots."
            ),
        )
        parser.add_argument(
            "--dest",
            type=Path,
            default=None,
            help=(
                "Directory that should contain sibling Horizon clones "
                f"(default: {HoloWorkspace.default_dest()})"
            ),
        )
        parser.add_argument(
            "command",
            nargs="?",
            choices=("list-demos", "print-clone", "status", "next-steps", "taxonomy"),
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
        elif command == "taxonomy":
            text = HoloWorkspace.format_taxonomy()
        else:
            text = HoloWorkspace.format_next_steps(dest)
        sys.stdout.write(text + "\n")
        return 0


def main(argv: list[str] | None = None) -> int:
    return HoloWorkspace.run(argv)


if __name__ == "__main__":
    raise SystemExit(main())
