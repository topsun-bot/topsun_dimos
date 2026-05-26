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

"""数据闭环 Unit 5:LLM 生成的"skill 调用序列"JSON Schema。

闭环系统中,LLM 一次性输出一段 JSON,描述"接下来 N 个 skill 调用",
而不是传统 tool-calling 一来一回。本文件用 pydantic v2 定义这段 JSON 的
结构与校验,并提供把已校验的 ``SkillSequence`` 映射到具体可调用对象的工具方法。

JSON 形态由 plan 的 D 节固定:

```json
{
  "task": "围绕办公楼跑一圈",
  "rationale": "可选的 LLM 思路文本",
  "steps": [
    {"skill": "GoToW", "args": {"x": 1.5, "y": 0.0, "yaw_deg": 0.0}, "expect_duration_s": 8.0},
    {"skill": "rotate", "args": {"degrees": 90}},
    {"skill": "GoToW", "args": {"x": 1.5, "y": 1.5}}
  ]
}
```
"""

from __future__ import annotations

from collections.abc import Callable
import json
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError


class SkillSequenceError(ValueError):
    """``SkillSequence`` 解析或校验失败时抛出的统一异常基类。

    继承自 ``ValueError`` 以便调用方既可以按业务粒度捕获,也可以按内置异常捕获。
    """


class SkillSequenceJSONDecodeError(SkillSequenceError):
    """LLM 返回的字符串不是合法 JSON 时抛出。

    通常发生在模型直接返回了一段散文,或 JSON 中含未转义的字符。``message``
    属性给出 ``json`` 模块的原始报错,可直接拼回到给 LLM 的 retry prompt 中。
    """


class SkillSequenceSchemaError(SkillSequenceError):
    """JSON 解析成功但字段不符合 ``SkillSequence`` schema 时抛出。

    ``errors`` 属性保留 pydantic 的结构化错误列表,可序列化后塞回 LLM 让其自修。
    """

    def __init__(self, message: str, errors: list[dict[str, Any]]):
        super().__init__(message)
        self.errors: list[dict[str, Any]] = errors


class SkillCall(BaseModel):
    """单次 skill 调用的 schema。

    Attributes:
        skill: 已注册的 skill 名(必须与 ``@skill`` decorator 注册的函数名一致)。
        args: 传给 skill 的关键字参数;允许为空表示无参数。
        expect_duration_s: 可选的期望执行时长(秒),供闭环里做超时与节奏控制。
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    skill: str = Field(..., min_length=1, description="已注册 skill 名,不可为空字符串")
    args: dict[str, Any] = Field(default_factory=dict, description="传入 skill 的 kwargs")
    expect_duration_s: float | None = Field(
        default=None,
        ge=0.0,
        description="预计执行秒数,非负;留空表示不约束",
    )


class SkillSequence(BaseModel):
    """一段连续的 skill 调用序列(LLM 一轮输出的产物)。

    Attributes:
        task: 自然语言描述本段序列要完成的任务,用于报告与 critic feedback。
        rationale: 可选的 LLM 推理文本,便于审计与后续 retry 时复用上下文。
        steps: 按顺序执行的 ``SkillCall`` 列表;至少一个。
    """

    model_config = ConfigDict(extra="forbid")

    task: str = Field(..., min_length=1, description="本段任务的自然语言描述")
    rationale: str | None = Field(default=None, description="LLM 的思考过程,可选")
    steps: list[SkillCall] = Field(
        ..., min_length=1, description="按顺序执行的 skill 调用列表,至少 1 个"
    )

    @classmethod
    def from_json(cls, raw: str) -> SkillSequence:
        """从原始 JSON 字符串解析出 ``SkillSequence``,失败时抛出友好异常。

        Args:
            raw: 待解析的 JSON 文本。允许在前后包裹空白或 markdown code-fence,
                方法会先剥离 ```json ... ``` 之类的常见包装。

        Returns:
            校验通过的 ``SkillSequence`` 实例。

        Raises:
            SkillSequenceJSONDecodeError: 字符串不是合法 JSON。
            SkillSequenceSchemaError: JSON 合法但不符合本类的 schema。
        """
        if raw is None:
            raise SkillSequenceJSONDecodeError("空输入:LLM 没有返回任何内容")

        cleaned = _strip_markdown_fence(raw).strip()
        if not cleaned:
            raise SkillSequenceJSONDecodeError("空输入:剥离代码块包装后没有 JSON 内容")

        try:
            payload = json.loads(cleaned)
        except json.JSONDecodeError as e:
            raise SkillSequenceJSONDecodeError(
                f"无法把 LLM 输出解析为 JSON:{e.msg}(行 {e.lineno} 列 {e.colno})"
            ) from e

        try:
            return cls.model_validate(payload)
        except ValidationError as e:
            # pydantic v2 的 ErrorDetails 是 TypedDict;为接口稳定性,
            # 复制成普通 dict 后再透传,避免 TypedDict 与 dict 在 mypy 下不兼容。
            errors: list[dict[str, Any]] = [dict(err) for err in e.errors(include_url=False)]
            raise SkillSequenceSchemaError(
                f"JSON 不符合 SkillSequence schema,共 {len(errors)} 处错误:{errors}",
                errors=errors,
            ) from e

    def to_calls(
        self, registry: dict[str, Callable[..., Any]]
    ) -> list[tuple[Callable[..., Any], dict[str, Any]]]:
        """把 ``steps`` 逐个映射到 ``(callable, kwargs)`` 对。

        Args:
            registry: skill 名 → callable 的字典(通常由 ``@skill`` 注册表扁平化而来)。

        Returns:
            与 ``steps`` 等长的列表,元素为 ``(callable, kwargs)`` 元组;
            kwargs 是 ``step.args`` 的副本,避免调用方意外修改本对象。

        Raises:
            KeyError: 某个 step 的 ``skill`` 名在 ``registry`` 中找不到。
        """
        calls: list[tuple[Callable[..., Any], dict[str, Any]]] = []
        for idx, step in enumerate(self.steps):
            if step.skill not in registry:
                available = sorted(registry.keys())
                raise KeyError(
                    f"步骤 {idx} 引用了未注册的 skill '{step.skill}';已注册可用:{available}"
                )
            calls.append((registry[step.skill], dict(step.args)))
        return calls


def _strip_markdown_fence(text: str) -> str:
    """剥离常见的 markdown code-fence 包装(```json ... ``` 等)。

    LLM 经常把 JSON 包在 ```json ... ``` 里;本函数只在头尾恰好是
    fence 时剥离,中间是裸 JSON 的情况原样返回。
    """
    stripped = text.strip()
    if not stripped.startswith("```"):
        return text

    # 去掉首行 fence(可能带 ```json 之类的语言标识)
    first_newline = stripped.find("\n")
    if first_newline == -1:
        # 整段没有换行,无法定位 fence 体,原样返回
        return text
    body = stripped[first_newline + 1 :]

    # 去掉末尾 fence
    if body.rstrip().endswith("```"):
        body = body.rstrip()[: -len("```")]
    return body
