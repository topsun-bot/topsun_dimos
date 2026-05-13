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

"""Smoke test for the PR Loop automation.

This file exists solely to exercise the parallel CI + @codex review
pipeline. It runs in milliseconds, has no external dependencies, and
asserts trivially true conditions so it never blocks the loop on
intrinsic test failure.
"""


def test_pr_loop_smoke_truthy():
    assert True


def test_pr_loop_smoke_arithmetic():
    assert 1 + 1 == 2
