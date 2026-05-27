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

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class GateStatus(str, Enum):
    PASSED = "passed"
    FAILED = "failed"
    BLOCKED = "blocked"  # 需求置信度不足，未执行测试


class RequirementConfidence(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class RiskLevel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class ReviewDepth(str, Enum):
    SHALLOW = "shallow"
    NORMAL = "normal"
    DEEP = "deep"


class TestLayer(str, Enum):
    UNIT = "unit"
    INTEGRATION = "integration"
    REGRESSION = "regression"


@dataclass(frozen=True)
class Evidence:
    """基于证据的审查条目 — 禁止无依据猜测。"""

    file_path: str
    location: str
    trigger: str
    risk_reason: str
    test_basis: str | None = None
    verification: str | None = None

    def to_dict(self) -> dict[str, str | None]:
        return {
            "file_path": self.file_path,
            "location": self.location,
            "trigger": self.trigger,
            "risk_reason": self.risk_reason,
            "test_basis": self.test_basis,
            "verification": self.verification,
        }


@dataclass
class TestResultSummary:
    layer: TestLayer
    passed: int
    failed: int
    skipped: int
    duration_seconds: float
    failed_nodeids: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.failed == 0


@dataclass
class QualityGateReport:
    """结构化 Quality Gate 输出，对应文档第八节示例。"""

    status: GateStatus
    requirement_confidence: RequirementConfidence
    risk_level: RiskLevel
    review_depth: ReviewDepth
    functional_notes: list[str] = field(default_factory=list)
    historical_behavior_notes: list[str] = field(default_factory=list)
    regression_risks: list[str] = field(default_factory=list)
    new_tests: list[str] = field(default_factory=list)
    test_summaries: list[TestResultSummary] = field(default_factory=list)
    failure_reasons: list[str] = field(default_factory=list)
    evidences: list[Evidence] = field(default_factory=list)
    recommendations: list[str] = field(default_factory=list)
    blocked_reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status.value,
            "requirement_confidence": self.requirement_confidence.value,
            "risk_level": self.risk_level.value,
            "review_depth": self.review_depth.value,
            "functional_notes": self.functional_notes,
            "historical_behavior_notes": self.historical_behavior_notes,
            "regression_risks": self.regression_risks,
            "new_tests": self.new_tests,
            "test_summaries": [
                {
                    "layer": s.layer.value,
                    "passed": s.passed,
                    "failed": s.failed,
                    "skipped": s.skipped,
                    "duration_seconds": s.duration_seconds,
                    "failed_nodeids": s.failed_nodeids,
                    "ok": s.ok,
                }
                for s in self.test_summaries
            ],
            "failure_reasons": self.failure_reasons,
            "evidences": [e.to_dict() for e in self.evidences],
            "recommendations": self.recommendations,
            "blocked_reason": self.blocked_reason,
        }
