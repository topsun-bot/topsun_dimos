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

import base64
import hashlib
import io
import json
import os
import tempfile
import threading
import time
from typing import Any

import numpy as np
from openai import OpenAI
from reactivex import Subject
import soundfile as sf  # type: ignore[import-untyped]
from unitree_webrtc_connect.constants import RTC_TOPIC

from dimos.agents.annotation import skill
from dimos.constants import DEFAULT_THREAD_JOIN_TIMEOUT
from dimos.core.core import rpc
from dimos.core.module import Module
from dimos.robot.unitree.go2.connection_spec import GO2ConnectionSpec
from dimos.stream.audio.node_output import SounddeviceAudioOutput
from dimos.stream.audio.tts.node_openai import OpenAITTSNode, Voice
from dimos.utils.logging_config import setup_logger

logger = setup_logger()

_AUDIO_API = {
    "GET_AUDIO_LIST": 1001,
    "SELECT_START_PLAY": 1002,
    "PAUSE": 1003,
    "SET_PLAY_MODE": 1007,
    "UPLOAD_AUDIO_FILE": 2001,
}


class SpeakSkill(Module):
    _tts_node: OpenAITTSNode | None = None
    _audio_output: SounddeviceAudioOutput | None = None
    _audio_lock: threading.Lock = threading.Lock()
    _bg_threads: list[threading.Thread] = []
    _bg_threads_lock: threading.Lock = threading.Lock()
    _connection: GO2ConnectionSpec | None = None
    _openai_client: OpenAI | None = None
    _audio_cache: dict[str, str] = {}  # text -> robot audio uuid

    @rpc
    def start(self) -> None:
        super().start()
        has_robot = self._connection is not None
        if has_robot:
            logger.info("SpeakSkill: GO2 connection detected, audio routes to robot speaker")
            self._openai_client = OpenAI()
        else:
            logger.info("SpeakSkill: No GO2 connection, audio routes to local speaker")
            self._tts_node = OpenAITTSNode(speed=1.2, voice=Voice.ONYX)
            self._audio_output = SounddeviceAudioOutput(sample_rate=24000)
            self._audio_output.consume_audio(self._tts_node.emit_audio())

    @rpc
    def stop(self) -> None:
        with self._bg_threads_lock:
            threads = list(self._bg_threads)
        for t in threads:
            t.join(timeout=DEFAULT_THREAD_JOIN_TIMEOUT)
        if self._tts_node:
            self._tts_node.dispose()
            self._tts_node = None
        if self._audio_output:
            self._audio_output.stop()
            self._audio_output = None
        super().stop()

    @skill
    def speak(self, text: str, blocking: bool = True) -> str:
        """Speak text out loud through the robot's speakers.

        USE THIS TOOL AS OFTEN AS NEEDED. People can't normally see what you say in text, but can hear what you speak.

        Try to be as concise as possible. Remember that speaking takes time, so get to the point quickly.

        Example usage:

            speak("Hello, I am your robot assistant.")
        """
        if self._connection is not None:
            return self._speak_via_robot(text)

        if self._tts_node is None:
            return "Error: TTS not initialized"

        if not blocking:
            thread = threading.Thread(
                target=self._speak_bg, args=(text,), daemon=True, name="SpeakSkill-bg"
            )
            with self._bg_threads_lock:
                self._bg_threads.append(thread)
            thread.start()
            return f"Speaking (non-blocking): {text}"

        return self._speak_blocking(text)

    @skill
    def speak_cached(self, text: str) -> str:
        """Speak pre-cached text instantly through the robot's speakers.

        The first call for a given text will generate and upload the audio
        (takes ~2-3s). Subsequent calls with the same text play instantly
        (~0.2s), skipping TTS generation and upload entirely.

        Use this for fixed alert phrases that need minimal latency.

        Args:
            text: The text to speak. Must match exactly for cache hits.
        """
        if self._connection is None:
            return self.speak(text)

        with self._audio_lock:
            cached_uuid = self._audio_cache.get(text)
            if cached_uuid:
                logger.info(f"SpeakSkill(cached hit): '{text}' -> {cached_uuid}")
                self._play_audio(cached_uuid)
                return f"Spoke (cached): {text}"

            logger.info(f"SpeakSkill(cache miss, generating): '{text}'")
            uuid = self._generate_and_upload(text)
            if uuid:
                self._audio_cache[text] = uuid
                self._play_audio(uuid)
                return f"Spoke (newly cached): {text}"
            return f"Error: failed to generate audio for '{text}'"

    @rpc
    def precache_audio(self, texts: list[str]) -> str:
        """Pre-generate and upload audio for given texts so speak_cached is instant."""
        if self._connection is None:
            return "No robot connection, precache not needed"

        with self._audio_lock:
            for text in texts:
                if text not in self._audio_cache:
                    logger.info(f"Precaching audio: '{text}'")
                    uuid = self._generate_and_upload(text)
                    if uuid:
                        self._audio_cache[text] = uuid
        return f"Precached {len(texts)} audio clips"

    def _generate_and_upload(self, text: str) -> str | None:
        """Generate TTS audio, convert to WAV, upload to robot. Returns uuid."""
        try:
            if self._openai_client is None:
                self._openai_client = OpenAI()

            response = self._openai_client.audio.speech.create(
                model="tts-1", voice="onyx", input=text, speed=1.2,
                response_format="mp3",
            )

            with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as tmp:
                tmp.write(response.content)
                tmp_path = tmp.name

            try:
                audio_array, sample_rate = sf.read(tmp_path)
            finally:
                os.unlink(tmp_path)

            if audio_array.ndim > 1:
                audio_array = np.mean(audio_array, axis=1)

            target_sr = 22050
            if sample_rate != target_sr:
                old_len = len(audio_array)
                new_len = int(old_len * target_sr / sample_rate)
                audio_array = np.interp(
                    np.linspace(0, old_len - 1, new_len), np.arange(old_len), audio_array
                )
                sample_rate = target_sr

            audio_array = audio_array / max(np.max(np.abs(audio_array)), 1e-6)

            buf = io.BytesIO()
            sf.write(buf, audio_array, sample_rate, format="WAV", subtype="PCM_16")
            wav_data = buf.getvalue()

            filename = f"speak_{hashlib.md5(text.encode()).hexdigest()[:8]}"
            return self._upload_audio(wav_data, filename)
        except Exception as e:
            logger.error(f"Failed to generate/upload audio: {e}")
            return None

    # -- Robot speaker path (via WebRTC AudioHub) --

    def _speak_via_robot(self, text: str) -> str:
        with self._audio_lock:
            try:
                display_text = text[:50] + "..." if len(text) > 50 else text
                logger.info(f"SpeakSkill(robot): '{display_text}'")

                if self._openai_client is None:
                    self._openai_client = OpenAI()

                response = self._openai_client.audio.speech.create(
                    model="tts-1", voice="onyx", input=text, speed=1.2,
                    response_format="mp3",
                )

                with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as tmp:
                    tmp.write(response.content)
                    tmp_path = tmp.name

                try:
                    audio_array, sample_rate = sf.read(tmp_path)
                finally:
                    os.unlink(tmp_path)

                if audio_array.ndim > 1:
                    audio_array = np.mean(audio_array, axis=1)

                target_sr = 22050
                if sample_rate != target_sr:
                    old_len = len(audio_array)
                    new_len = int(old_len * target_sr / sample_rate)
                    audio_array = np.interp(
                        np.linspace(0, old_len - 1, new_len), np.arange(old_len), audio_array
                    )
                    sample_rate = target_sr

                audio_array = audio_array / max(np.max(np.abs(audio_array)), 1e-6)

                buf = io.BytesIO()
                sf.write(buf, audio_array, sample_rate, format="WAV", subtype="PCM_16")
                wav_data = buf.getvalue()

                duration = len(audio_array) / sample_rate
                logger.info(f"Audio: {len(wav_data) / 1024:.1f}KB, {duration:.1f}s")

                filename = f"speak_{int(time.time() * 1000)}"
                uuid = self._upload_audio(wav_data, filename)
                self._play_audio(uuid)

                return f"Spoke: {text}"

            except Exception as e:
                logger.error(f"SpeakSkill(robot) error: {e}")
                return f"Error speaking: {e!s}"

    def _audio_hub_request(self, api_id: int, parameter: dict[str, Any] | None = None) -> Any:
        assert self._connection is not None
        data = {"api_id": api_id, "parameter": json.dumps(parameter) if parameter else "{}"}
        return self._connection.publish_request(RTC_TOPIC["AUDIO_HUB_REQ"], data)

    def _upload_audio(self, wav_data: bytes, filename: str) -> str:
        file_md5 = hashlib.md5(wav_data).hexdigest()
        b64 = base64.b64encode(wav_data).decode()
        chunk_size = 61440
        chunks = [b64[i : i + chunk_size] for i in range(0, len(b64), chunk_size)]

        for i, chunk in enumerate(chunks, 1):
            self._audio_hub_request(_AUDIO_API["UPLOAD_AUDIO_FILE"], {
                "file_name": filename,
                "file_type": "wav",
                "file_size": len(wav_data),
                "current_block_index": i,
                "total_block_number": len(chunks),
                "block_content": chunk,
                "current_block_size": len(chunk),
                "file_md5": file_md5,
                "create_time": int(time.time() * 1000),
            })

        resp = self._audio_hub_request(_AUDIO_API["GET_AUDIO_LIST"], {})
        if resp and "data" in resp:
            data_str = resp.get("data", {}).get("data", "{}")
            for audio in json.loads(data_str).get("audio_list", []):
                if audio.get("CUSTOM_NAME") == filename:
                    return audio.get("UNIQUE_ID", filename)
        return filename

    def _play_audio(self, uuid: str) -> None:
        self._audio_hub_request(_AUDIO_API["SET_PLAY_MODE"], {"play_mode": "no_cycle"})
        time.sleep(0.1)
        self._audio_hub_request(_AUDIO_API["SELECT_START_PLAY"], {"unique_id": uuid})

    # -- Local speaker path (fallback) --

    def _speak_bg(self, text: str) -> None:
        try:
            self._speak_blocking(text)
        finally:
            with self._bg_threads_lock:
                self._bg_threads = [
                    t for t in self._bg_threads if t is not threading.current_thread()
                ]

    def _speak_blocking(self, text: str) -> str:
        with self._audio_lock:
            if self._tts_node is None:
                return "Error: TTS not initialized"

            text_subject: Subject[str] = Subject()
            audio_complete = threading.Event()
            self._tts_node.consume_text(text_subject)

            def set_as_complete(_t: str) -> None:
                audio_complete.set()

            def set_as_complete_e(_e: Exception) -> None:
                audio_complete.set()

            subscription = self._tts_node.emit_text().subscribe(
                on_next=set_as_complete,
                on_error=set_as_complete_e,
            )

            text_subject.on_next(text)
            text_subject.on_completed()

            timeout = max(5, len(text) * 0.1)

            if not audio_complete.wait(timeout=timeout):
                logger.warning(f"TTS timeout reached for: {text}")
                subscription.dispose()
                return f"Warning: TTS timeout while speaking: {text}"
            else:
                time.sleep(0.3)

            subscription.dispose()

            return f"Spoke: {text}"
