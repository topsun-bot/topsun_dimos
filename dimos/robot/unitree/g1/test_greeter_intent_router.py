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

from dimos.robot.unitree.g1.greeter_intent_router import (
    DANCE_SHORTCUT,
    GreeterIntentRouterConfig,
    collect_greeter_prewarm_texts,
    is_location_query,
    match_faq_answer,
    match_gesture_shortcut,
    match_greeter_intent,
    match_unmapped_location_reply,
    normalize_utterance,
)

_CFG = GreeterIntentRouterConfig()


def _match(text: str) -> str | None:
    return match_greeter_intent(text, _CFG)


def test_normalize_strips_punctuation() -> None:
    assert normalize_utterance("  你好！ ") == "你好"
    assert normalize_utterance("Hello.") == "hello"


def test_shortcut_hello() -> None:
    assert _match("你好") == "welcome"
    assert _match("您好") == "welcome"
    assert _match("hello") == "welcome"
    assert _match("你好呀") == "welcome"
    assert _match("喂你好") == "welcome"
    assert _match("哈喽啊") == "welcome"


def test_shortcut_goodbye() -> None:
    assert _match("再见") == "farewell"
    assert _match("拜拜") == "farewell"


def test_long_question_not_shortcut() -> None:
    assert _match("你好请问厕所在哪里") is None
    assert _match("今天天气怎么样") is None


def test_faq_identity() -> None:
    answer = match_faq_answer("你是谁", _CFG)
    assert answer is not None
    assert "Daneel" in answer


def test_faq_location_not_shortcut() -> None:
    assert match_faq_answer("厕所在哪", _CFG) is None
    assert match_faq_answer("前台在哪", _CFG) is None


def test_unmapped_location_reply() -> None:
    reply = match_unmapped_location_reply("厕所在哪", _CFG)
    assert reply is not None
    assert "还没录入地图" in reply
    assert match_unmapped_location_reply("怎么去前台", _CFG) == reply


def test_location_query_detection() -> None:
    assert is_location_query("厕所在哪", 48) is True
    assert is_location_query("厕所有纸吗", 48) is False


def test_unmapped_location_skipped_when_landmarks_on() -> None:
    cfg = GreeterIntentRouterConfig(location_landmarks_available=True)
    assert match_unmapped_location_reply("厕所在哪", cfg) is None


def test_faq_no_match() -> None:
    assert match_faq_answer("今天天气怎么样", _CFG) is None


def test_gesture_wave_with_speak() -> None:
    assert match_gesture_shortcut("挥挥手", _CFG) == ("HighWave", "嗨，向你挥挥手！")
    assert match_gesture_shortcut("挥挥手好吗", _CFG) == ("HighWave", "嗨，向你挥挥手！")


def test_gesture_heart_with_speak() -> None:
    assert match_gesture_shortcut("比心", _CFG) == ("ArmHeart", "比心哦！")


def test_dance_shortcut() -> None:
    assert match_gesture_shortcut("跳个舞", _CFG) == (DANCE_SHORTCUT, "来啦，献舞一支！")
    assert match_gesture_shortcut("来段表演", _CFG) == (DANCE_SHORTCUT, "来啦，献舞一支！")


def test_gesture_not_complex_question() -> None:
    assert match_gesture_shortcut("挥手然后带我去厕所", _CFG) is None
    assert match_gesture_shortcut("今天天气怎么样", _CFG) is None


def test_llm_disabled_by_default() -> None:
    assert _CFG.llm_enabled is False
    assert "还不会" in _CFG.llm_fallback_template


def test_prewarm_texts_include_templates() -> None:
    texts = collect_greeter_prewarm_texts(_CFG)
    assert _CFG.welcome_template in texts
    assert _CFG.llm_fallback_template in texts
    assert "嗨，向你挥挥手！" in texts
