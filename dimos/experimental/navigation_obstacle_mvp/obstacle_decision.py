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

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

DecisionKind = Literal["ignore", "cross", "avoid", "align", "wait"]
ActionKind = Literal["sport", "velocity", "stop", "wait"]


@dataclass(frozen=True)
class ThresholdCrossingConfig:
    """Tunable limits for low-threshold crossing.

    The defaults target a conservative Go2-style maneuver: only obstacles that
    are clearly above normal floor noise and still within a small climb envelope
    are converted from "avoid/replan" into a physical crossing sequence.
    """

    trigger_height_m: float = 0.10
    max_crossable_height_m: float = 0.18
    max_crossable_width_m: float = 0.45
    max_approach_distance_m: float = 1.20
    approach_stop_distance_m: float = 0.18
    min_lateral_clearance_m: float = 0.12
    max_heading_error_rad: float = 0.35
    approach_speed_mps: float = 0.18
    crossing_speed_mps: float = 0.24
    settle_duration_s: float = 0.25
    min_motion_duration_s: float = 0.20
    post_cross_distance_m: float = 0.20
    foot_raise_margin_m: float = 0.04
    min_foot_raise_height_m: float = 0.12
    max_foot_raise_height_m: float = 0.18
    body_height_lift_m: float = 0.03


@dataclass(frozen=True)
class ThresholdObstacle:
    """Local estimate for a low obstacle directly ahead of the robot."""

    height_m: float
    width_m: float
    distance_m: float
    lateral_clearance_m: float
    heading_error_rad: float = 0.0

    def __post_init__(self) -> None:
        for field_name in ("height_m", "width_m", "distance_m", "lateral_clearance_m"):
            if getattr(self, field_name) < 0:
                raise ValueError(f"{field_name} must be non-negative")


@dataclass(frozen=True)
class CrossingAction:
    """One low-level physical action in a threshold crossing plan."""

    kind: ActionKind
    label: str
    duration_s: float = 0.0
    linear_x_mps: float = 0.0
    linear_y_mps: float = 0.0
    angular_z_radps: float = 0.0
    sport_api_id: int | None = None
    sport_parameter: float | bool | None = None


@dataclass(frozen=True)
class ThresholdDecision:
    """Decision plus optional executable action sequence."""

    kind: DecisionKind
    reason: str
    obstacle: ThresholdObstacle
    actions: tuple[CrossingAction, ...] = ()

    @property
    def can_cross(self) -> bool:
        return self.kind == "cross"

    def summary(self) -> str:
        if not self.actions:
            return f"{self.kind}: {self.reason}"

        action_labels = ", ".join(action.label for action in self.actions)
        return f"{self.kind}: {self.reason}; actions: {action_labels}"


class ThresholdCrossingPlanner:
    """Classify low obstacles and build the physical crossing primitive."""

    _SPORT_BALANCE_STAND = 1002
    _SPORT_BODY_HEIGHT = 1013
    _SPORT_FOOT_RAISE_HEIGHT = 1014

    def __init__(self, config: ThresholdCrossingConfig | None = None) -> None:
        self.config = config or ThresholdCrossingConfig()

    def evaluate(self, obstacle: ThresholdObstacle) -> ThresholdDecision:
        cfg = self.config

        if obstacle.height_m < cfg.trigger_height_m:
            return ThresholdDecision(
                kind="ignore",
                reason=(
                    f"obstacle height {obstacle.height_m:.2f}m is below "
                    f"{cfg.trigger_height_m:.2f}m crossing trigger"
                ),
                obstacle=obstacle,
            )

        if obstacle.height_m > cfg.max_crossable_height_m:
            return ThresholdDecision(
                kind="avoid",
                reason=(
                    f"obstacle height {obstacle.height_m:.2f}m exceeds "
                    f"{cfg.max_crossable_height_m:.2f}m crossing limit"
                ),
                obstacle=obstacle,
            )

        required_foot_raise_m = obstacle.height_m + cfg.foot_raise_margin_m
        if required_foot_raise_m > cfg.max_foot_raise_height_m:
            return ThresholdDecision(
                kind="avoid",
                reason=(
                    f"obstacle height {obstacle.height_m:.2f}m needs "
                    f"{required_foot_raise_m:.2f}m foot raise with clearance margin, above "
                    f"{cfg.max_foot_raise_height_m:.2f}m foot raise limit"
                ),
                obstacle=obstacle,
            )

        if obstacle.width_m > cfg.max_crossable_width_m:
            return ThresholdDecision(
                kind="avoid",
                reason=(
                    f"obstacle width {obstacle.width_m:.2f}m exceeds "
                    f"{cfg.max_crossable_width_m:.2f}m crossing limit"
                ),
                obstacle=obstacle,
            )

        if obstacle.lateral_clearance_m < cfg.min_lateral_clearance_m:
            return ThresholdDecision(
                kind="avoid",
                reason=(
                    f"lateral clearance {obstacle.lateral_clearance_m:.2f}m is below "
                    f"{cfg.min_lateral_clearance_m:.2f}m safety margin"
                ),
                obstacle=obstacle,
            )

        if abs(obstacle.heading_error_rad) > cfg.max_heading_error_rad:
            return ThresholdDecision(
                kind="align",
                reason=(
                    f"heading error {obstacle.heading_error_rad:.2f}rad exceeds "
                    f"{cfg.max_heading_error_rad:.2f}rad crossing alignment limit"
                ),
                obstacle=obstacle,
            )

        if obstacle.distance_m > cfg.max_approach_distance_m:
            return ThresholdDecision(
                kind="wait",
                reason=(
                    f"obstacle is {obstacle.distance_m:.2f}m away, outside "
                    f"{cfg.max_approach_distance_m:.2f}m local crossing window"
                ),
                obstacle=obstacle,
            )

        return ThresholdDecision(
            kind="cross",
            reason=(f"{obstacle.height_m:.2f}m threshold is within the physical crossing envelope"),
            obstacle=obstacle,
            actions=self._build_actions(obstacle),
        )

    def _build_actions(self, obstacle: ThresholdObstacle) -> tuple[CrossingAction, ...]:
        cfg = self.config
        foot_raise_height = self._clamp(
            obstacle.height_m + cfg.foot_raise_margin_m,
            cfg.min_foot_raise_height_m,
            cfg.max_foot_raise_height_m,
        )

        actions: list[CrossingAction] = [
            CrossingAction(
                kind="sport",
                label="enter BalanceStand",
                sport_api_id=self._SPORT_BALANCE_STAND,
            ),
            CrossingAction(
                kind="sport",
                label=f"raise feet to {foot_raise_height:.2f}m",
                sport_api_id=self._SPORT_FOOT_RAISE_HEIGHT,
                sport_parameter=foot_raise_height,
            ),
            CrossingAction(
                kind="sport",
                label=f"raise body by {cfg.body_height_lift_m:.2f}m",
                sport_api_id=self._SPORT_BODY_HEIGHT,
                sport_parameter=cfg.body_height_lift_m,
            ),
            CrossingAction(kind="wait", label="settle posture", duration_s=cfg.settle_duration_s),
        ]

        approach_distance = max(0.0, obstacle.distance_m - cfg.approach_stop_distance_m)
        if approach_distance > 0.01:
            actions.append(
                CrossingAction(
                    kind="velocity",
                    label=f"slow approach {approach_distance:.2f}m",
                    duration_s=self._duration(approach_distance, cfg.approach_speed_mps),
                    linear_x_mps=cfg.approach_speed_mps,
                )
            )
            actions.append(
                CrossingAction(kind="stop", label="pause before threshold", duration_s=0.1)
            )

        crossing_distance = (
            cfg.approach_stop_distance_m + obstacle.width_m + cfg.post_cross_distance_m
        )
        actions.extend(
            [
                CrossingAction(
                    kind="velocity",
                    label=f"cross threshold {crossing_distance:.2f}m",
                    duration_s=self._duration(crossing_distance, cfg.crossing_speed_mps),
                    linear_x_mps=cfg.crossing_speed_mps,
                ),
                CrossingAction(
                    kind="stop", label="stop after crossing", duration_s=cfg.settle_duration_s
                ),
                CrossingAction(
                    kind="sport",
                    label="restore body height",
                    sport_api_id=self._SPORT_BODY_HEIGHT,
                    sport_parameter=0.0,
                ),
                CrossingAction(
                    kind="sport",
                    label="restore foot raise height",
                    sport_api_id=self._SPORT_FOOT_RAISE_HEIGHT,
                    sport_parameter=0.0,
                ),
            ]
        )
        return tuple(actions)

    def _duration(self, distance_m: float, speed_mps: float) -> float:
        if speed_mps <= 0:
            raise ValueError("speed_mps must be positive")
        return max(self.config.min_motion_duration_s, distance_m / speed_mps)

    @staticmethod
    def _clamp(value: float, low: float, high: float) -> float:
        return min(max(value, low), high)
