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

from dimos.robot.unitree.g1.greeter_landmark_store import Landmark
from dimos.robot.unitree.g1.greeter_tour_skill import (
    GreeterTourSkillConfig,
    distance_2d,
    is_guide_request,
    parse_synonyms,
    within_arrival,
)

_CFG = GreeterTourSkillConfig()


def test_parse_synonyms_splits_and_dedups() -> None:
    assert parse_synonyms("卫生间, 洗手间、卫生间") == ("卫生间", "洗手间")
    assert parse_synonyms("") == ()
    assert parse_synonyms("  reception  ") == ("reception",)


def test_is_guide_request() -> None:
    assert is_guide_request("带我去前台", _CFG.guide_keywords) is True
    assert is_guide_request("带路去厕所", _CFG.guide_keywords) is True
    assert is_guide_request("厕所在哪", _CFG.guide_keywords) is False
    assert is_guide_request("前台怎么走", _CFG.guide_keywords) is False


def test_distance_2d() -> None:
    assert distance_2d(0.0, 0.0, 3.0, 4.0) == 5.0


def test_within_arrival() -> None:
    lm = Landmark(name="前台", x=10.0, y=0.0, z=0.0, intro_script="到了。")
    assert within_arrival(9.5, 0.0, lm, 0.8) is True
    assert within_arrival(8.0, 0.0, lm, 0.8) is False
    # z 不参与到站判定(只看平面距离)。
    assert within_arrival(10.0, 0.5, lm, 0.8) is True


def test_guide_vs_where_decision() -> None:
    """复刻 handle_location_query 的纯决策:带路 vs 仅讲解。"""
    lm = Landmark(name="前台", x=1.0, y=1.0, z=0.0, intro_script="前台到了。")
    guide_text = "带我去前台"
    where_text = "前台在哪"
    assert is_guide_request(guide_text, _CFG.guide_keywords) is True
    assert is_guide_request(where_text, _CFG.guide_keywords) is False
    assert lm.name in guide_text
