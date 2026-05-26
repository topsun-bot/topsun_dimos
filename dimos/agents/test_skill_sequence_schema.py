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

"""Unit 5 schema 层单测:校验 ``SkillSequence`` 解析、错误处理、调用映射。"""

from __future__ import annotations

import json
from typing import Any

import pytest

from dimos.agents.skill_sequence_schema import (
    SkillCall,
    SkillSequence,
    SkillSequenceJSONDecodeError,
    SkillSequenceSchemaError,
)

# ---------- 合法 JSON 解析 ----------


def _full_payload() -> dict[str, Any]:
    return {
        "task": "围绕办公楼跑一圈",
        "rationale": "先向前再左转",
        "steps": [
            {
                "skill": "GoToW",
                "args": {"x": 1.5, "y": 0.0, "yaw_deg": 0.0},
                "expect_duration_s": 8.0,
            },
            {"skill": "rotate", "args": {"degrees": 90}},
            {"skill": "GoToW", "args": {"x": 1.5, "y": 1.5}},
        ],
    }


def test_from_json_full_payload_succeeds() -> None:
    """plan D 节给出的完整示例 JSON 应当无错解析。"""
    seq = SkillSequence.from_json(json.dumps(_full_payload()))

    assert seq.task == "围绕办公楼跑一圈"
    assert seq.rationale == "先向前再左转"
    assert len(seq.steps) == 3
    assert seq.steps[0].skill == "GoToW"
    assert seq.steps[0].args == {"x": 1.5, "y": 0.0, "yaw_deg": 0.0}
    assert seq.steps[0].expect_duration_s == 8.0
    # 缺省的 expect_duration_s 应为 None
    assert seq.steps[2].expect_duration_s is None


def test_from_json_minimal_payload_succeeds() -> None:
    """``rationale``、``args``、``expect_duration_s`` 均可省略。"""
    raw = json.dumps(
        {
            "task": "向前 1 米",
            "steps": [{"skill": "GoToW"}],
        }
    )
    seq = SkillSequence.from_json(raw)
    assert seq.rationale is None
    assert seq.steps[0].args == {}
    assert seq.steps[0].expect_duration_s is None


def test_from_json_handles_markdown_fence() -> None:
    """LLM 常用 ```json ... ``` 包裹,``from_json`` 应剥离后再解析。"""
    payload = json.dumps(_full_payload())
    wrapped = f"```json\n{payload}\n```"
    seq = SkillSequence.from_json(wrapped)
    assert seq.task == "围绕办公楼跑一圈"

    # 不带语言标识也要能处理
    wrapped2 = f"```\n{payload}\n```"
    seq2 = SkillSequence.from_json(wrapped2)
    assert seq2.task == "围绕办公楼跑一圈"


# ---------- JSON 解析错误 ----------


def test_from_json_invalid_json_raises_decode_error() -> None:
    with pytest.raises(SkillSequenceJSONDecodeError) as ei:
        SkillSequence.from_json("{not really json")
    assert "JSON" in str(ei.value) or "json" in str(ei.value).lower()


def test_from_json_empty_raises_decode_error() -> None:
    with pytest.raises(SkillSequenceJSONDecodeError):
        SkillSequence.from_json("")
    with pytest.raises(SkillSequenceJSONDecodeError):
        SkillSequence.from_json("   \n  ")
    with pytest.raises(SkillSequenceJSONDecodeError):
        SkillSequence.from_json("```\n\n```")


# ---------- schema 校验错误 ----------


def test_missing_task_raises_schema_error() -> None:
    raw = json.dumps({"steps": [{"skill": "GoToW"}]})
    with pytest.raises(SkillSequenceSchemaError) as ei:
        SkillSequence.from_json(raw)
    # pydantic 错误结构应被透传出来
    assert any(err["loc"][-1] == "task" for err in ei.value.errors)


def test_empty_steps_raises_schema_error() -> None:
    raw = json.dumps({"task": "go", "steps": []})
    with pytest.raises(SkillSequenceSchemaError) as ei:
        SkillSequence.from_json(raw)
    assert any("steps" in str(err.get("loc", "")) for err in ei.value.errors)


def test_wrong_type_raises_schema_error() -> None:
    """``expect_duration_s`` 给字符串应被拒。"""
    raw = json.dumps(
        {
            "task": "go",
            "steps": [{"skill": "GoToW", "expect_duration_s": "nope"}],
        }
    )
    with pytest.raises(SkillSequenceSchemaError):
        SkillSequence.from_json(raw)


def test_negative_duration_rejected() -> None:
    raw = json.dumps(
        {
            "task": "go",
            "steps": [{"skill": "GoToW", "expect_duration_s": -1.0}],
        }
    )
    with pytest.raises(SkillSequenceSchemaError):
        SkillSequence.from_json(raw)


def test_empty_skill_name_rejected() -> None:
    raw = json.dumps({"task": "go", "steps": [{"skill": ""}]})
    with pytest.raises(SkillSequenceSchemaError):
        SkillSequence.from_json(raw)


def test_extra_fields_rejected() -> None:
    """``extra='forbid'`` 防止 LLM 偷塞未知键。"""
    raw = json.dumps(
        {
            "task": "go",
            "steps": [{"skill": "GoToW"}],
            "unexpected_field": 42,
        }
    )
    with pytest.raises(SkillSequenceSchemaError):
        SkillSequence.from_json(raw)


def test_schema_error_carries_errors_list() -> None:
    """生成器要把 ``errors`` 序列化回给 LLM,所以必须是可序列化的 list[dict]。"""
    raw = json.dumps({"steps": []})  # 缺 task + 空 steps,双错
    with pytest.raises(SkillSequenceSchemaError) as ei:
        SkillSequence.from_json(raw)
    assert isinstance(ei.value.errors, list)
    assert len(ei.value.errors) >= 2
    # 可 JSON 序列化
    assert json.dumps(ei.value.errors, default=str)


# ---------- to_calls 映射 ----------


def test_to_calls_maps_registered_skills() -> None:
    seq = SkillSequence.from_json(json.dumps(_full_payload()))

    def go_to_w(x: float, y: float, yaw_deg: float = 0.0) -> str:
        return f"goto({x},{y},{yaw_deg})"

    def rotate(degrees: float) -> str:
        return f"rotate({degrees})"

    registry = {"GoToW": go_to_w, "rotate": rotate}
    calls = seq.to_calls(registry)

    assert len(calls) == 3
    fn0, kw0 = calls[0]
    assert fn0 is go_to_w
    assert kw0 == {"x": 1.5, "y": 0.0, "yaw_deg": 0.0}

    fn1, kw1 = calls[1]
    assert fn1 is rotate
    assert kw1 == {"degrees": 90}

    # 实际可调用
    assert fn0(**kw0) == "goto(1.5,0.0,0.0)"
    assert fn1(**kw1) == "rotate(90)"


def test_to_calls_unknown_skill_raises_key_error() -> None:
    raw = json.dumps(
        {
            "task": "go",
            "steps": [
                {"skill": "GoToW", "args": {"x": 1.0}},
                {"skill": "wave_hand"},
            ],
        }
    )
    seq = SkillSequence.from_json(raw)
    registry = {"GoToW": lambda **_: "ok"}
    with pytest.raises(KeyError) as ei:
        seq.to_calls(registry)
    # 错误信息应同时点名缺失的 skill 与可用列表,便于排查
    assert "wave_hand" in str(ei.value)
    assert "GoToW" in str(ei.value)


def test_to_calls_returns_kwargs_copies() -> None:
    """``to_calls`` 应返回 ``args`` 的 *副本*,防止调用方意外污染原对象。"""
    seq = SkillSequence.from_json(
        json.dumps(
            {
                "task": "go",
                "steps": [{"skill": "GoToW", "args": {"x": 1.0}}],
            }
        )
    )
    registry = {"GoToW": lambda **_: "ok"}
    _, kwargs = seq.to_calls(registry)[0]
    kwargs["x"] = 999
    # 原 SkillCall 不受影响
    assert seq.steps[0].args == {"x": 1.0}


def test_to_calls_empty_registry_with_steps_raises() -> None:
    seq = SkillSequence.from_json(json.dumps({"task": "go", "steps": [{"skill": "GoToW"}]}))
    with pytest.raises(KeyError):
        seq.to_calls({})


# ---------- SkillCall 直接用 ----------


def test_skill_call_is_frozen() -> None:
    """``SkillCall`` 用 ``frozen=True`` 防止 critic/报告环节意外修改。"""
    call = SkillCall(skill="GoToW", args={"x": 1.0})
    with pytest.raises((TypeError, ValueError)):
        call.skill = "rotate"  # type: ignore[misc]
