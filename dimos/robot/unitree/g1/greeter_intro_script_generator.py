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

"""Generate greeter landmark ``intro_script`` text at tag time via an LLM."""

from __future__ import annotations

from dataclasses import dataclass
import os
import re
from typing import Any, Protocol

import requests

from dimos.utils.logging_config import setup_logger

logger = setup_logger()

_DASHSCOPE_COMPAT_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
_QUOTE_WRAP_RE = re.compile(r'^[\s"“”\'「」『』]+|[\s"“”\'「」『』]+$')
_MARKDOWN_FENCE_RE = re.compile(r"```.*?```", re.DOTALL)


class ChatCompleter(Protocol):
    def complete(self, *, model: str, system: str, user: str, timeout_sec: float) -> str: ...


@dataclass(frozen=True)
class LlmClientConfig:
    api_key: str
    base_url: str
    model: str


def fallback_intro_script(name: str, template: str) -> str:
    return template.format(name=name.strip())


def sanitize_intro_script(text: str, *, max_chars: int) -> str:
    """Normalize LLM output to a short spoken line."""
    cleaned = _MARKDOWN_FENCE_RE.sub("", text.strip())
    cleaned = cleaned.replace("\n", " ").strip()
    cleaned = _QUOTE_WRAP_RE.sub("", cleaned)
    cleaned = re.sub(r"\s{2,}", " ", cleaned)
    if len(cleaned) > max_chars:
        cleaned = cleaned[:max_chars].rstrip("，。！？、,.!? ") + "。"
    return cleaned


def build_intro_script_prompt(name: str) -> tuple[str, str]:
    system = (
        "你是展厅导览讲解员。根据地点名称写一段到站后播报的中文讲解词。"
        "要求：1~2句、口语化、礼貌；只写讲解正文，不要标题、列表或引号；"
        "不要编造具体公司名、人名、数字或未给出的设施细节；"
        "若名称较泛，用通用欢迎/指引措辞。"
    )
    user = f"地点名称：{name.strip()}\n请写讲解词："
    return system, user


def default_intro_model(base_url: str) -> str:
    lowered = base_url.lower()
    if "deepseek" in lowered:
        return "deepseek-chat"
    if "dashscope" in lowered:
        return "qwen-plus"
    return "gpt-4o-mini"


def resolve_llm_client_config(model_override: str = "") -> LlmClientConfig | None:
    """Pick OpenAI-compatible endpoint from env (DeepSeek or DashScope)."""
    base_url = os.getenv("OPENAI_BASE_URL", "").strip()
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key:
        dashscope_key = os.getenv("DASHSCOPE_API_KEY", "").strip()
        if dashscope_key:
            api_key = dashscope_key
            base_url = _DASHSCOPE_COMPAT_BASE_URL
    if not api_key:
        return None
    if not base_url:
        base_url = "https://api.openai.com/v1"
    model = model_override.strip() or os.getenv("DIMOS_GREETER_INTRO_MODEL", "").strip()
    if not model:
        model = default_intro_model(base_url)
    return LlmClientConfig(api_key=api_key, base_url=base_url.rstrip("/"), model=model)


class RequestsChatCompleter:
    def complete(self, *, model: str, system: str, user: str, timeout_sec: float) -> str:
        cfg = resolve_llm_client_config(model)
        if cfg is None:
            raise RuntimeError("未配置 LLM API Key（需要 OPENAI_API_KEY 或 DASHSCOPE_API_KEY）")
        url = f"{cfg.base_url}/chat/completions"
        payload: dict[str, Any] = {
            "model": cfg.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": 0,
            "max_tokens": 120,
        }
        response = requests.post(
            url,
            headers={
                "Authorization": f"Bearer {cfg.api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=timeout_sec,
        )
        response.raise_for_status()
        data = response.json()
        content = data["choices"][0]["message"]["content"]
        if not isinstance(content, str) or not content.strip():
            raise RuntimeError("LLM 返回空讲解词")
        return content


def generate_intro_script_for_landmark(
    name: str,
    *,
    model: str = "",
    timeout_sec: float = 15.0,
    max_chars: int = 120,
    completer: ChatCompleter | None = None,
) -> str:
    system, user = build_intro_script_prompt(name)
    backend = completer or RequestsChatCompleter()
    raw = backend.complete(model=model, system=system, user=user, timeout_sec=timeout_sec)
    script = sanitize_intro_script(raw, max_chars=max_chars)
    if not script:
        raise RuntimeError("讲解词清洗后为空")
    return script


def resolve_intro_script_for_tag(
    name: str,
    intro_script: str,
    *,
    llm_enabled: bool,
    fallback_template: str,
    model: str,
    timeout_sec: float,
    max_chars: int,
    completer: ChatCompleter | None = None,
) -> tuple[str, str]:
    """Return ``(script, source)`` where source is ``provided`` | ``llm`` | ``fallback``."""
    provided = intro_script.strip()
    if provided:
        return provided, "provided"
    if llm_enabled:
        try:
            generated = generate_intro_script_for_landmark(
                name,
                model=model,
                timeout_sec=timeout_sec,
                max_chars=max_chars,
                completer=completer,
            )
            logger.info("标点 LLM 生成讲解词(%s): %s", name, generated[:80])
            return generated, "llm"
        except Exception:
            logger.exception("标点 LLM 生成讲解词失败,使用模板: %s", name)
    return fallback_intro_script(name, fallback_template), "fallback"


__all__ = [
    "ChatCompleter",
    "LlmClientConfig",
    "RequestsChatCompleter",
    "build_intro_script_prompt",
    "default_intro_model",
    "fallback_intro_script",
    "generate_intro_script_for_landmark",
    "resolve_intro_script_for_tag",
    "resolve_llm_client_config",
    "sanitize_intro_script",
]
