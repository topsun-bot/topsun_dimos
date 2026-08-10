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

from datetime import UTC, datetime

from dimos.verification.schema import (
    FailureCategory,
    Gate,
    RunManifest,
    SafetyStopReason,
    VerificationVerdict,
    summarize_repeated_trials,
)


def _manifest(
    run_id: str, verdicts: list[VerificationVerdict], attempt_index: int = 0
) -> RunManifest:
    return RunManifest(
        run_id=run_id,
        git_sha="deadbeef",
        branch="claude/happy-meitner-vop710",
        task_id="g1.wave_greeting",
        started_at=datetime(2026, 8, 10, 12, 0, 0, tzinfo=UTC),
        verdicts=verdicts,
        attempt_index=attempt_index,
    )


def test_run_manifest_passed_all_gates_true_only_when_every_verdict_passes() -> None:
    manifest = _manifest(
        "run-1",
        [
            VerificationVerdict(gate=Gate.SIM_REGRESSION, passed=True),
            VerificationVerdict(gate=Gate.FIELD, passed=True),
        ],
    )
    assert manifest.passed_all_gates is True
    assert manifest.highest_gate_reached == Gate.FIELD


def test_run_manifest_reports_dominant_failure_category_of_first_failing_gate() -> None:
    manifest = _manifest(
        "run-2",
        [
            VerificationVerdict(gate=Gate.SIM_REGRESSION, passed=True),
            VerificationVerdict(
                gate=Gate.FIELD,
                passed=False,
                failure_category=FailureCategory.TIMING_LATENCY,
            ),
        ],
    )
    assert manifest.passed_all_gates is False
    assert manifest.dominant_failure_category == FailureCategory.TIMING_LATENCY


def test_verdict_is_safety_stop_only_when_reason_set() -> None:
    ok = VerificationVerdict(
        gate=Gate.FIELD, passed=False, failure_category=FailureCategory.CODE_LOGIC
    )
    stopped = VerificationVerdict(
        gate=Gate.FIELD,
        passed=False,
        safety_stop_reason=SafetyStopReason.ORIENTATION_LIMIT,
    )
    assert ok.is_safety_stop is False
    assert stopped.is_safety_stop is True


def test_summarize_repeated_trials_merge_ready_requires_all_passing() -> None:
    manifests = [
        _manifest("a", [VerificationVerdict(gate=Gate.FIELD, passed=True)], attempt_index=i)
        for i in range(3)
    ]
    merge_ready, category = summarize_repeated_trials(manifests)
    assert merge_ready is True
    assert category is None


def test_summarize_repeated_trials_flags_mixed_results_as_flaky() -> None:
    manifests = [
        _manifest("a", [VerificationVerdict(gate=Gate.FIELD, passed=True)], attempt_index=0),
        _manifest(
            "b",
            [
                VerificationVerdict(
                    gate=Gate.FIELD, passed=False, failure_category=FailureCategory.CODE_LOGIC
                )
            ],
            attempt_index=1,
        ),
        _manifest("c", [VerificationVerdict(gate=Gate.FIELD, passed=True)], attempt_index=2),
    ]
    merge_ready, category = summarize_repeated_trials(manifests)
    assert merge_ready is False
    assert category == FailureCategory.FLAKY


def test_summarize_repeated_trials_reports_consistent_failure_category() -> None:
    manifests = [
        _manifest(
            "a",
            [
                VerificationVerdict(
                    gate=Gate.FIELD,
                    passed=False,
                    failure_category=FailureCategory.CALIBRATION_DRIFT,
                )
            ],
            attempt_index=i,
        )
        for i in range(3)
    ]
    merge_ready, category = summarize_repeated_trials(manifests)
    assert merge_ready is False
    assert category == FailureCategory.CALIBRATION_DRIFT


def test_summarize_repeated_trials_empty_batch_is_not_merge_ready() -> None:
    merge_ready, category = summarize_repeated_trials([])
    assert merge_ready is False
    assert category is None
