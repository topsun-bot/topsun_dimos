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

"""Machine-checkable task specifications for the hardware verification funnel.

See docs/development/hardware_verification_loop.md for the full design: a task
spec is a versioned, machine-checkable definition of "what does it mean for the
robot to have done X", used to judge both simulation regression trials and real
hardware trials against the same criteria.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True)
class Predicate:
    """One machine-checkable pass/fail condition evaluated against a signal report.

    ``signal`` selects which signal report the predicate reads (e.g.
    "proprioception", "external_camera"); ``field`` is the metric name within
    that report. ``min``/``max`` are inclusive bounds; a bound left as ``None``
    is unchecked.
    """

    name: str
    signal: str
    field: str
    min: float | None = None
    max: float | None = None
    required: bool = True

    def evaluate(self, value: float | None) -> str | None:
        """Return a human-readable rejection reason, or ``None`` if satisfied."""
        if value is None:
            if self.required:
                return f"{self.name}: no value reported for {self.signal}.{self.field}"
            return None
        if self.min is not None and value < self.min:
            return f"{self.name}: {value} below minimum {self.min}"
        if self.max is not None and value > self.max:
            return f"{self.name}: {value} above maximum {self.max}"
        return None


@dataclass(frozen=True)
class TaskSpec:
    """A single named, versioned, machine-checkable robot task definition."""

    task_id: str
    description: str
    robot: str
    predicates: tuple[Predicate, ...]
    repeat_trials: int = 5
    max_trial_seconds: float = 60.0

    @staticmethod
    def from_dict(data: dict[str, Any]) -> TaskSpec:
        predicates = tuple(Predicate(**p) for p in data.get("predicates", []))
        return TaskSpec(
            task_id=str(data["task_id"]),
            description=str(data.get("description", "")),
            robot=str(data.get("robot", "")),
            predicates=predicates,
            repeat_trials=int(data.get("repeat_trials", 5)),
            max_trial_seconds=float(data.get("max_trial_seconds", 60.0)),
        )


def load_task_spec(path: Path) -> TaskSpec:
    """Load a single task spec YAML file."""
    with path.open() as f:
        data = yaml.safe_load(f)
    if not isinstance(data, dict):
        raise ValueError(f"Task spec {path} did not contain a YAML mapping")
    return TaskSpec.from_dict(data)


def load_task_specs(directory: Path) -> list[TaskSpec]:
    """Load every ``*.yaml``/``*.yml`` task spec in a directory (the golden regression set)."""
    paths = sorted(directory.glob("*.yaml")) + sorted(directory.glob("*.yml"))
    return [load_task_spec(path) for path in paths]
