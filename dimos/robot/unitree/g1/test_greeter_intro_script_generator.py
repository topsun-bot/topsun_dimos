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

import pytest

from dimos.robot.unitree.g1.greeter_intro_script_generator import (
    build_intro_script_prompt,
    default_intro_model,
    fallback_intro_script,
    resolve_intro_script_for_tag,
    sanitize_intro_script,
)


class _FakeCompleter:
    def __init__(self, response: str) -> None:
        self._response = response
        self.calls: list[tuple[str, str]] = []

    def complete(self, *, model: str, system: str, user: str, timeout_sec: float) -> str:
        self.calls.append((system, user))
        return self._response


def test_sanitize_intro_script_strips_quotes_and_newlines() -> None:
    raw = '  "这里是前台，\n请在此登记来访信息。"  '
    assert sanitize_intro_script(raw, max_chars=120) == "这里是前台， 请在此登记来访信息。"


def test_fallback_intro_script() -> None:
    assert fallback_intro_script("前台", "这里是{name}，欢迎参观。") == "这里是前台，欢迎参观。"


def test_default_intro_model() -> None:
    assert default_intro_model("https://api.deepseek.com") == "deepseek-chat"
    assert default_intro_model("https://dashscope.aliyuncs.com/compatible-mode/v1") == "qwen-plus"


def test_resolve_intro_script_for_tag_uses_provided() -> None:
    script, source = resolve_intro_script_for_tag(
        "前台",
        " 手写讲解词 ",
        llm_enabled=True,
        fallback_template="这里是{name}。",
        model="",
        timeout_sec=1.0,
        max_chars=120,
    )
    assert script == "手写讲解词"
    assert source == "provided"


def test_resolve_intro_script_for_tag_llm() -> None:
    fake = _FakeCompleter("欢迎来到前台，请在此登记。")
    script, source = resolve_intro_script_for_tag(
        "前台",
        "",
        llm_enabled=True,
        fallback_template="这里是{name}。",
        model="test-model",
        timeout_sec=1.0,
        max_chars=120,
        completer=fake,
    )
    assert source == "llm"
    assert "前台" in script
    assert len(fake.calls) == 1
    _system, user = fake.calls[0]
    assert "前台" in user


def test_resolve_intro_script_for_tag_fallback_on_llm_error() -> None:
    class _FailCompleter:
        def complete(self, *, model: str, system: str, user: str, timeout_sec: float) -> str:
            raise RuntimeError("network down")

    script, source = resolve_intro_script_for_tag(
        "前台",
        "",
        llm_enabled=True,
        fallback_template="这里是{name}，欢迎参观。",
        model="",
        timeout_sec=1.0,
        max_chars=120,
        completer=_FailCompleter(),
    )
    assert source == "fallback"
    assert script == "这里是前台，欢迎参观。"


def test_resolve_intro_script_for_tag_fallback_when_llm_disabled() -> None:
    script, source = resolve_intro_script_for_tag(
        "前台",
        "",
        llm_enabled=False,
        fallback_template="我们到了{name}。",
        model="",
        timeout_sec=1.0,
        max_chars=120,
    )
    assert source == "fallback"
    assert script == "我们到了前台。"


def test_build_intro_script_prompt_contains_name() -> None:
    system, user = build_intro_script_prompt("展厅A")
    assert "展厅A" in user
    assert "讲解" in system
