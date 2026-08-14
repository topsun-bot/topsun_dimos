# Copyright 2026 Dimensional Inc.
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

import builtins
from unittest.mock import MagicMock

import pytest

from dimos.agents.web_human_input import WebInput


def test_web_input_keeps_text_interface_when_whisper_is_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    web_interface = MagicMock()
    web_interface.query_stream.subscribe.return_value = MagicMock()
    monkeypatch.setattr(
        "dimos.agents.web_human_input.RobotWebInterface",
        MagicMock(return_value=web_interface),
    )
    transport = MagicMock()
    monkeypatch.setattr("dimos.agents.web_human_input.make_transport", lambda _topic: transport)
    real_import = builtins.__import__

    def import_without_whisper(name: str, *args: object, **kwargs: object) -> object:
        if name == "dimos.stream.audio.stt.node_whisper":
            raise ImportError("no whisper backend")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", import_without_whisper)

    module = WebInput()
    module.start()
    module.stop()

    web_interface.query_stream.subscribe.assert_called_once_with(transport.publish)
    web_interface.run.assert_called_once_with()
    web_interface.shutdown.assert_called_once_with()
