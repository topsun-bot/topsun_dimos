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

"""回归：GlobalConfig 默认值与历史行为保持一致。"""

import pytest

from dimos.core.global_config import GlobalConfig


@pytest.mark.regression
def test_replay_db_default_unchanged() -> None:
    assert GlobalConfig().replay_db == "go2_short"


@pytest.mark.regression
def test_n_workers_default_unchanged() -> None:
    assert GlobalConfig().n_workers == 2


@pytest.mark.regression
def test_robot_width_default_unchanged(evidence) -> None:
    cfg = GlobalConfig()
    assert cfg.robot_width == 0.3
    evidence.add(
        file_path="dimos/core/global_config.py",
        location="robot_width field",
        trigger="实例化 GlobalConfig() 无覆盖",
        risk_reason="机器人碰撞模型宽度为导航栈依赖的默认参数",
        test_basis="test/regression/test_global_config_defaults.py",
        verification=f"robot_width={cfg.robot_width}",
    )
