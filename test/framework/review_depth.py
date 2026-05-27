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

from framework.models import ReviewDepth, RiskLevel, TestLayer
from framework.risk import RiskAssessment


@dataclass(frozen=True)
class ReviewPlan:
    depth: ReviewDepth
    layers: tuple[TestLayer, ...]
    pytest_extra_args: tuple[str, ...]


class AdaptiveReviewDepth:
    """根据风险等级自适应选择审查深度与测试层。"""

    def plan(self, risk: RiskAssessment) -> ReviewPlan:
        match risk.level:
            case RiskLevel.CRITICAL | RiskLevel.HIGH:
                return ReviewPlan(
                    depth=ReviewDepth.DEEP,
                    layers=(TestLayer.UNIT, TestLayer.INTEGRATION, TestLayer.REGRESSION),
                    pytest_extra_args=("-x",),
                )
            case RiskLevel.MEDIUM:
                return ReviewPlan(
                    depth=ReviewDepth.NORMAL,
                    layers=(TestLayer.UNIT, TestLayer.REGRESSION),
                    pytest_extra_args=(),
                )
            case RiskLevel.LOW:
                return ReviewPlan(
                    depth=ReviewDepth.SHALLOW,
                    layers=(TestLayer.UNIT,),
                    pytest_extra_args=(),
                )
