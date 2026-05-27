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

import json
from pathlib import Path

from framework.models import GateStatus
from framework.pr_context import PRContext
from framework.runner import QualityGateRunner
import pytest


@pytest.mark.quality_gate
def test_runner_produces_report_files(tmp_path: Path) -> None:
    ctx = PRContext(
        title="test gate",
        body="acceptance criteria: smoke test passes",
        changed_files=("test/unit/test_framework_core.py",),
    )
    QualityGateRunner().run(
        pr_context=ctx,
        skip_tests=True,
        output_dir=tmp_path,
    )
    assert (tmp_path / "quality_gate_report.json").is_file()
    assert (tmp_path / "quality_gate_report.md").is_file()
    data = json.loads((tmp_path / "quality_gate_report.json").read_text())
    assert data["status"] in {s.value for s in GateStatus}
