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

import ast
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class QualityIssueRecord:
    file_path: str
    test_name: str
    issue: str
    severity: str  # warning | error


class TestQualityVerifier:
    """测试质量验证 — 检测浅断言、空测试体等低价值模式。"""

    SHALLOW_ASSERTIONS = frozenset({"True", "False", "1", "0", "None"})

    def scan_file(self, path: Path) -> list[QualityIssueRecord]:
        issues: list[QualityIssueRecord] = []
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except SyntaxError:
            return [
                QualityIssueRecord(
                    file_path=str(path),
                    test_name="<module>",
                    issue="无法解析测试文件 AST",
                    severity="error",
                )
            ]

        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef) or not node.name.startswith("test_"):
                continue
            issues.extend(self._check_test_function(path, node))
        return issues

    def _check_test_function(self, path: Path, func: ast.FunctionDef) -> list[QualityIssueRecord]:
        issues: list[QualityIssueRecord] = []
        rel = str(path)

        body_stmts = [s for s in func.body if not isinstance(s, ast.Expr) or not isinstance(s.value, ast.Constant)]
        if not body_stmts:
            issues.append(
                QualityIssueRecord(
                    file_path=rel,
                    test_name=func.name,
                    issue="测试函数体为空或仅含 docstring",
                    severity="error",
                )
            )

        for child in ast.walk(func):
            if isinstance(child, ast.Assert) and isinstance(child.test, ast.Constant):
                val = child.test.value
                if val is True or val is False:
                    issues.append(
                        QualityIssueRecord(
                            file_path=rel,
                            test_name=func.name,
                            issue="存在恒真/恒假浅断言，未验证实际行为",
                            severity="warning",
                        )
                    )
        if len(func.body) == 1 and isinstance(func.body[0], ast.Pass):
            issues.append(
                QualityIssueRecord(
                    file_path=rel,
                    test_name=func.name,
                    issue="测试仅包含 pass，无实际验证",
                    severity="error",
                )
            )

        return issues

    def scan_directory(self, root: Path) -> list[QualityIssueRecord]:
        all_issues: list[QualityIssueRecord] = []
        for path in sorted(root.rglob("test_*.py")):
            all_issues.extend(self.scan_file(path))
        return all_issues
