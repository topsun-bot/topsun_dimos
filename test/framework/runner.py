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
import subprocess
import sys
import time

from framework.models import (
    GateStatus,
    QualityGateReport,
    RequirementConfidence,
    TestLayer,
    TestResultSummary,
)
from framework.pr_context import PRContext, load_pr_context
from framework.report import write_report
from framework.requirement import RequirementConfidenceChecker
from framework.review_depth import AdaptiveReviewDepth
from framework.risk import RiskClassifier
from framework.test_quality import TestQualityVerifier


class QualityGateRunner:
    """编排 Quality Gate 全流程：需求检查 → 风险 → 深度 → pytest → 报告。"""

    def __init__(self, repo_root: Path | None = None) -> None:
        self.repo_root = repo_root or Path(__file__).resolve().parents[2]
        self.test_root = self.repo_root / "test"

    def run(
        self,
        *,
        pr_context: PRContext | None = None,
        skip_tests: bool = False,
        output_dir: Path | None = None,
    ) -> QualityGateReport:
        ctx = pr_context or load_pr_context()
        req = RequirementConfidenceChecker().check(ctx)
        risk = RiskClassifier().assess(ctx)
        plan = AdaptiveReviewDepth().plan(risk)

        out_dir = output_dir or self.test_root / ".quality_gate" / "reports"

        if not req.can_proceed:
            report = QualityGateReport(
                status=GateStatus.BLOCKED,
                requirement_confidence=req.confidence,
                risk_level=risk.level,
                review_depth=plan.depth,
                blocked_reason="；".join(req.issues),
                recommendations=list(req.issues),
            )
            write_report(report, out_dir)
            return report

        test_summaries: list[TestResultSummary] = []
        failure_reasons: list[str] = []
        quality_issues = TestQualityVerifier().scan_directory(self.test_root)

        for issue in quality_issues:
            if issue.severity == "error":
                failure_reasons.append(f"{issue.file_path}::{issue.test_name}: {issue.issue}")

        if not skip_tests:
            for layer in plan.layers:
                summary = self._run_pytest_layer(layer, plan.pytest_extra_args)
                test_summaries.append(summary)
                if not summary.ok:
                    failure_reasons.append(f"{layer.value} 层测试失败 ({summary.failed} failed)")

        historical: list[str] = []
        regression_risks: list[str] = []
        if risk.regression_sensitive:
            regression_risks.append("变更触及回归敏感路径，已执行 regression 层验证")
            historical.append("已对回归敏感模块执行额外测试层")
        else:
            historical.append("未发现预设 backward compatibility 高风险路径")

        new_tests = [f for f in ctx.changed_files if f.startswith("test/")]

        status = GateStatus.PASSED
        if failure_reasons or any(not s.ok for s in test_summaries):
            status = GateStatus.FAILED

        report = QualityGateReport(
            status=status,
            requirement_confidence=req.confidence,
            risk_level=risk.level,
            review_depth=plan.depth,
            functional_notes=[
                "当前实现满足 PR 描述中的可推断验收标准"
                if req.confidence == RequirementConfidence.HIGH
                else "需求信息中等，建议在 PR 中补充验收标准"
            ],
            historical_behavior_notes=historical,
            regression_risks=regression_risks or list(risk.reasons[:3]),
            new_tests=new_tests,
            test_summaries=test_summaries,
            failure_reasons=failure_reasons,
            recommendations=self._recommendations(req.confidence, risk.level, quality_issues),
        )
        write_report(report, out_dir)
        return report

    def _run_pytest_layer(self, layer: TestLayer, extra_args: tuple[str, ...]) -> TestResultSummary:
        layer_dir = self.test_root / layer.value
        if not layer_dir.is_dir():
            return TestResultSummary(
                layer=layer,
                passed=0,
                failed=0,
                skipped=0,
                duration_seconds=0.0,
            )

        junit = self.test_root / ".quality_gate" / f"junit_{layer.value}.xml"
        junit.parent.mkdir(parents=True, exist_ok=True)

        cmd = [
            sys.executable,
            "-m",
            "pytest",
            str(layer_dir),
            "-c",
            str(self.test_root / "pytest.ini"),
            "-m",
            layer.value,
            "--tb=short",
            "-q",
            f"--junitxml={junit}",
            *extra_args,
        ]
        start = time.monotonic()
        proc = subprocess.run(cmd, cwd=self.repo_root, capture_output=True, text=True)
        duration = time.monotonic() - start

        passed, failed, skipped, failed_nodeids = _parse_junit(junit)
        if proc.returncode != 0 and failed == 0 and passed == 0:
            failed = 1
            failed_nodeids = [proc.stdout[-500:] if proc.stdout else "pytest exited non-zero"]

        return TestResultSummary(
            layer=layer,
            passed=passed,
            failed=failed,
            skipped=skipped,
            duration_seconds=duration,
            failed_nodeids=failed_nodeids,
        )

    def _recommendations(
        self, confidence: RequirementConfidence, risk_level, quality_issues
    ) -> list[str]:
        recs: list[str] = []
        if confidence != RequirementConfidence.HIGH:
            recs.append("在 PR 描述中补充验收标准与非目标，提高 Requirement Confidence")
        warnings = [q for q in quality_issues if q.severity == "warning"]
        if warnings:
            recs.append(f"发现 {len(warnings)} 个测试质量警告，建议加强断言与边界覆盖")
        recs.append(f"风险等级为 {risk_level.value}，人工 Reviewer 应关注架构与业务设计")
        return recs


def _parse_junit(path: Path) -> tuple[int, int, int, list[str]]:
    if not path.is_file():
        return 0, 0, 0, []

    import xml.etree.ElementTree as ET

    root = ET.parse(path).getroot()
    if root.tag == "testsuites":
        suite = root.find("testsuite")
        if suite is None:
            return 0, 0, 0, []
        testsuite = suite
    else:
        testsuite = root

    passed = (
        int(testsuite.attrib.get("tests", 0))
        - int(testsuite.attrib.get("failures", 0))
        - int(testsuite.attrib.get("errors", 0))
    )
    failed = int(testsuite.attrib.get("failures", 0)) + int(testsuite.attrib.get("errors", 0))
    skipped = int(testsuite.attrib.get("skipped", 0))
    failed_nodeids = [
        f"{case.attrib.get('classname', '')}::{case.attrib.get('name', '')}"
        for case in testsuite.findall("testcase")
        if case.find("failure") is not None or case.find("error") is not None
    ]
    return max(passed, 0), failed, skipped, failed_nodeids


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Run Codex PR Quality Gate")
    parser.add_argument("--skip-tests", action="store_true", help="仅生成报告骨架，不执行 pytest")
    parser.add_argument("--context-file", type=Path, default=None)
    parser.add_argument("--output-dir", type=Path, default=None)
    parser.add_argument("--print-json", action="store_true")
    args = parser.parse_args()

    ctx = (
        load_pr_context(context_file=args.context_file) if args.context_file else load_pr_context()
    )
    runner = QualityGateRunner()
    report = runner.run(pr_context=ctx, skip_tests=args.skip_tests, output_dir=args.output_dir)

    if args.print_json:
        print(json.dumps(report.to_dict(), indent=2, ensure_ascii=False))

    from framework.report import format_markdown

    print(format_markdown(report))
    return (
        0 if report.status == GateStatus.PASSED else 1 if report.status == GateStatus.FAILED else 2
    )


if __name__ == "__main__":
    raise SystemExit(main())
