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

"""数据闭环 Unit 5:``SkillSequenceGenerator`` —— 让 LLM 产出结构化 skill 序列。

与传统单轮 tool-calling 不同,本生成器一次性向 LLM 索要一段符合
``SkillSequence`` schema 的 JSON,描述"接下来 N 步 skill 调用"。
若返回的 JSON 不合法,生成器会把校验错误信息塞回模型让其自修,最多重试
``max_schema_retries`` 次,仍失败则抛 ``SkillSequenceGenerationError``。

注意:本模块的"schema retry"独立于闭环的 ``max_iters``——schema retry 处理
"格式错误",闭环 retry 处理"行为效果不佳"。
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from langchain.chat_models import init_chat_model
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from dimos.agents.skill_sequence_schema import (
    SkillSequence,
    SkillSequenceError,
    SkillSequenceJSONDecodeError,
    SkillSequenceSchemaError,
)
from dimos.utils.logging_config import setup_logger

if TYPE_CHECKING:
    from langchain_core.language_models.chat_models import BaseChatModel
    from langchain_core.messages.base import BaseMessage

logger = setup_logger()


DEFAULT_SYSTEM_PROMPT = """你是 DimOS 数据闭环里的"序列规划器"。
任务:看用户描述 + 真值轨迹摘要,输出一段 JSON 来描述接下来该按顺序执行哪些 skill。

输出格式必须严格符合以下 schema(不要包裹 markdown code fence,直接返回 JSON 对象):

{
  "task": "<对本段任务的一句话复述>",
  "rationale": "<可选:你的思路,留空可省略>",
  "steps": [
    {"skill": "<已注册 skill 名>", "args": {<kwargs>}, "expect_duration_s": <可选浮点>},
    ...
  ]
}

约束:
- steps 至少 1 个,且每个 step 的 skill 必须在 available_skills 列表中。
- args 必须是 JSON 对象;参数名与类型按 available_skills 文档严格对齐。
- 不要输出除 JSON 之外的任何文本(不要寒暄,不要解释,不要 markdown)。
"""


class SkillSequenceGenerationError(SkillSequenceError):
    """达到 ``max_schema_retries`` 仍未拿到合法 JSON 时抛出。

    Attributes:
        attempts: 已经发生过几次 LLM 调用(含第一次)。
        last_error: 最后一次 schema 校验失败时的内部异常,便于追溯。
    """

    def __init__(self, message: str, attempts: int, last_error: Exception | None):
        super().__init__(message)
        self.attempts: int = attempts
        self.last_error: Exception | None = last_error


class SkillSequenceGenerator:
    """让 LLM 看任务描述 + 真值摘要 + 上一轮反馈,生成一段 ``SkillSequence``。

    本类专为闭环系统设计——单轮内部能自动消化 schema 错误,但行为层面的
    "效果不好"由外层的 ``ClosedLoopCritic``(Unit 6)处理。

    典型用法::

        gen = SkillSequenceGenerator(
            model="gpt-4o",
            model_provider="openai",
            skill_registry={"GoToW": go_to_w, "rotate": rotate},
        )
        seq = gen.generate(
            task="向前走 3 米",
            truth_summary={"start": (0.0, 0.0), "end": (3.0, 0.0), "path_length": 3.0},
        )
    """

    def __init__(
        self,
        model: str = "gpt-4o",
        model_provider: str = "openai",
        skill_registry: dict[str, Any] | None = None,
        max_schema_retries: int = 3,
        system_prompt: str = DEFAULT_SYSTEM_PROMPT,
        llm: BaseChatModel | None = None,
    ):
        """构造一个生成器。

        Args:
            model: 模型名,默认 ``"gpt-4o"``。会传给 ``init_chat_model``。
            model_provider: 模型供应商,默认 ``"openai"``。
            skill_registry: skill 名 → callable 的注册表;同时作为给 LLM 的
                "可用 skill 列表"信息源。``None`` 等价于空字典,但实际很难出
                合法计划,主要给单元测试用。
            max_schema_retries: schema 错误时的最大重试次数(含首次调用)。
                必须 ``>= 1``;``1`` 表示"不自修,失败即抛"。
            system_prompt: 系统提示词,默认使用模块级 ``DEFAULT_SYSTEM_PROMPT``。
            llm: 已构造好的 ``BaseChatModel`` 实例。如果传入则跳过
                ``init_chat_model``,主要给测试做依赖注入。

        Note:
            未注入 ``llm`` 时会在此立刻调用 ``init_chat_model``——若环境变量
            (如 ``OPENAI_API_KEY``)缺失,会在构造阶段就抛错而非延迟到
            ``generate()``,便于尽早暴露配置问题。
        """
        if max_schema_retries < 1:
            raise ValueError(f"max_schema_retries 必须 >= 1,实际收到 {max_schema_retries}")

        self.model: str = model
        self.model_provider: str = model_provider
        self.skill_registry: dict[str, Any] = skill_registry or {}
        self.max_schema_retries: int = max_schema_retries
        self.system_prompt: str = system_prompt

        self._llm: BaseChatModel = (
            llm if llm is not None else init_chat_model(model=model, model_provider=model_provider)
        )

    def generate(
        self,
        task: str,
        truth_summary: dict[str, Any],
        feedback: list[dict[str, Any]] | None = None,
    ) -> SkillSequence:
        """调用 LLM,解析并校验返回的 JSON,失败则把错误塞回去重试。

        Args:
            task: 自然语言任务描述(例如"围绕办公楼走一圈")。
            truth_summary: 真值轨迹的简要数值摘要,例如
                ``{"start": (0,0), "end": (3,0), "path_length": 3.0, "duration_s": 5.0}``。
                内容会被 ``json.dumps`` 序列化后嵌入 prompt,所以值必须能 JSON
                序列化(tuple 会被转成 list)。
            feedback: 闭环上一轮的 critic feedback 列表;每个元素是任意可序列化
                的 dict。``None`` 表示首轮,无反馈。

        Returns:
            校验通过的 ``SkillSequence`` 实例。

        Raises:
            SkillSequenceGenerationError: 连续 ``max_schema_retries`` 次都未拿到合法 JSON。
        """
        user_prompt = self._build_user_prompt(task, truth_summary, feedback)
        messages: list[BaseMessage] = [
            SystemMessage(content=self.system_prompt),
            HumanMessage(content=user_prompt),
        ]

        last_error: Exception | None = None
        for attempt in range(1, self.max_schema_retries + 1):
            response = self._llm.invoke(messages)
            raw_text = _extract_text(response)
            logger.debug(
                "SkillSequenceGenerator received response.",
                attempt=attempt,
                raw_length=len(raw_text),
            )

            try:
                return SkillSequence.from_json(raw_text)
            except (SkillSequenceJSONDecodeError, SkillSequenceSchemaError) as e:
                last_error = e
                logger.warning(
                    "SkillSequence schema validation failed.",
                    attempt=attempt,
                    error=str(e),
                )
                if attempt >= self.max_schema_retries:
                    break
                # 把模型上轮回复 + 一条人工反馈追加到对话,让它自修。
                messages.append(AIMessage(content=raw_text))
                messages.append(HumanMessage(content=_format_retry_prompt(e)))

        raise SkillSequenceGenerationError(
            f"LLM 连续 {self.max_schema_retries} 次返回不合法的 SkillSequence JSON;"
            f"最后一次错误:{last_error}",
            attempts=self.max_schema_retries,
            last_error=last_error,
        )

    def _build_user_prompt(
        self,
        task: str,
        truth_summary: dict[str, Any],
        feedback: list[dict[str, Any]] | None,
    ) -> str:
        """组装本轮 LLM 的 user message。

        包含:任务描述、真值摘要、可用 skill 列表、可选的历史反馈。
        """
        skill_names = sorted(self.skill_registry.keys())
        sections = [
            f"# 任务\n{task}",
            f"# 真值轨迹摘要\n```json\n{_safe_json_dumps(truth_summary)}\n```",
            f"# 可用 skill 列表\n{json.dumps(skill_names, ensure_ascii=False)}",
        ]
        if feedback:
            sections.append(f"# 上一轮闭环反馈\n```json\n{_safe_json_dumps(feedback)}\n```")
        sections.append(
            "请直接返回一个 JSON 对象(不要包 markdown code fence),符合 SkillSequence schema。"
        )
        return "\n\n".join(sections)


def _extract_text(message: Any) -> str:
    """从 LangChain ``AIMessage`` 中抽出纯文本。

    LangChain 的 ``content`` 可能是 ``str``,也可能是富内容列表;本函数把它
    归一成单段字符串,方便交给 ``SkillSequence.from_json``。
    """
    content = getattr(message, "content", message)
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict) and item.get("type") == "text":
                parts.append(str(item.get("text", "")))
        return "".join(parts)
    return str(content)


def _format_retry_prompt(error: Exception) -> str:
    """把 schema 错误格式化成给 LLM 的修正请求文本。"""
    if isinstance(error, SkillSequenceSchemaError):
        errors_json = _safe_json_dumps(error.errors)
        return (
            "你上次返回的 JSON 没有通过 SkillSequence schema 校验,"
            "下面是 pydantic 报错明细:\n"
            f"```json\n{errors_json}\n```\n"
            "请只返回一个修正后的 JSON 对象,严格按 schema,不要其他文本。"
        )
    # SkillSequenceJSONDecodeError 或其他
    return (
        "你上次的返回不是合法 JSON,具体错误:\n"
        f"{error}\n"
        "请只返回一个合法的 JSON 对象,符合 SkillSequence schema,不要 markdown 包裹。"
    )


def _safe_json_dumps(obj: Any) -> str:
    """把 prompt 用对象序列化为 JSON,失败时退化成 ``str()``。

    确保 prompt 拼接不会因为 ``truth_summary`` 里夹了非 JSON 原生类型
    (例如 ``numpy.float64``、``tuple``)而崩溃。
    """
    try:
        return json.dumps(obj, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return str(obj)
