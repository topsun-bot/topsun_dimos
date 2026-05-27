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

import json
from pathlib import Path

from framework.models import GateStatus, QualityGateReport


def format_markdown(report: QualityGateReport) -> str:
    """生成与 Codex PR Quality Gate 文档一致的 PR 评论 Markdown。"""
    passed = report.status == GateStatus.PASSED
    icon = "✅" if passed else "❌"
    title = "Codex Quality Gate Passed" if passed else "Codex Quality Gate Failed"
    if report.status == GateStatus.BLOCKED:
        title = "Codex Quality Gate Blocked"

    lines = [
        f"{icon} **{title}**",
        "",
        f"**Requirement Confidence:** {report.requirement_confidence.value.title()}",
        f"**Risk Level:** {report.risk_level.value.title()}",
        f"**Review Depth:** {report.review_depth.value.title()}",
        "",
    ]

    if report.blocked_reason:
        lines += ["**Blocked:**", f"- {report.blocked_reason}", ""]

    if report.functional_notes:
        lines += ["**功能合理性：**"]
        lines += [f"- {n}" for n in report.functional_notes]
        lines.append("")

    if report.historical_behavior_notes:
        lines += ["**Historical Behavior:**"]
        lines += [f"- {n}" for n in report.historical_behavior_notes]
        lines.append("")

    if report.regression_risks:
        lines += ["**Regression Risk:**"]
        lines += [f"- {r}" for r in report.regression_risks]
        lines.append("")

    if report.new_tests:
        lines += ["**新增测试：**"]
        lines += [f"- `{t}`" for t in report.new_tests]
        lines.append("")

    if report.test_summaries:
        lines += ["**测试结果：**"]
        for s in report.test_summaries:
            status = "Passed" if s.ok else "Failed"
            lines.append(
                f"- `{s.layer.value}`: {status} "
                f"(passed={s.passed}, failed={s.failed}, skipped={s.skipped}, "
                f"{s.duration_seconds:.1f}s)"
            )
            for nodeid in s.failed_nodeids[:5]:
                lines.append(f"  - failed: `{nodeid}`")
        lines.append("")

    if report.failure_reasons:
        lines += ["**失败原因：**"]
        lines += [f"- {r}" for r in report.failure_reasons]
        lines.append("")

    if report.evidences:
        lines += ["**Evidence:**"]
        for ev in report.evidences:
            lines.append(f"- `{ev.file_path}` @ {ev.location}: {ev.risk_reason}")
            lines.append(f"  - trigger: {ev.trigger}")
            if ev.test_basis:
                lines.append(f"  - test: {ev.test_basis}")
        lines.append("")

    if report.recommendations:
        lines += ["**建议：**"]
        lines += [f"- {r}" for r in report.recommendations]
        lines.append("")

    if not passed and report.status != GateStatus.BLOCKED:
        lines += [
            "**结论：**",
            "- 已仅在 `test/` 目录补充/执行验证用例",
            "- 未修改业务代码（`dimos/`）",
            "",
        ]

    return "\n".join(lines).rstrip() + "\n"


def write_report(report: QualityGateReport, output_dir: Path) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "quality_gate_report.json"
    md_path = output_dir / "quality_gate_report.md"
    json_path.write_text(
        json.dumps(report.to_dict(), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    md_path.write_text(format_markdown(report), encoding="utf-8")
    return json_path, md_path
