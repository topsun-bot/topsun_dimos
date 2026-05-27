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

from framework.models import GateStatus, RiskLevel
from framework.pr_context import PRContext
from framework.report import format_markdown
from framework.requirement import RequirementConfidenceChecker
from framework.risk import RiskClassifier
import pytest


@pytest.mark.unit
@pytest.mark.quality_gate
def test_requirement_checker_blocks_empty_pr() -> None:
    result = RequirementConfidenceChecker().check(PRContext(title="", body="", changed_files=()))
    assert result.can_proceed is False


@pytest.mark.unit
@pytest.mark.quality_gate
def test_risk_classifier_flags_mcp_changes() -> None:
    assessment = RiskClassifier().assess(
        PRContext(
            title="mcp",
            body="acceptance criteria: update tools",
            changed_files=("dimos/agents/mcp/mcp_server.py",),
        )
    )
    assert assessment.level == RiskLevel.CRITICAL


@pytest.mark.unit
@pytest.mark.quality_gate
def test_markdown_report_contains_status_line() -> None:
    from framework.models import QualityGateReport, RequirementConfidence, ReviewDepth

    report = QualityGateReport(
        status=GateStatus.PASSED,
        requirement_confidence=RequirementConfidence.HIGH,
        risk_level=RiskLevel.LOW,
        review_depth=ReviewDepth.SHALLOW,
    )
    md = format_markdown(report)
    assert "Codex Quality Gate Passed" in md
