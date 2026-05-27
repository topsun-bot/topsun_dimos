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

import pytest

from dimos.core.global_config import GlobalConfig


@pytest.mark.unit
def test_global_config_default_replay_is_false() -> None:
    cfg = GlobalConfig()
    assert cfg.replay is False


@pytest.mark.unit
def test_global_config_parses_mujoco_start_pos() -> None:
    cfg = GlobalConfig(mujoco_start_pos="-2.5, 3.0")
    assert cfg.mujoco_start_pos == "-2.5, 3.0"
