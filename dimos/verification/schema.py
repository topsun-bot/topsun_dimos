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

"""Shared data contracts for the graduated (sim -> rig -> field) hardware
acceptance pipeline described in docs/development/real_robot_closed_loop_verification.md.

These types are the common language every gate (static checks, Isaac Sim
regression replay, tethered/fenced rig checks, full-field trials with an
external camera + VLM judge) is expected to produce and consume, so that a
failed run always carries a structured verdict instead of a bare pass/fail.
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field


class Gate(str, Enum):
    """One stage of the shift-left acceptance pyramid, cheapest first."""

    STATIC = "static"  # lint / type check / unit tests
    SIM_REGRESSION = "sim_regression"  # Isaac Sim replay of recorded failure scenarios
    RIG = "rig"  # tethered or fenced low-risk hardware check
    FIELD = "field"  # full-field real-world trial


class FailureCategory(str, Enum):
    """Root-cause bucket for a failed verdict, used to route feedback instead
    of always asking the coding agent to rewrite logic."""

    CODE_LOGIC = "code_logic"
    TIMING_LATENCY = "timing_latency"
    CALIBRATION_DRIFT = "calibration_drift"
    HARDWARE_FAULT = "hardware_fault"
    ENV_NOISE = "env_noise"
    SAFETY_STOP = "safety_stop"
    FLAKY = "flaky"


class SafetyStopReason(str, Enum):
    """Hard stop conditions. A safety stop is never a task failure to hand
    back to the coding agent — it pauses the loop and escalates instead."""

    ORIENTATION_LIMIT = "orientation_limit"
    JOINT_TORQUE_LIMIT = "joint_torque_limit"
    JOINT_TEMPERATURE_LIMIT = "joint_temperature_limit"
    GEOFENCE_VIOLATION = "geofence_violation"
    OBSERVER_LOST = "observer_lost"
    MANUAL_ESTOP = "manual_estop"


class RubricItem(BaseModel):
    """One checkable acceptance criterion for a task/skill."""

    id: str
    description: str
    passed: bool | None = None
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    evidence_timestamp_s: float | None = None


class VerificationVerdict(BaseModel):
    """Structured output of a single gate's judge."""

    gate: Gate
    passed: bool
    rubric: list[RubricItem] = Field(default_factory=list)
    failure_category: FailureCategory | None = None
    failure_category_confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    safety_stop_reason: SafetyStopReason | None = None
    notes: str = ""

    @property
    def is_safety_stop(self) -> bool:
        return self.safety_stop_reason is not None


class RunManifest(BaseModel):
    """Full audit record for one closed-loop iteration through the gates."""

    run_id: str
    git_sha: str
    branch: str
    task_id: str
    started_at: datetime
    finished_at: datetime | None = None
    verdicts: list[VerificationVerdict] = Field(default_factory=list)
    video_refs: list[str] = Field(default_factory=list)
    telemetry_ref: str | None = None
    attempt_index: int = 0

    @property
    def highest_gate_reached(self) -> Gate | None:
        return self.verdicts[-1].gate if self.verdicts else None

    @property
    def passed_all_gates(self) -> bool:
        return bool(self.verdicts) and all(v.passed for v in self.verdicts)

    @property
    def dominant_failure_category(self) -> FailureCategory | None:
        """Root cause of the first gate that failed, if any."""
        for verdict in self.verdicts:
            if not verdict.passed:
                return verdict.failure_category
        return None


def summarize_repeated_trials(
    manifests: list[RunManifest],
    min_pass_rate: float = 1.0,
) -> tuple[bool, FailureCategory | None]:
    """Statistical merge-readiness gate for a batch of repeated field trials
    of the same task_id (see section 2.6 of the design doc).

    Returns (merge_ready, dominant_failure_category). merge_ready is True
    only when every trial passed and min_pass_rate == 1.0; a mix of pass and
    fail is reported as FLAKY rather than CODE_LOGIC, since a single failing
    trial does not by itself indicate a code defect.
    """
    if not manifests:
        return False, None

    pass_count = sum(1 for m in manifests if m.passed_all_gates)
    pass_rate = pass_count / len(manifests)

    if pass_rate >= min_pass_rate:
        return True, None

    if 0 < pass_count < len(manifests):
        return False, FailureCategory.FLAKY

    categories = [m.dominant_failure_category for m in manifests if not m.passed_all_gates]
    categories = [c for c in categories if c is not None]
    if not categories:
        return False, None
    most_common = Counter(categories).most_common(1)[0][0]
    return False, most_common
