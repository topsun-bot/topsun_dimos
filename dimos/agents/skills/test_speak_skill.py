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

from unittest.mock import patch

from dimos.agents.skills.speak_skill import SpeakSkill


def test_start_without_openai_api_key_skips_tts() -> None:
    skill = SpeakSkill.__new__(SpeakSkill)
    skill._tts_node = None
    skill._audio_output = None
    skill._bg_threads = []
    skill._bg_threads_lock = __import__("threading").Lock()
    skill._audio_lock = __import__("threading").Lock()

    with patch.dict("os.environ", {}, clear=True):
        with patch.object(SpeakSkill.__bases__[0], "start"):
            skill.start()

    assert skill._tts_node is None
    assert skill._audio_output is None
    assert skill.speak("hello") == "TTS disabled (OPENAI_API_KEY not set)"
