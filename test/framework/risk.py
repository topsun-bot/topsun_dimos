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
import re

from framework.models import RiskLevel
from framework.pr_context import PRContext


@dataclass(frozen=True)
class RiskAssessment:
    level: RiskLevel
    reasons: tuple[str, ...]
    regression_sensitive: bool


# DimOS 仓库风险规则（路径前缀 → 权重）
_PATH_RULES: list[tuple[re.Pattern[str], RiskLevel, str]] = [
    (re.compile(r"experimental/security", re.I), RiskLevel.CRITICAL, "安全相关模块"),
    (re.compile(r"agents/mcp", re.I), RiskLevel.CRITICAL, "MCP 代理与工具暴露"),
    (re.compile(r"robot/.*/connection", re.I), RiskLevel.HIGH, "机器人连接与硬件控制"),
    (re.compile(r"core/coordination", re.I), RiskLevel.HIGH, "模块协调与进程生命周期"),
    (re.compile(r"navigation/", re.I), RiskLevel.HIGH, "导航与路径规划"),
    (re.compile(r"manipulation/", re.I), RiskLevel.HIGH, "操作与抓取规划"),
    (re.compile(r"perception/", re.I), RiskLevel.MEDIUM, "感知流水线"),
    (re.compile(r"protocol/", re.I), RiskLevel.MEDIUM, "通信协议与 RPC"),
    (re.compile(r"^test/", re.I), RiskLevel.LOW, "仅测试目录变更"),
    (re.compile(r"^docs/", re.I), RiskLevel.LOW, "文档变更"),
]

_REGRESSION_PATTERNS = (
    re.compile(r"global_config", re.I),
    re.compile(r"run_registry", re.I),
    re.compile(r"blueprints", re.I),
    re.compile(r"module_coordinator", re.I),
    re.compile(r"connection\.py", re.I),
    re.compile(r"state", re.I),
)


class RiskClassifier:
    """根据变更路径进行风险等级分类。"""

    def assess(self, ctx: PRContext) -> RiskAssessment:
        if not ctx.changed_files:
            return RiskAssessment(
                level=RiskLevel.MEDIUM,
                reasons=("无变更文件列表，采用默认中等风险",),
                regression_sensitive=True,
            )

        max_level = RiskLevel.LOW
        reasons: list[str] = []
        regression_sensitive = False

        for path in ctx.changed_files:
            for pattern, level, reason in _PATH_RULES:
                if pattern.search(path):
                    if _level_rank(level) > _level_rank(max_level):
                        max_level = level
                    reasons.append(f"{path}: {reason}")
                    break

            for reg_pat in _REGRESSION_PATTERNS:
                if reg_pat.search(path):
                    regression_sensitive = True
                    reasons.append(f"{path}: 可能影响历史行为（回归敏感）")
                    break

        if not reasons:
            reasons.append("未匹配预设规则，使用中等风险默认值")
            if _level_rank(RiskLevel.MEDIUM) > _level_rank(max_level):
                max_level = RiskLevel.MEDIUM

        deduped: list[str] = []
        seen: set[str] = set()
        for r in reasons:
            if r not in seen:
                seen.add(r)
                deduped.append(r)

        return RiskAssessment(
            level=max_level,
            reasons=tuple(deduped[:12]),
            regression_sensitive=regression_sensitive,
        )


def _level_rank(level: RiskLevel) -> int:
    return {
        RiskLevel.LOW: 0,
        RiskLevel.MEDIUM: 1,
        RiskLevel.HIGH: 2,
        RiskLevel.CRITICAL: 3,
    }[level]
