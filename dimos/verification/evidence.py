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

"""Structured failure evidence bundles for the diagnose-fix-redeploy loop.

Packages what today is communicated as an ad hoc verbal description ("it
walked forward then fell left") into a reproducible record a coding agent can
act on directly, instead of starting each re-diagnosis from scratch. See
docs/development/hardware_verification_loop.md#24-stage-5.

The recording referenced by ``recording_dataset`` is expected to already exist
as a ``dimos.memory2`` dataset (aligned proprioception + camera streams),
replayable via ``dimos.utils.testing.replay.Memory2ReplayAdapter``; this
module only packages the structured verdict alongside a pointer to it.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from dimos.verification.judge import VerificationResult


@dataclass(frozen=True)
class EvidenceBundle:
    """Everything needed to diagnose one failed verification without re-running it."""

    task_id: str
    git_sha: str
    result: VerificationResult
    recording_dataset: str
    likely_cause_summary: str
    environment_notes: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, object]:
        return {
            "task_id": self.task_id,
            "git_sha": self.git_sha,
            "stage": self.result.stage.value,
            "accepted": self.result.accepted,
            "reject_reasons": list(self.result.reject_reasons),
            "recording_dataset": self.recording_dataset,
            "likely_cause_summary": self.likely_cause_summary,
            "environment_notes": dict(self.environment_notes),
        }


def build_evidence_bundle(
    *,
    task_id: str,
    git_sha: str,
    result: VerificationResult,
    recording_dataset: str,
    environment_notes: dict[str, str] | None = None,
) -> EvidenceBundle:
    """Assemble an evidence bundle for a failed (or undetermined) verification result."""
    if result.accepted:
        raise ValueError("Evidence bundles are only built for failed/undetermined results")
    summary = (
        result.vlm_opinion.summary
        if result.vlm_opinion is not None
        else "; ".join(result.reject_reasons) or "no signal reported a verdict (undetermined)"
    )
    return EvidenceBundle(
        task_id=task_id,
        git_sha=git_sha,
        result=result,
        recording_dataset=recording_dataset,
        likely_cause_summary=summary,
        environment_notes=environment_notes or {},
    )
