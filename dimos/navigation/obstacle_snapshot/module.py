# Copyright 2026 Dimensional Inc.
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

import time
from threading import RLock
from typing import Any

from reactivex.disposable import Disposable

from dimos.core.core import rpc
from dimos.core.module import Module
from dimos.core.stream import In
from dimos.msgs.geometry_msgs.PoseStamped import PoseStamped
from dimos.navigation.obstacle_snapshot.types import (
    ObstacleNavOutcome,
    ObstacleTriggerReason,
)
from dimos.navigation.replanning_a_star.planner_spec import ReplanningAStarPlannerSpec
from dimos.robot.unitree.unitree_skill_container_spec import UnitreeSkillContainerSpec
from dimos.utils.logging_config import setup_logger

logger = setup_logger()


class ObstacleSnapshotModule(Module):
    """Cross-first obstacle handling: try step over, then replan, then stop."""

    odom: In[PoseStamped]

    _planner: ReplanningAStarPlannerSpec
    _unitree: UnitreeSkillContainerSpec | None = None

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._latest_odom: PoseStamped | None = None
        self._lock = RLock()
        self._cross_attempts = 0
        self._detour_attempts = 0
        self._episode_xy: tuple[float, float] | None = None

    @rpc
    def start(self) -> None:
        super().start()
        self.register_disposable(Disposable(self.odom.subscribe(self._on_odom)))

    @rpc
    def stop(self) -> None:
        super().stop()

    def _on_odom(self, odom: PoseStamped) -> None:
        self._latest_odom = odom

    @rpc
    def handle_navigation_block(self, reason: ObstacleTriggerReason) -> ObstacleNavOutcome:
        """Priority: cross (high) → detour/replan (low) → stop."""
        if not self.config.g.obstacle_navigation:
            return "detour"

        self._maybe_reset_episode()

        g = self.config.g
        max_cross = g.obstacle_cross_max_attempts
        max_detour = g.obstacle_detour_max_attempts

        if self._cross_attempts < max_cross:
            self._cross_attempts += 1
            logger.info(
                "Obstacle navigation: trying cross",
                attempt=self._cross_attempts,
                max_attempts=max_cross,
                reason=reason,
            )
            if self._try_cross():
                self._reset_episode()
                print(
                    "========== Go2 越障成功 ==========\n"
                    f"原因: {reason}\n继续前往原目标\n"
                    "========================================"
                )
                return "continue"

        if self._detour_attempts < max_detour:
            self._detour_attempts += 1
            logger.info(
                "Obstacle navigation: cross failed, replanning (detour)",
                detour_attempt=self._detour_attempts,
                reason=reason,
            )
            print(
                "========== Go2 越障失败，改绕障 ==========\n"
                f"原因: {reason}\n"
                "========================================"
            )
            return "detour"

        logger.warning(
            "Obstacle navigation: cross and detour exhausted; stopping",
            reason=reason,
        )
        print(
            "========== Go2 无法通过，已停下 ==========\n"
            f"原因: {reason}\n"
            "========================================"
        )
        return "stop"

    def _try_cross(self) -> bool:
        forward_m = self.config.g.obstacle_cross_forward_m
        if not self.config.g.simulation and self._unitree is not None:
            self._unitree.execute_sport_command("FootRaiseHeight")
            time.sleep(0.4)

        ok = self._planner.attempt_cross_forward(forward_m)

        if not self.config.g.simulation and self._unitree is not None:
            self._unitree.execute_sport_command("RecoveryStand")
            time.sleep(0.2)

        return ok

    def _maybe_reset_episode(self) -> None:
        with self._lock:
            odom = self._latest_odom
            if odom is None:
                return
            xy = (float(odom.position.x), float(odom.position.y))
            if self._episode_xy is None:
                self._episode_xy = xy
                return
            if (
                (xy[0] - self._episode_xy[0]) ** 2 + (xy[1] - self._episode_xy[1]) ** 2
            ) ** 0.5 > self.config.g.obstacle_episode_reset_m:
                self._reset_episode()

    def _reset_episode(self) -> None:
        with self._lock:
            self._cross_attempts = 0
            self._detour_attempts = 0
            if self._latest_odom is not None:
                self._episode_xy = (
                    float(self._latest_odom.position.x),
                    float(self._latest_odom.position.y),
                )
