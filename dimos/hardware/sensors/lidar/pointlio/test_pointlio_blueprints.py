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

import importlib

import pytest

from dimos.hardware.sensors.lidar.pointlio import pointlio_blueprints


def test_pointlio_rust_replay_imports_when_env_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Registered blueprints must import without DIMOS_MID360_PCAP (dimos list / CI)."""
    monkeypatch.delenv("DIMOS_MID360_PCAP", raising=False)
    module = importlib.reload(pointlio_blueprints)
    assert module.pointlio_rust_replay is not None
