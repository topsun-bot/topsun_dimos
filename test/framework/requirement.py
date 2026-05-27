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

from framework.models import RequirementConfidence
from framework.pr_context import PRContext


@dataclass(frozen=True)
class RequirementCheckResult:
    confidence: RequirementConfidence
    can_proceed: bool
    issues: tuple[str, ...]


class RequirementConfidenceChecker:
    """需求置信度检查 — 需求不清晰时停止生成/执行测试。"""

    MIN_BODY_CHARS = 40

    def check(self, ctx: PRContext) -> RequirementCheckResult:
        issues: list[str] = []

        if not ctx.title.strip():
            issues.append("PR 标题为空，无法推断变更意图")

        body = ctx.body.strip()
        if len(body) < self.MIN_BODY_CHARS:
            issues.append(f"PR 描述过短（<{self.MIN_BODY_CHARS} 字符），缺少验收标准或边界说明")

        if body and not ctx.has_acceptance_criteria:
            issues.append("未检测到验收标准（acceptance / 验收 / test plan 等关键词）")

        if not ctx.changed_files and not body:
            issues.append("无法获取变更文件列表且 PR 描述为空")

        if not issues:
            return RequirementCheckResult(
                confidence=RequirementConfidence.HIGH,
                can_proceed=True,
                issues=(),
            )

        if len(issues) >= 2 or not body:
            return RequirementCheckResult(
                confidence=RequirementConfidence.LOW,
                can_proceed=False,
                issues=tuple(issues),
            )

        return RequirementCheckResult(
            confidence=RequirementConfidence.MEDIUM,
            can_proceed=True,
            issues=tuple(issues),
        )
