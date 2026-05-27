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

"""集成：蓝图注册表可导入且包含已知 agentic 蓝图。"""

import pytest


@pytest.mark.integration
def test_all_blueprints_exports_go2_agentic() -> None:
    from dimos.robot.all_blueprints import all_blueprints

    assert "unitree-go2-agentic" in all_blueprints
