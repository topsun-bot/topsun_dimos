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

import sys
from pathlib import Path

import pytest

# 使 framework 包可从 test/ 根导入
_TEST_ROOT = Path(__file__).resolve().parent
if str(_TEST_ROOT) not in sys.path:
    sys.path.insert(0, str(_TEST_ROOT))

from framework.evidence import EvidenceCollector  # noqa: E402


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line("markers", "unit: 单元测试层（Quality Gate）")
    config.addinivalue_line("markers", "integration: 集成测试层（Quality Gate）")
    config.addinivalue_line("markers", "regression: 回归测试层（Quality Gate）")
    config.addinivalue_line("markers", "quality_gate: 框架自检")


@pytest.fixture
def evidence() -> EvidenceCollector:
    """基于证据的审查收集器 — 测试中记录 file/location/trigger。"""
    return EvidenceCollector()
