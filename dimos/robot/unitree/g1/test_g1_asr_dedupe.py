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

from dimos.robot.unitree.g1.g1_asr_dedupe import (
    asr_texts_similar,
    is_unitree_status_payload,
    is_valid_user_utterance,
    looks_like_robot_echo,
    should_publish_asr,
)
from dimos.robot.unitree.g1.g1_builtin_voice_input import parse_g1_asr_payload


def test_parse_g1_asr_payload_plain() -> None:
    assert parse_g1_asr_payload("  你好  ") == "你好"


def test_parse_g1_asr_payload_json_text() -> None:
    assert parse_g1_asr_payload('{"text":"带我去前台"}') == "带我去前台"


def test_parse_g1_asr_payload_play_state_empty() -> None:
    assert parse_g1_asr_payload('{"play_state":1}') == ""
    assert parse_g1_asr_payload('{"play_state":0}') == ""


def test_is_unitree_status_payload() -> None:
    assert is_unitree_status_payload('{"play_state":1}')
    assert not is_unitree_status_payload('{"text":"你好"}')


def test_should_publish_asr_dedupes_prefix() -> None:
    assert not should_publish_asr(
        "你叫什么名字",
        last_text="你叫什么",
        last_publish_time=10.0,
        now=10.5,
        dedupe_window_s=12.0,
        min_text_len=2,
    )
    assert asr_texts_similar("你叫什么", "你叫什么名字")


def test_invalid_utterances() -> None:
    assert not is_valid_user_utterance("お？")
    assert not is_valid_user_utterance("。")
    assert not is_valid_user_utterance('{"play_state":0}')
    assert not is_valid_user_utterance("抱歉，这个我还不会，您可以试试说你好")
    assert looks_like_robot_echo("对，这个我还不会，您可以试试说您好")


def test_valid_greetings() -> None:
    assert is_valid_user_utterance("你好")
    assert is_valid_user_utterance("你是谁")
    assert is_valid_user_utterance("hello")
