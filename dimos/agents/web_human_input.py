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

from threading import Thread

import reactivex as rx
import reactivex.operators as ops

from dimos.constants import DEFAULT_THREAD_JOIN_TIMEOUT
from dimos.core.core import rpc
from dimos.core.global_config import global_config
from dimos.core.module import Module
from dimos.core.transport import pLCMTransport
from dimos.stream.audio.base import AudioEvent
from dimos.stream.audio.node_normalizer import AudioNormalizer
from dimos.utils.logging_config import setup_logger
from dimos.web.robot_web_interface import RobotWebInterface

logger = setup_logger()

try:
    from huggingface_hub.errors import LocalEntryNotFoundError
except ImportError:
    LocalEntryNotFoundError = type(  # type: ignore[misc,assignment]
        "LocalEntryNotFoundError",
        (Exception,),
        {},
    )


class WebInput(Module):
    _web_interface: RobotWebInterface | None = None
    _thread: Thread | None = None
    _human_transport: pLCMTransport[str] | None = None
    _stt_disabled: bool = False

    @rpc
    def start(self) -> None:
        super().start()

        self._human_transport = pLCMTransport("/human_input")

        audio_subject: rx.subject.Subject[AudioEvent] | None = None

        if global_config.web_input_stt_enabled:
            audio_subject = self._try_init_stt_pipeline()
        else:
            self._stt_disabled = True
            logger.warning(
                "WebInput STT disabled (web_input_stt_enabled=False). "
                "Web UI text input only; use dimos agent-send or MCP."
            )

        self._web_interface = RobotWebInterface(
            port=5555,
            text_streams={"agent_responses": rx.subject.Subject()},
            audio_subject=audio_subject,
        )

        # Direct text from web interface
        unsub = self._web_interface.query_stream.subscribe(self._human_transport.publish)
        self.register_disposable(unsub)

        self._thread = Thread(target=self._web_interface.run, daemon=True)
        self._thread.start()

        logger.info("Web interface started at http://localhost:5555")

    def _try_init_stt_pipeline(self) -> rx.subject.Subject[AudioEvent] | None:
        """Wire browser audio → Whisper STT. Returns audio subject or None on failure."""
        from dimos.stream.audio.stt.node_whisper import WhisperNode

        try:
            audio_subject: rx.subject.Subject[AudioEvent] = rx.subject.Subject()
            normalizer = AudioNormalizer()
            stt_node = WhisperNode()

            normalizer.consume_audio(audio_subject.pipe(ops.share()))
            stt_node.consume_audio(normalizer.emit_audio())

            unsub = stt_node.emit_text().subscribe(self._human_transport.publish)
            self.register_disposable(unsub)
            return audio_subject
        except LocalEntryNotFoundError as exc:
            self._stt_disabled = True
            logger.warning(
                "WebInput STT disabled: Whisper model not in local cache and "
                "HuggingFace download unavailable (%s). Web UI text input only.",
                exc,
            )
            return None
        except Exception as exc:
            self._stt_disabled = True
            logger.warning(
                "WebInput STT disabled: Whisper init failed (%s). Web UI text input only.",
                exc,
            )
            return None

    @rpc
    def stop(self) -> None:
        if self._web_interface:
            self._web_interface.shutdown()
        if self._thread:
            self._thread.join(timeout=DEFAULT_THREAD_JOIN_TIMEOUT)
        if self._human_transport:
            self._human_transport.lcm.stop()
        super().stop()
