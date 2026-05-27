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

from dataclasses import dataclass
import json
import os
from pathlib import Path


@dataclass(frozen=True)
class PRContext:
    """PR 元数据，供需求置信度与风险分析使用。"""

    title: str
    body: str
    changed_files: tuple[str, ...]
    base_ref: str = "main"

    @property
    def has_acceptance_criteria(self) -> bool:
        body_lower = self.body.lower()
        markers = (
            "acceptance",
            "验收",
            "criteria",
            "expected behavior",
            "test plan",
            "测试",
        )
        return any(m in body_lower for m in markers)

    @property
    def has_non_goals(self) -> bool:
        body_lower = self.body.lower()
        return "non-goal" in body_lower or "非目标" in body_lower or "out of scope" in body_lower


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def load_pr_context(
    *,
    context_file: Path | None = None,
    base_ref: str | None = None,
) -> PRContext:
    """从环境变量、JSON 文件或 git diff 加载 PR 上下文。"""
    root = _repo_root()
    ctx_path = context_file or root / "test" / ".quality_gate" / "pr_context.json"

    if ctx_path.is_file():
        data = json.loads(ctx_path.read_text(encoding="utf-8"))
        return PRContext(
            title=str(data.get("title", "")),
            body=str(data.get("body", "")),
            changed_files=tuple(str(f) for f in data.get("changed_files", [])),
            base_ref=str(data.get("base_ref", base_ref or "main")),
        )

    title = os.environ.get("QUALITY_GATE_PR_TITLE", "")
    body = os.environ.get("QUALITY_GATE_PR_BODY", "")
    ref = base_ref or os.environ.get("QUALITY_GATE_BASE_REF", "main")
    changed = _git_changed_files(root, ref)
    return PRContext(title=title, body=body, changed_files=changed, base_ref=ref)


def _git_changed_files(root: Path, base_ref: str) -> tuple[str, ...]:
    import subprocess

    for spec in (f"{base_ref}...HEAD", f"origin/{base_ref}...HEAD", "HEAD~1..HEAD"):
        try:
            out = subprocess.run(
                ["git", "diff", "--name-only", spec],
                cwd=root,
                capture_output=True,
                text=True,
                check=True,
                timeout=30,
            )
            files = tuple(line.strip() for line in out.stdout.splitlines() if line.strip())
            if files:
                return files
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError):
            continue
    return ()
