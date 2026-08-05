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

"""Multi-signal verification judge for the hardware verification funnel.

Combines machine-checkable task predicates (the primary, authoritative judge)
with an optional VLM opinion (a secondary signal, recorded for diagnosis but
never allowed to override a predicate-based verdict -- see
docs/development/hardware_verification_loop.md#23-stage-4).

Mirrors the accept/reject-with-reasons pattern used by
``dimos.perception.fiducial.fixture_verification``, so the same trial (in
MuJoCo or on real hardware) can be judged by the same code path.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from dimos.verification.task_spec import TaskSpec


class FunnelStage(str, Enum):
    """Which stage of the verification funnel produced this result.

    The stage itself is diagnostic signal: a sim-regression failure points at a
    logic bug, while a hardware-trial failure after a sim-regression pass
    points at a sim2real gap or an environment/hardware fault.
    """

    LINT = "lint"
    UNIT_TEST = "unit_test"
    SIM_REGRESSION = "sim_regression"
    SAFETY_ENVELOPE = "safety_envelope"
    HARDWARE_TRIAL = "hardware_trial"


@dataclass(frozen=True)
class SignalReport:
    """One signal source's readings for a single trial.

    ``metrics`` maps predicate field names (e.g. "net_displacement_m") to
    values. A field absent from ``metrics`` is treated as "not reported",
    which a required predicate rejects rather than silently passing.
    """

    signal: str
    metrics: dict[str, float]
    confidence: float = 1.0
    notes: str = ""


@dataclass(frozen=True)
class VlmOpinion:
    """Secondary, non-authoritative opinion from a vision-language model judge."""

    verdict: bool | None
    summary: str


@dataclass(frozen=True)
class VerificationResult:
    task_id: str
    stage: FunnelStage
    accepted: bool
    reject_reasons: tuple[str, ...]
    vlm_opinion: VlmOpinion | None = None

    @property
    def is_undetermined(self) -> bool:
        """True when no predicate reported a reason yet the trial wasn't accepted.

        This happens when every relevant signal dropped out (e.g. camera
        occlusion) -- treated distinctly from a confirmed failure so a
        dropped-signal trial can be retried rather than counted as a defect.
        """
        return not self.accepted and not self.reject_reasons


def evaluate(
    task: TaskSpec,
    reports: dict[str, SignalReport],
    *,
    stage: FunnelStage,
    vlm_opinion: VlmOpinion | None = None,
) -> VerificationResult:
    """Evaluate a task spec's predicates against one trial's signal reports."""
    reasons: list[str] = []
    for predicate in task.predicates:
        report = reports.get(predicate.signal)
        value = report.metrics.get(predicate.field) if report else None
        reason = predicate.evaluate(value)
        if reason:
            reasons.append(reason)

    return VerificationResult(
        task_id=task.task_id,
        stage=stage,
        accepted=not reasons,
        reject_reasons=tuple(reasons),
        vlm_opinion=vlm_opinion,
    )


def evaluate_repeated_trials(
    task: TaskSpec,
    trial_results: list[VerificationResult],
) -> VerificationResult:
    """Merge-gate check: all ``task.repeat_trials`` repeated trials must pass.

    A single passing trial carries little statistical weight for a physical
    system; this is Stage 7 of the funnel
    (docs/development/hardware_verification_loop.md#25-stage-67-回归集与合并门禁).
    """
    if len(trial_results) < task.repeat_trials:
        return VerificationResult(
            task_id=task.task_id,
            stage=FunnelStage.HARDWARE_TRIAL,
            accepted=False,
            reject_reasons=(
                f"only {len(trial_results)}/{task.repeat_trials} required trials completed",
            ),
        )

    failed_reasons = tuple(
        f"trial {i + 1} failed: {', '.join(r.reject_reasons) or 'undetermined'}"
        for i, r in enumerate(trial_results)
        if not r.accepted
    )
    return VerificationResult(
        task_id=task.task_id,
        stage=FunnelStage.HARDWARE_TRIAL,
        accepted=not failed_reasons,
        reject_reasons=failed_reasons,
    )
