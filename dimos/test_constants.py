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

"""Smoke test for `dimos.constants`."""

from __future__ import annotations

from dimos import constants


def test_default_thread_join_timeout_is_positive() -> None:
    """`DEFAULT_THREAD_JOIN_TIMEOUT` must be a positive float used by graceful shutdown."""
    assert isinstance(constants.DEFAULT_THREAD_JOIN_TIMEOUT, float)
    assert constants.DEFAULT_THREAD_JOIN_TIMEOUT > 0
