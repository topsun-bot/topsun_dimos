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

"""Tests for the hardware verification funnel's predicate/judge/evidence logic.

Pure-logic tests -- no simulator, camera, or robot connection required. They
exercise the same code path used to judge both simulation-regression trials
and real hardware trials, per docs/development/hardware_verification_loop.md.
"""

from __future__ import annotations

from typing import Any

import pytest

from dimos.verification.evidence import build_evidence_bundle
from dimos.verification.judge import (
    FunnelStage,
    SignalReport,
    VlmOpinion,
    evaluate,
    evaluate_repeated_trials,
)
from dimos.verification.safety_governor import CommandSample, SafetyEnvelope, check_command_sequence
from dimos.verification.task_spec import Predicate, TaskSpec, load_task_spec


def _walk_forward_task(**overrides: Any) -> TaskSpec:
    predicates = (
        Predicate(
            name="net_displacement", signal="proprioception", field="net_displacement_m", min=2.0
        ),
        Predicate(name="no_fall", signal="proprioception", field="fall_events", max=0.0),
        Predicate(
            name="reached_target", signal="external_camera", field="target_offset_m", max=0.3
        ),
    )
    defaults: dict[str, Any] = dict(
        task_id="walk_forward_2m",
        description="Walk 2m forward",
        robot="go2",
        predicates=predicates,
    )
    defaults.update(overrides)
    return TaskSpec(**defaults)


def _good_reports() -> dict[str, SignalReport]:
    return {
        "proprioception": SignalReport(
            "proprioception", {"net_displacement_m": 2.4, "fall_events": 0}
        ),
        "external_camera": SignalReport("external_camera", {"target_offset_m": 0.1}),
    }


def _fallen_reports() -> dict[str, SignalReport]:
    return {
        "proprioception": SignalReport(
            "proprioception", {"net_displacement_m": 0.5, "fall_events": 1}
        ),
        "external_camera": SignalReport("external_camera", {"target_offset_m": 0.1}),
    }


def test_predicate_passes_within_bounds() -> None:
    task = _walk_forward_task()
    result = evaluate(task, _good_reports(), stage=FunnelStage.HARDWARE_TRIAL)
    assert result.accepted
    assert result.reject_reasons == ()


def test_predicate_rejects_with_reason_on_fall() -> None:
    task = _walk_forward_task()
    result = evaluate(task, _fallen_reports(), stage=FunnelStage.HARDWARE_TRIAL)
    assert not result.accepted
    assert any("no_fall" in reason for reason in result.reject_reasons)


def test_missing_signal_is_a_reject_reason_not_a_silent_pass() -> None:
    task = _walk_forward_task()
    reports = {
        "proprioception": SignalReport(
            "proprioception", {"net_displacement_m": 2.4, "fall_events": 0}
        )
    }
    result = evaluate(task, reports, stage=FunnelStage.HARDWARE_TRIAL)
    assert not result.accepted
    assert any("external_camera" in reason for reason in result.reject_reasons)


def test_vlm_opinion_is_recorded_but_never_overrides_predicates() -> None:
    task = _walk_forward_task()
    vlm = VlmOpinion(verdict=True, summary="Looked like a clean walk to me.")
    result = evaluate(task, _fallen_reports(), stage=FunnelStage.HARDWARE_TRIAL, vlm_opinion=vlm)
    assert not result.accepted, "predicate failure must win even if the VLM disagrees"
    assert result.vlm_opinion is vlm


def test_repeated_trials_require_all_trials_to_pass() -> None:
    task = _walk_forward_task(repeat_trials=3)
    trials = [
        evaluate(task, _good_reports(), stage=FunnelStage.HARDWARE_TRIAL),
        evaluate(task, _good_reports(), stage=FunnelStage.HARDWARE_TRIAL),
        evaluate(task, _fallen_reports(), stage=FunnelStage.HARDWARE_TRIAL),
    ]
    gate = evaluate_repeated_trials(task, trials)
    assert not gate.accepted, "one failing trial out of N must fail the merge gate"


def test_repeated_trials_pass_when_all_pass() -> None:
    task = _walk_forward_task(repeat_trials=2)
    trials = [evaluate(task, _good_reports(), stage=FunnelStage.HARDWARE_TRIAL) for _ in range(2)]
    gate = evaluate_repeated_trials(task, trials)
    assert gate.accepted


def test_repeated_trials_short_of_required_count_fails_the_gate() -> None:
    task = _walk_forward_task(repeat_trials=3)
    trials = [evaluate(task, _good_reports(), stage=FunnelStage.HARDWARE_TRIAL) for _ in range(2)]
    gate = evaluate_repeated_trials(task, trials)
    assert not gate.accepted
    assert "2/3" in gate.reject_reasons[0]


def test_safety_envelope_flags_overspeed_and_workspace_violation() -> None:
    envelope = SafetyEnvelope(
        max_linear_velocity_mps=1.0,
        max_angular_velocity_radps=1.5,
        max_joint_torque_nm=20.0,
        workspace_bounds_m=(-3.0, 3.0, -3.0, 3.0),
    )
    commands = [
        CommandSample(0.0, 0.5, 0.2, 5.0, (0.0, 0.0)),
        CommandSample(1.0, 2.0, 0.2, 5.0, (0.5, 0.5)),
        CommandSample(2.0, 0.5, 0.2, 5.0, (10.0, 0.0)),
    ]
    violations = check_command_sequence(envelope, commands)
    assert len(violations) == 2
    assert any("linear velocity" in v for v in violations)
    assert any("workspace bounds" in v for v in violations)


def test_safety_envelope_clean_sequence_has_no_violations() -> None:
    envelope = SafetyEnvelope(1.0, 1.5, 20.0, (-3.0, 3.0, -3.0, 3.0))
    commands = [CommandSample(0.0, 0.5, 0.2, 5.0, (0.0, 0.0))]
    assert check_command_sequence(envelope, commands) == []


def test_evidence_bundle_rejects_being_built_for_a_passing_result() -> None:
    task = _walk_forward_task()
    result = evaluate(task, _good_reports(), stage=FunnelStage.HARDWARE_TRIAL)
    with pytest.raises(ValueError):
        build_evidence_bundle(
            task_id=task.task_id, git_sha="abc123", result=result, recording_dataset="ds"
        )


def test_evidence_bundle_summarizes_predicate_failures() -> None:
    task = _walk_forward_task()
    result = evaluate(task, _fallen_reports(), stage=FunnelStage.HARDWARE_TRIAL)
    bundle = build_evidence_bundle(
        task_id=task.task_id, git_sha="abc123", result=result, recording_dataset="ds/run1"
    )
    assert bundle.git_sha == "abc123"
    assert "no_fall" in bundle.likely_cause_summary
    payload = bundle.to_dict()
    assert payload["accepted"] is False


def test_load_task_spec_from_yaml(tmp_path: Any) -> None:
    spec_path = tmp_path / "walk_forward_2m.yaml"
    spec_path.write_text(
        """
task_id: walk_forward_2m
description: Walk 2m forward
robot: go2
repeat_trials: 5
predicates:
  - name: net_displacement
    signal: proprioception
    field: net_displacement_m
    min: 2.0
"""
    )
    task = load_task_spec(spec_path)
    assert task.task_id == "walk_forward_2m"
    assert task.repeat_trials == 5
    assert len(task.predicates) == 1
