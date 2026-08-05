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

"""Static safety envelope checks, independent of the AI-authored control code.

This is Stage 3 of the verification funnel
(docs/development/hardware_verification_loop.md#22-stage-3). The whole point of
this module is that it must NOT trust the code it is checking: it inspects the
commands a candidate skill would send, without importing or executing that
skill's own logic, so a bug in the generated code cannot also disable the
check that would have caught it.

A companion runtime watchdog (out of scope for this module) should subscribe
to the same envelope and the live proprioception/external-camera streams
during a hardware trial and trigger a hard stop on violation.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SafetyEnvelope:
    """Hard bounds a commanded trajectory must stay within to be allowed on hardware."""

    max_linear_velocity_mps: float
    max_angular_velocity_radps: float
    max_joint_torque_nm: float
    workspace_bounds_m: tuple[float, float, float, float]  # (min_x, max_x, min_y, max_y)


@dataclass(frozen=True)
class CommandSample:
    """One sample of a commanded (not necessarily yet-executed) trajectory point."""

    timestamp_s: float
    linear_velocity_mps: float
    angular_velocity_radps: float
    joint_torque_nm: float
    position_m: tuple[float, float]


def check_command_sequence(envelope: SafetyEnvelope, commands: list[CommandSample]) -> list[str]:
    """Return every envelope violation found in ``commands``; empty means safe to deploy."""
    violations: list[str] = []
    min_x, max_x, min_y, max_y = envelope.workspace_bounds_m
    for cmd in commands:
        if abs(cmd.linear_velocity_mps) > envelope.max_linear_velocity_mps:
            violations.append(
                f"t={cmd.timestamp_s:.2f}s linear velocity {cmd.linear_velocity_mps} "
                f"exceeds {envelope.max_linear_velocity_mps} m/s"
            )
        if abs(cmd.angular_velocity_radps) > envelope.max_angular_velocity_radps:
            violations.append(
                f"t={cmd.timestamp_s:.2f}s angular velocity {cmd.angular_velocity_radps} "
                f"exceeds {envelope.max_angular_velocity_radps} rad/s"
            )
        if abs(cmd.joint_torque_nm) > envelope.max_joint_torque_nm:
            violations.append(
                f"t={cmd.timestamp_s:.2f}s joint torque {cmd.joint_torque_nm} "
                f"exceeds {envelope.max_joint_torque_nm} Nm"
            )
        x, y = cmd.position_m
        if not (min_x <= x <= max_x and min_y <= y <= max_y):
            violations.append(
                f"t={cmd.timestamp_s:.2f}s position {cmd.position_m} outside "
                f"workspace bounds {envelope.workspace_bounds_m}"
            )
    return violations
