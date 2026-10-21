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

from dimos_lcm.geometry_msgs import Pose
import pytest

from dimos.msgs.helpers import lcm_msg_type


def test_lcm_msg_type() -> None:
    assert lcm_msg_type("geometry_msgs.Pose") is Pose
    with pytest.raises(ImportError, match="has no message class 'Nope'") as missing:
        lcm_msg_type("geometry_msgs.Nope")
    # Missing class must be ImportError, not ModuleNotFoundError from a
    # nonexistent `dimos_lcm.geometry_msgs.Nope` submodule.
    assert type(missing.value) is ImportError
    with pytest.raises(ValueError):
        lcm_msg_type("Bare")
