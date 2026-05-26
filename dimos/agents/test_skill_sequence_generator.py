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

"""Unit 5 generator 层单测:用注入的 fake LLM 验证重试与错误处理。

铁律:本测试文件**不允许真的访问 OpenAI / Anthropic / 任何外部 LLM API**。
所有 LLM 行为都通过依赖注入的 fake 类模拟。
"""

from __future__ import annotations

from collections.abc import Sequence
import json
from typing import Any

from langchain_core.messages import AIMessage
import pytest

from dimos.agents.skill_sequence_generator import (
    DEFAULT_SYSTEM_PROMPT,
    SkillSequenceGenerationError,
    SkillSequenceGenerator,
    _extract_text,
    _format_retry_prompt,
)
from dimos.agents.skill_sequence_schema import (
    SkillSequence,
    SkillSequenceJSONDecodeError,
    SkillSequenceSchemaError,
)

# ---------- 辅助:可脚本化的 fake LLM ----------


class FakeChatModel:
    """脚本化的 fake LangChain chat model。

    每次 ``invoke`` 按 ``responses`` 顺序返回一段字符串(包成 ``AIMessage``),
    并把传入的 ``messages`` 全部存到 ``calls`` 里供断言。

    完全在内存中模拟,不发任何网络请求,符合"CI 单测不调外部 LLM"的要求。
    """

    def __init__(self, responses: Sequence[str]):
        self.responses: list[str] = list(responses)
        self.calls: list[list[Any]] = []
        self.i: int = 0

    def invoke(self, messages: Sequence[Any], **_: Any) -> AIMessage:
        if self.i >= len(self.responses):
            raise AssertionError(
                f"FakeChatModel 被调用了 {self.i + 1} 次,但只准备了 "
                f"{len(self.responses)} 条预设回复"
            )
        # 深拷贝 messages 到调用记录(转 list 即可,内部对象不可变)
        self.calls.append(list(messages))
        text = self.responses[self.i]
        self.i += 1
        return AIMessage(content=text)


def _valid_payload() -> dict[str, Any]:
    return {
        "task": "向前 3 米",
        "steps": [
            {"skill": "GoToW", "args": {"x": 3.0, "y": 0.0}, "expect_duration_s": 6.0},
        ],
    }


def _valid_json() -> str:
    return json.dumps(_valid_payload(), ensure_ascii=False)


# ---------- 构造时校验 ----------


def test_init_rejects_zero_retries() -> None:
    fake = FakeChatModel(responses=[_valid_json()])
    with pytest.raises(ValueError, match="max_schema_retries"):
        SkillSequenceGenerator(max_schema_retries=0, llm=fake)


def test_init_uses_injected_llm_and_skips_init_chat_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """传入 ``llm=...`` 时不应再调用 ``init_chat_model``——避免无密钥触发网络。"""

    def boom(*_a: Any, **_kw: Any) -> Any:
        raise AssertionError("不应调用 init_chat_model:已通过 llm= 注入")

    monkeypatch.setattr("dimos.agents.skill_sequence_generator.init_chat_model", boom)
    fake = FakeChatModel(responses=[_valid_json()])
    gen = SkillSequenceGenerator(llm=fake)
    assert gen._llm is fake


def test_init_calls_init_chat_model_when_no_llm_injected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """没注入 ``llm=`` 时应转发到 ``init_chat_model(model, model_provider)``。"""
    captured: dict[str, Any] = {}
    sentinel = FakeChatModel(responses=[_valid_json()])

    def fake_init(**kwargs: Any) -> Any:
        captured.update(kwargs)
        return sentinel

    monkeypatch.setattr("dimos.agents.skill_sequence_generator.init_chat_model", fake_init)

    gen = SkillSequenceGenerator(model="gpt-4o", model_provider="openai")
    assert captured == {"model": "gpt-4o", "model_provider": "openai"}
    assert gen._llm is sentinel


# ---------- 单次成功路径 ----------


def test_generate_first_try_returns_sequence() -> None:
    fake = FakeChatModel(responses=[_valid_json()])
    gen = SkillSequenceGenerator(llm=fake)

    seq = gen.generate(
        task="向前 3 米",
        truth_summary={"start": (0.0, 0.0), "end": (3.0, 0.0), "path_length": 3.0},
    )

    assert isinstance(seq, SkillSequence)
    assert seq.task == "向前 3 米"
    assert seq.steps[0].skill == "GoToW"
    # 单次成功只调一次 LLM
    assert fake.i == 1


def test_prompt_includes_task_truth_skills_and_feedback() -> None:
    """user message 应包含 task / truth_summary / available skills / feedback 四块。"""
    fake = FakeChatModel(responses=[_valid_json()])
    gen = SkillSequenceGenerator(
        llm=fake,
        skill_registry={"GoToW": lambda **_: "ok", "rotate": lambda **_: "ok"},
    )

    feedback = [{"iter": 1, "metrics": {"ate": 0.7}, "note": "偏右 1m"}]
    gen.generate(
        task="向前 3 米",
        truth_summary={"path_length": 3.0},
        feedback=feedback,
    )

    # 第一条消息是 system,第二条是 user
    sys_msg, user_msg = fake.calls[0][0], fake.calls[0][1]
    assert sys_msg.content == DEFAULT_SYSTEM_PROMPT
    text = user_msg.content
    assert "向前 3 米" in text
    assert "path_length" in text
    assert "GoToW" in text and "rotate" in text
    assert "iter" in text and "0.7" in text


def test_prompt_omits_feedback_section_when_none() -> None:
    fake = FakeChatModel(responses=[_valid_json()])
    gen = SkillSequenceGenerator(llm=fake)
    gen.generate(task="t", truth_summary={"k": 1})
    user_msg_text = fake.calls[0][1].content
    assert "上一轮闭环反馈" not in user_msg_text


def test_prompt_handles_non_json_truth_summary_values() -> None:
    """``truth_summary`` 里夹了非 JSON 原生类型(tuple)也要拼得出 prompt。"""
    fake = FakeChatModel(responses=[_valid_json()])
    gen = SkillSequenceGenerator(llm=fake)
    gen.generate(
        task="t",
        truth_summary={"start": (0.0, 0.0), "end": (3.0, 0.0)},
    )
    # 没崩 = 通过;同时检查内容确实进了 prompt
    user_text = fake.calls[0][1].content
    assert "start" in user_text and "end" in user_text


# ---------- schema 重试路径 ----------


def test_generate_retries_on_bad_json_then_succeeds() -> None:
    """首次返回坏 JSON,第二次返回好 JSON,应成功并发生 1 次重试。"""
    fake = FakeChatModel(responses=["not json at all", _valid_json()])
    gen = SkillSequenceGenerator(llm=fake, max_schema_retries=3)

    seq = gen.generate(task="t", truth_summary={})

    assert seq.task == "向前 3 米"
    # 两次 LLM 调用
    assert fake.i == 2
    # 第二次调用的 message 列表必须包含第一次的 AI 回复 + 修正反馈
    second_messages = fake.calls[1]
    assert len(second_messages) >= 4  # system + user + ai + retry-user
    ai_replies = [m for m in second_messages if isinstance(m, AIMessage)]
    assert any("not json at all" in str(m.content) for m in ai_replies)
    # 最后一条 HumanMessage 应包含"修正"提示
    last_human = second_messages[-1]
    assert "JSON" in last_human.content or "json" in last_human.content


def test_generate_retries_on_schema_violation() -> None:
    """首次返回合法 JSON 但字段缺失,第二次合法,应成功。"""
    bad = json.dumps({"steps": []})  # 缺 task,空 steps
    fake = FakeChatModel(responses=[bad, _valid_json()])
    gen = SkillSequenceGenerator(llm=fake, max_schema_retries=3)

    seq = gen.generate(task="t", truth_summary={})
    assert seq.task == "向前 3 米"
    assert fake.i == 2

    # retry prompt 应携带 pydantic 错误明细
    retry_text = fake.calls[1][-1].content
    assert "schema" in retry_text.lower() or "pydantic" in retry_text.lower()


def test_generate_exhausts_retries_and_raises() -> None:
    """连续 ``max_schema_retries`` 次都坏 → 抛 ``SkillSequenceGenerationError``。"""
    fake = FakeChatModel(responses=["bad1", "bad2", "bad3"])
    gen = SkillSequenceGenerator(llm=fake, max_schema_retries=3)

    with pytest.raises(SkillSequenceGenerationError) as ei:
        gen.generate(task="t", truth_summary={})

    assert ei.value.attempts == 3
    assert isinstance(ei.value.last_error, SkillSequenceJSONDecodeError)
    assert fake.i == 3


def test_generate_max_retries_one_means_no_retry() -> None:
    """``max_schema_retries=1`` 表示首次失败立刻抛,不再重试。"""
    fake = FakeChatModel(responses=["bad"])
    gen = SkillSequenceGenerator(llm=fake, max_schema_retries=1)

    with pytest.raises(SkillSequenceGenerationError) as ei:
        gen.generate(task="t", truth_summary={})
    assert ei.value.attempts == 1
    assert fake.i == 1


def test_generate_propagates_last_error_type_when_schema_fail() -> None:
    """连续 schema 错(JSON 合法但字段错)→ ``last_error`` 是 ``SkillSequenceSchemaError``。"""
    bad = json.dumps({"steps": []})
    fake = FakeChatModel(responses=[bad, bad])
    gen = SkillSequenceGenerator(llm=fake, max_schema_retries=2)

    with pytest.raises(SkillSequenceGenerationError) as ei:
        gen.generate(task="t", truth_summary={})
    assert isinstance(ei.value.last_error, SkillSequenceSchemaError)


def test_generate_accepts_markdown_wrapped_response() -> None:
    """LLM 包了 markdown code fence 也要能解析。"""
    wrapped = "```json\n" + _valid_json() + "\n```"
    fake = FakeChatModel(responses=[wrapped])
    gen = SkillSequenceGenerator(llm=fake)
    seq = gen.generate(task="t", truth_summary={})
    assert seq.task == "向前 3 米"


# ---------- 内部工具函数 ----------


def test_extract_text_from_string_content() -> None:
    msg = AIMessage(content="hello")
    assert _extract_text(msg) == "hello"


def test_extract_text_from_list_content() -> None:
    """LangChain 富内容(list of dict)也要能扁平化成单段字符串。"""
    msg = AIMessage(
        content=[
            {"type": "text", "text": "foo"},
            {"type": "text", "text": "bar"},
        ]
    )
    assert _extract_text(msg) == "foobar"


def test_extract_text_from_raw_string() -> None:
    # 不是 message 对象时,getattr 取不到 content,会退化成 str()
    assert _extract_text("plain") == "plain"


def test_format_retry_prompt_for_decode_error_mentions_json() -> None:
    err = SkillSequenceJSONDecodeError("bad json")
    text = _format_retry_prompt(err)
    assert "JSON" in text or "json" in text


def test_format_retry_prompt_for_schema_error_contains_errors_payload() -> None:
    err = SkillSequenceSchemaError(
        "schema bad",
        errors=[{"loc": ["task"], "msg": "field required", "type": "missing"}],
    )
    text = _format_retry_prompt(err)
    assert "task" in text
    assert "field required" in text
