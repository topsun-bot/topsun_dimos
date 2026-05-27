#!/usr/bin/env python3
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

"""CLI：运行 Codex PR Quality Gate。

用法（仓库根目录）::

    uv run python test/bin/run_quality_gate.py
    QUALITY_GATE_PR_BODY="$(cat pr.md)" uv run python test/bin/run_quality_gate.py --print-json
"""

from __future__ import annotations

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_TEST_ROOT = _REPO_ROOT / "test"
if str(_TEST_ROOT) not in sys.path:
    sys.path.insert(0, str(_TEST_ROOT))

from framework.runner import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
