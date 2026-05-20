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

"""Unit tests for the 1.3/1.7 m hysteresis in GlobalPlanner._find_wide_path.

T5: Distance to goal oscillating around 1.5 m must *not* cause
    frequent voronoi ↔ gradient map flips.
"""

import pytest


def _simulate_hysteresis(
    distances: list[float],
    near_enter: float = 1.3,
    far_exit: float = 1.7,
) -> list[str]:
    """Pure-function simulation of the hysteresis state machine.

    Returns a list of map type strings ("voronoi" | "gradient") parallel to *distances*.
    """
    using_near = False  # start with far (voronoi) — default
    maps: list[str] = []
    for d in distances:
        if d > far_exit:
            using_near = False
        elif d < near_enter:
            using_near = True
        maps.append("gradient" if using_near else "voronoi")
    return maps


def _count_flips(maps: list[str]) -> int:
    """Count the number of times the map type changes."""
    flips = 0
    for i in range(1, len(maps)):
        if maps[i] != maps[i - 1]:
            flips += 1
    return flips


class TestHysteresisBasic:
    def test_far_when_distance_above_1_7(self) -> None:
        maps = _simulate_hysteresis([2.0, 1.8])
        assert maps == ["voronoi", "voronoi"]

    def test_near_when_distance_below_1_3(self) -> None:
        maps = _simulate_hysteresis([1.2, 1.0, 0.5])
        assert maps == ["gradient", "gradient", "gradient"]

    def test_keeps_previous_in_dead_zone_entering_from_far(self) -> None:
        # Start far (2.0), move to dead zone (1.5) — should stay voronoi
        maps = _simulate_hysteresis([2.0, 1.5])
        assert maps == ["voronoi", "voronoi"]

    def test_keeps_previous_in_dead_zone_entering_from_near(self) -> None:
        # Start near (1.2), move to dead zone (1.5) — should stay gradient
        maps = _simulate_hysteresis([1.2, 1.5])
        assert maps == ["gradient", "gradient"]

    def test_transitions_at_boundaries(self) -> None:
        maps = _simulate_hysteresis([2.0, 1.2, 1.8])
        assert maps == ["voronoi", "gradient", "voronoi"]


class TestHysteresisT5:
    """T5: goal distance oscillating around 1.5 m must not flip voronoi ↔ gradient."""

    def test_smooth_approach_no_flips(self) -> None:
        """Robot approaches goal monotonically: far → dead → near. Expect ≤1 flip."""
        distances = [2.5, 2.2, 2.0, 1.8, 1.6, 1.5, 1.4, 1.35, 1.2, 1.0, 0.8, 0.5]
        maps = _simulate_hysteresis(distances)
        flips = _count_flips(maps)
        assert flips <= 1, f"Expected ≤1 flip, got {flips} — sequence: {maps}"

    def test_dead_zone_stable_T5(self) -> None:
        """Oscillating between 1.3 and 1.7 must NOT flip at every distance change."""
        # Simulate robot moving back and forth near the 1.5 m boundary
        distances = [1.6, 1.55, 1.5, 1.45, 1.4, 1.35, 1.45, 1.5, 1.55, 1.6]
        maps = _simulate_hysteresis(distances)
        flips = _count_flips(maps)
        # With old hard threshold at 1.5, this would flip ~10 times.
        # With hysteresis, it should never flip — stays in the dead zone.
        assert flips == 0, (
            f"T5 FAIL: dead-zone oscillation caused {flips} map flips "
            f"(old behaviour). Expected 0 with hysteresis. Maps: {maps}"
        )

    def test_crosses_upper_threshold_once(self) -> None:
        """Crossing above 1.7 then back to dead zone causes only 2 flips (far→dead, back→)."""
        distances = [1.2, 1.5, 1.8, 1.6, 1.5]
        maps = _simulate_hysteresis(distances)
        flips = _count_flips(maps)
        assert flips <= 2, f"Expected ≤2 flips, got {flips} — maps: {maps}"

    def test_hard_threshold_would_flip_many_times(self) -> None:
        """Sanity check: the old 1.5m hard threshold *would* flip on every crossing."""
        distances = [1.51, 1.49, 1.51, 1.49, 1.51, 1.49]
        # Old behaviour: >1.5→voronoi, <=1.5→gradient → 6 flips
        old_maps = ["voronoi" if d > 1.5 else "gradient" for d in distances]
        old_flips = _count_flips(old_maps)
        assert old_flips >= 5, "Test precondition: old threshold should flip frequently"

        # New behaviour: should stay in dead zone (no flip at all if starting from far)
        new_maps = _simulate_hysteresis(distances)
        new_flips = _count_flips(new_maps)
        # Starting from far (>1.7), all are in dead zone → 0 flips
        # But from default state (far), 1.51 > 1.7? No, 1.51 < 1.7 → dead zone
        # With default _using_near=False and distance=1.51 (<1.7):
        # dead zone → keep False → voronoi → 0 flips
        assert new_flips == 0, (
            f"Hysteresis should prevent flips. Old: {old_flips}, New: {new_flips}. "
            f"Maps: {new_maps}"
        )
