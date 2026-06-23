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

"""Filter Unitree G1 ``rt/audio_msg`` — user speech vs status JSON / TTS echo."""

from __future__ import annotations

import json
import re

_PUNCT_RE = re.compile(r"[\s，。！？、,.!?;:'\"“”‘’\-—…]+")
_CJK_RE = re.compile(r"[\u4e00-\u9fff]")
_ASCII_GREETINGS = frozenset({"hello", "hi", "hey", "bye", "goodbye"})

_USER_JSON_KEYS = frozenset({"text", "asr", "result", "content", "utterance", "query"})
_STATUS_JSON_KEYS = frozenset({"play_state", "state", "volume", "code", "index", "status"})

# ASR 拾到机器人/模板播报时不应再触发 greeter。
_ROBOT_ECHO_MARKERS = (
    "宇树",
    "unitree",
    "智能接待员",
    "daneel",
    "danee",
    "很高兴为您服务",
    "欢迎来访",
    "欢迎下次",
    "我是机器人",
    "这个我还不会",
    "您可以试试",
    "试试说你好",
    "挥手或跳",
    "分手会动",
    "风说会度",
    "挥手和推动",
    "祝你好",
    "王老师",
    "嗨向你挥挥手",
)


def normalize_asr_text(text: str) -> str:
    return _PUNCT_RE.sub("", text.strip().lower())


def is_unitree_status_payload(raw: str) -> bool:
    """True for ``{"play_state":1}`` style DDS payloads (not user speech)."""
    stripped = raw.strip()
    if not stripped.startswith("{"):
        return False
    try:
        parsed = json.loads(stripped)
    except json.JSONDecodeError:
        return False
    if not isinstance(parsed, dict) or not parsed:
        return False
    keys = set(parsed.keys())
    if keys & _USER_JSON_KEYS:
        return False
    if keys & _STATUS_JSON_KEYS:
        return True
    return True


def asr_texts_similar(a: str, b: str) -> bool:
    """True when ASR fragments are the same utterance (prefix/suffix overlap)."""
    na = normalize_asr_text(a)
    nb = normalize_asr_text(b)
    if not na or not nb:
        return False
    if na == nb:
        return True
    return na in nb or nb in na


def looks_like_robot_echo(text: str) -> bool:
    lowered = text.lower()
    return any(marker in lowered for marker in _ROBOT_ECHO_MARKERS)


def is_valid_user_utterance(text: str) -> bool:
    """Drop status JSON, punctuation-only, non-Chinese noise, and robot TTS echo."""
    cleaned = text.strip()
    if not cleaned:
        return False
    if is_unitree_status_payload(cleaned):
        return False
    norm = normalize_asr_text(cleaned)
    if len(norm) < 2:
        return False
    if not _CJK_RE.search(cleaned):
        letters = re.sub(r"[^a-z]", "", cleaned.lower())
        if letters not in _ASCII_GREETINGS:
            return False
    if looks_like_robot_echo(cleaned):
        return False
    return True


def should_publish_asr(
    text: str,
    *,
    last_text: str,
    last_publish_time: float,
    now: float,
    dedupe_window_s: float,
    min_text_len: int,
) -> bool:
    """Drop invalid, echo, or rapid duplicate ASR messages."""
    if not is_valid_user_utterance(text):
        return False
    cleaned = text.strip()
    if len(cleaned) < min_text_len:
        return False
    if last_text and (now - last_publish_time) < dedupe_window_s:
        if cleaned == last_text or asr_texts_similar(cleaned, last_text):
            return False
    return True


__all__ = [
    "asr_texts_similar",
    "is_unitree_status_payload",
    "is_valid_user_utterance",
    "looks_like_robot_echo",
    "normalize_asr_text",
    "should_publish_asr",
]
