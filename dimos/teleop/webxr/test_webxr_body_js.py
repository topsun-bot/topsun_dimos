# Copyright 2026 Dimensional Inc.
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

from pathlib import Path
import shutil
import subprocess

import pytest

_TEST = Path(__file__).parent / "web" / "static" / "webxr_body.test.mjs"


@pytest.mark.skipif(shutil.which("node") is None, reason="node is required to run captureBody tests")
def test_capture_body_js_contract() -> None:
    completed = subprocess.run(
        ["node", "--test", str(_TEST)],
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
