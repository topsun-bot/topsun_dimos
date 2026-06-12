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

"""一致性守卫:迎宾系统提示词里列出的手势名必须与 ``ARM_COMMANDS`` 保持同步。

``GREETER_SYSTEM_PROMPT`` 用的是手敲的中文手势清单(便于中文场景),但手势名(英文键)
来自 ``commands.py`` 的 ``ARM_COMMANDS``。本测试确保两者集合完全一致——一旦 ``commands.py``
新增/改名/删除手势而提示词未同步,测试立即失败,避免 LLM 看到失效或缺失的手势名。
"""

import re

from dimos.robot.unitree.g1.effectors.high_level.commands import ARM_COMMANDS
from dimos.robot.unitree.g1.greeter_system_prompt import GREETER_SYSTEM_PROMPT


def test_greeter_prompt_arm_commands_in_sync() -> None:
    # The only ASCII double-quoted alphanumeric tokens in the prompt are the
    # arm-gesture names listed under `execute_arm_command`.
    quoted = set(re.findall(r'"([A-Za-z][A-Za-z0-9]*)"', GREETER_SYSTEM_PROMPT))
    expected = set(ARM_COMMANDS)
    assert quoted == expected, (
        "GREETER_SYSTEM_PROMPT 的手势清单与 ARM_COMMANDS 不同步。\n"
        f"  提示词缺少: {sorted(expected - quoted)}\n"
        f"  提示词多余/失效: {sorted(quoted - expected)}"
    )
