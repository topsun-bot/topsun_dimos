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

from dimos.navigation.replanning_a_star.foot_raise import (
    CROSS_FOOT_RAISE_M,
    CROSS_FRONT_REACH_M,
    cross_threshold_gait,
    foot_raise_height_for_cost,
    front_reach_for_cost,
)


def test_foot_raise_scales_with_cost() -> None:
    assert foot_raise_height_for_cost(0, 0.15) == 0.0
    assert abs(foot_raise_height_for_cost(49, 0.15) - 0.15) < 0.001


def test_front_reach_scales_with_cost() -> None:
    assert front_reach_for_cost(0, 0.12) == 0.0
    assert abs(front_reach_for_cost(49, 0.12) - 0.12) < 0.001


def test_cross_gait_fixed_raise_and_reach() -> None:
    raise_m, reach_m = cross_threshold_gait(33, 0.18, 0.10)
    assert raise_m == CROSS_FOOT_RAISE_M
    assert reach_m == CROSS_FRONT_REACH_M


def test_cross_gait_zero_when_no_obstacle() -> None:
    assert cross_threshold_gait(0, 0.20, 0.05) == (0.0, 0.0)
