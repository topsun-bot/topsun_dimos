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

"""Skill-level dry-run checks for the threshold crossing skill.

These tests sit one level above ``test_obstacle_decision.py``: instead of
calling the planner directly, they drive the real
``ThresholdCrossingSkillContainer.cross_threshold`` skill method and assert on
the string it returns to an agent/MCP caller.

``ThresholdCrossingSkillContainer`` is a ``dimos.core.module.Module``: its
``__init__`` stands up an LCM/RPC broker, which is not available (and not
needed) for a deterministic dry-run check. We therefore construct the instance
with ``object.__new__`` and reproduce the one line of real ``__init__`` state
the dry-run path depends on (``self._planner``). This exercises the actual
skill method body -- obstacle construction, planner integration, the
``dry_run`` gate and ``summary()`` formatting -- without booting a blueprint
or a simulator. It does NOT exercise Module wiring, ``cmd_vel`` transport, or
physical execution; that requires an execution blueprint + MuJoCo physics.
"""

from __future__ import annotations

from dimos.experimental.navigation_obstacle_mvp.module import (
    ThresholdCrossingSkillContainer,
)
from dimos.experimental.navigation_obstacle_mvp.obstacle_decision import (
    ThresholdCrossingPlanner,
)


def _skill_container() -> ThresholdCrossingSkillContainer:
    """Build the skill container without the Module RPC broker.

    Mirrors the real ``__init__`` (``self._planner = ThresholdCrossingPlanner()``)
    after bypassing ``Module.__init__``. The dry-run path of
    ``cross_threshold`` only reads ``self._planner``.
    """
    container = object.__new__(ThresholdCrossingSkillContainer)
    container._planner = ThresholdCrossingPlanner()
    return container


def test_skill_dry_run_plans_crossable_threshold() -> None:
    container = _skill_container()

    result = container.cross_threshold(
        height_m=0.12,
        width_m=0.16,
        distance_m=0.35,
        lateral_clearance_m=0.20,
        dry_run=True,
    )

    assert result.startswith("cross:")
    # The skill must emit the full physical maneuver, in order, for a
    # threshold inside the crossing envelope.
    for step in (
        "enter BalanceStand",
        "raise feet to 0.16m",  # 0.12m height + 0.04m margin, clamped to envelope
        "raise body by 0.03m",
        "cross threshold",
        "restore body height",
        "restore foot raise height",
    ):
        assert step in result, f"missing maneuver step: {step!r} in {result!r}"


def test_skill_dry_run_rejects_wall_as_avoid() -> None:
    container = _skill_container()

    result = container.cross_threshold(
        height_m=0.30,
        width_m=0.16,
        distance_m=0.35,
        lateral_clearance_m=0.20,
        dry_run=True,
    )

    assert result.startswith("avoid:")
    assert "0.18m crossing limit" in result
    # A wall must not produce a physical maneuver.
    assert "actions:" not in result


def test_skill_dry_run_ignores_low_bump() -> None:
    container = _skill_container()

    result = container.cross_threshold(
        height_m=0.05,
        width_m=0.10,
        distance_m=0.35,
        lateral_clearance_m=0.20,
        dry_run=True,
    )

    assert result.startswith("ignore:")
    assert "actions:" not in result


def test_skill_dry_run_rejects_wide_obstacle() -> None:
    container = _skill_container()

    result = container.cross_threshold(
        height_m=0.12,
        width_m=0.80,
        distance_m=0.35,
        lateral_clearance_m=0.20,
        dry_run=True,
    )

    assert result.startswith("avoid:")
    assert "width" in result


def test_skill_dry_run_requests_alignment_when_skewed() -> None:
    container = _skill_container()

    result = container.cross_threshold(
        height_m=0.12,
        width_m=0.16,
        distance_m=0.35,
        lateral_clearance_m=0.20,
        heading_error_rad=0.9,
        dry_run=True,
    )

    assert result.startswith("align:")


def test_skill_dry_run_is_hardware_safe() -> None:
    """dry_run=True must return a plan without touching the (absent) robot
    connection or cmd_vel stream -- proving the planning path is side-effect
    free and safe to call before any blueprint is wired."""
    container = _skill_container()

    # Would raise AttributeError on self._connection / self.cmd_vel if the
    # dry-run gate were broken and it attempted execution. Use a known
    # in-envelope threshold (0.12m): note the effective max crossable height
    # is ~0.14m, not the configured 0.18m, because a 0.04m foot-raise margin
    # is added before the 0.18m foot-raise limit check.
    result = container.cross_threshold(
        height_m=0.12,
        width_m=0.16,
        distance_m=0.35,
        lateral_clearance_m=0.20,
        dry_run=True,
    )

    assert result.startswith("cross:")
