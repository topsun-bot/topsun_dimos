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

"""Importing G1 DDS SDK must not require the Unitree wheel (docs / collection)."""

from __future__ import annotations

import sys


def test_dds_sdk_import_does_not_load_unitree_sdk2py() -> None:
    doomed = [
        name
        for name in sys.modules
        if name == "unitree_sdk2py" or name.startswith("unitree_sdk2py.")
    ]
    for name in doomed:
        del sys.modules[name]
    for name in list(sys.modules):
        if name.startswith("dimos.robot.unitree.g1.effectors.high_level.dds_sdk"):
            del sys.modules[name]

    from dimos.robot.unitree.g1.effectors.high_level import dds_sdk

    assert dds_sdk.G1HighLevelDdsSdk is not None
    assert not any(
        name == "unitree_sdk2py" or name.startswith("unitree_sdk2py.") for name in sys.modules
    )
