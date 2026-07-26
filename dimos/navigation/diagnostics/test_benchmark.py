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

from dimos.navigation.diagnostics.benchmark import (
    run_event_loop_heartbeat_microbenchmark,
)


def test_event_loop_benchmark_compares_identical_sample_counts() -> None:
    result = run_event_loop_heartbeat_microbenchmark(
        samples=20,
        interval_sec=0.0005,
    )

    assert result["samples_per_mode"] == 20
    assert result["off"]["samples"] == 20
    assert result["full"]["samples"] == 20
    assert result["off"]["writer_error"] is None
    assert result["full"]["writer_error"] is None
    assert isinstance(result["p99_delta_pass"], bool)
