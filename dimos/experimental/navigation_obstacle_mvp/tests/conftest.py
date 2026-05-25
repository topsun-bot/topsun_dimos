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

"""Override session autoconf — unit tests skip LCM autoconf; MuJoCo tests probe LCM when needed."""

from collections.abc import Iterator

import pytest

from dimos.e2e_tests.lcm_spy import LcmSpy
from dimos.experimental.navigation_obstacle_mvp.tests.sim_test_deps import require_lcm_multicast


@pytest.fixture(scope="session", autouse=True)
def _autoconf():
    yield


@pytest.fixture
def lcm_spy() -> Iterator[LcmSpy]:
    """Probe LCM before e2e lcm_spy so restricted networks skip instead of ERROR."""
    require_lcm_multicast()
    spy = LcmSpy()
    spy.start()
    yield spy
    spy.stop()
