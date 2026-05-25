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

from unittest.mock import MagicMock, patch

from huggingface_hub.errors import LocalEntryNotFoundError
import reactivex as rx

from dimos.agents.web_human_input import WebInput


def _bare_web_input() -> WebInput:
    web_input = WebInput.__new__(WebInput)
    web_input._web_interface = None
    web_input._thread = None
    web_input._human_transport = None
    web_input._stt_disabled = False
    return web_input


def test_start_whisper_cache_miss_disables_stt() -> None:
    web_input = _bare_web_input()
    mock_transport = MagicMock()
    mock_transport.publish = MagicMock()

    with patch.object(WebInput.__bases__[0], "start"):
        with patch("dimos.agents.web_human_input.global_config") as mock_cfg:
            mock_cfg.web_input_stt_enabled = True
            with patch("dimos.agents.web_human_input.pLCMTransport", return_value=mock_transport):
                with patch("dimos.agents.web_human_input.RobotWebInterface") as mock_web_cls:
                    mock_web = mock_web_cls.return_value
                    mock_web.query_stream = rx.subject.Subject()
                    with patch(
                        "dimos.stream.audio.stt.node_whisper.WhisperNode",
                        side_effect=LocalEntryNotFoundError("offline"),
                    ):
                        web_input.start()

    assert web_input._stt_disabled is True
    mock_web_cls.assert_called_once()
    assert mock_web_cls.call_args.kwargs["audio_subject"] is None


def test_start_stt_disabled_via_config() -> None:
    web_input = _bare_web_input()

    with patch.object(WebInput.__bases__[0], "start"):
        with patch("dimos.agents.web_human_input.global_config") as mock_cfg:
            mock_cfg.web_input_stt_enabled = False
            with patch("dimos.agents.web_human_input.pLCMTransport"):
                with patch("dimos.agents.web_human_input.RobotWebInterface") as mock_web_cls:
                    mock_web_cls.return_value.query_stream = rx.subject.Subject()
                    with patch("dimos.stream.audio.stt.node_whisper.WhisperNode") as mock_whisper:
                        web_input.start()

    assert web_input._stt_disabled is True
    mock_whisper.assert_not_called()
    assert mock_web_cls.call_args.kwargs["audio_subject"] is None


def test_start_whisper_generic_failure_disables_stt() -> None:
    web_input = _bare_web_input()

    with patch.object(WebInput.__bases__[0], "start"):
        with patch("dimos.agents.web_human_input.global_config") as mock_cfg:
            mock_cfg.web_input_stt_enabled = True
            with patch("dimos.agents.web_human_input.pLCMTransport"):
                with patch("dimos.agents.web_human_input.RobotWebInterface") as mock_web_cls:
                    mock_web_cls.return_value.query_stream = rx.subject.Subject()
                    with patch(
                        "dimos.stream.audio.stt.node_whisper.WhisperNode",
                        side_effect=RuntimeError("boom"),
                    ):
                        web_input.start()

    assert web_input._stt_disabled is True
    assert mock_web_cls.call_args.kwargs["audio_subject"] is None
