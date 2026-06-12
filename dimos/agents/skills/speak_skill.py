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
import threading
import time
from typing import Any

import numpy as np
from reactivex import Subject

from dimos.agents.annotation import skill
from dimos.constants import DEFAULT_THREAD_JOIN_TIMEOUT
from dimos.core.core import rpc
from dimos.core.module import Module
from dimos.robot.unitree.go2.connection_spec import GO2ConnectionSpec
from dimos.stream.audio.node_output import SounddeviceAudioOutput
from dimos.stream.audio.tts.node_dashscope import DashScopeTTSNode
from dimos.stream.audio.tts.node_openai import OpenAITTSNode, Voice
from dimos.utils.logging_config import setup_logger

logger = setup_logger()

_SPEECH_SAMPLE_RATE = 24000


class SpeakSkill(Module):
    _tts_node: OpenAITTSNode | DashScopeTTSNode | None = None
    _audio_output: SounddeviceAudioOutput | None = None
    _connection: GO2ConnectionSpec | None = None
    _audio_lock: threading.Lock = threading.Lock()
    _bg_threads: list[threading.Thread] = []
    _bg_threads_lock: threading.Lock = threading.Lock()
    _speech_cache: dict[str, np.ndarray]

    @rpc
    def start(self) -> None:
        super().start()
        self._speech_cache = {}
        dashscope_key = os.environ.get("DASHSCOPE_API_KEY", "")
        if dashscope_key:
            logger.info("SpeakSkill: 使用 DashScope CosyVoice TTS (DASHSCOPE_API_KEY 已配置)")
            self._tts_node = DashScopeTTSNode(api_key=dashscope_key)
        else:
            logger.info("SpeakSkill: 使用 OpenAI TTS")
            self._tts_node = OpenAITTSNode(speed=1.2, voice=Voice.ONYX)
        self._audio_output = SounddeviceAudioOutput(sample_rate=24000)
        self._audio_output.consume_audio(self._tts_node.emit_audio())

    @rpc
    def prewarm_texts(self, texts: list[str]) -> None:
        """后台预合成固定台词, 后续 speak 直接播缓存(跳过云端 TTS 延迟)."""
        unique = list(dict.fromkeys(t.strip() for t in texts if t.strip()))

        def _warm() -> None:
            for text in unique:
                if text in self._speech_cache:
                    continue
                try:
                    self._speech_cache[text] = self._synthesize_to_array(text)
                    logger.info("SpeakSkill 预缓存完成: %s", text[:40])
                except Exception:
                    logger.exception("SpeakSkill 预缓存失败: %s", text[:40])
            logger.info("SpeakSkill 预缓存结束 (%d/%d 条)", len(self._speech_cache), len(unique))

        threading.Thread(target=_warm, daemon=True, name="SpeakSkill-prewarm").start()

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

    def _speak_bg(self, text: str) -> None:
        try:
            self._speak_blocking(text)
        finally:
            # Remove this thread from the list of background threads when done
            with self._bg_threads_lock:
                self._bg_threads = [
                    t for t in self._bg_threads if t is not threading.current_thread()
                ]

    def _speak_blocking(self, text: str) -> str:
        t0 = time.monotonic()
        # Use lock to prevent simultaneous speech
        with self._audio_lock:
            if self._tts_node is None:
                return "Error: TTS not initialized"

            go2_result = self._speak_on_go2_if_available(text)
            if go2_result is not None:
                return go2_result

            cached = self._speech_cache.get(text)
            if cached is not None:
                return self._play_cached_audio(cached, text, t0)

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
                # Small delay to ensure buffers flush
                time.sleep(0.3)

            subscription.dispose()

            logger.info("SpeakSkill 完成,耗时 %.1fs, text=%s", time.monotonic() - t0, text[:40])
            return f"Spoke: {text}"

    def _play_cached_audio(self, audio: np.ndarray, text: str, t0: float) -> str:
        import sounddevice as sd  # type: ignore[import-untyped]

        sd.play(audio, samplerate=_SPEECH_SAMPLE_RATE)
        # Block until playback actually finishes — sd.play() is non-blocking, so a
        # fixed sleep would return mid-utterance and let a follow-up gesture/speak
        # overlap the audio (and breaks the _audio_lock's serialization guarantee).
        sd.wait()
        logger.info("SpeakSkill 缓存播放,耗时 %.1fs, text=%s", time.monotonic() - t0, text[:40])
        return f"Spoke (cached): {text}"

    def _audio_bytes_to_array(self, audio_bytes: bytes) -> np.ndarray:
        import soundfile as sf  # type: ignore[import-untyped]

        with sf.SoundFile(io.BytesIO(audio_bytes), "r") as sound_file:
            actual_sr = int(sound_file.samplerate)
            audio_array: np.ndarray[Any, Any] = sound_file.read(dtype="float32")
        if audio_array.ndim > 1:
            audio_array = audio_array.mean(axis=1)
        if actual_sr != _SPEECH_SAMPLE_RATE:
            duration = len(audio_array) / float(actual_sr)
            src_t = np.linspace(0.0, duration, num=len(audio_array), endpoint=False)
            dst_len = max(1, int(duration * _SPEECH_SAMPLE_RATE))
            dst_t = np.linspace(0.0, duration, num=dst_len, endpoint=False)
            audio_array = np.interp(dst_t, src_t, audio_array)
        return audio_array.astype(np.float32)

    def _synthesize_to_array(self, text: str) -> np.ndarray:
        if self._tts_node is None:
            raise RuntimeError("TTS not initialized")
        if isinstance(self._tts_node, DashScopeTTSNode):
            return self._audio_bytes_to_array(self._synthesize_dashscope_audio(text))
        from openai import OpenAI

        client = OpenAI()
        response = client.audio.speech.create(
            model="tts-1",
            voice="onyx",
            input=text,
            speed=1.2,
        )
        return self._audio_bytes_to_array(response.content)

    def _speak_on_go2_if_available(self, text: str) -> str | None:
        """优先通过 Go2 AudioHub 播放, 让声音从机器人扬声器发出."""
        if self._connection is None or not os.environ.get("DASHSCOPE_API_KEY"):
            return None

        try:
            audio_bytes = self._synthesize_dashscope_audio(text)
            wav_bytes = self._convert_audio_to_wav(audio_bytes)
            audio_name = f"dimos_tts_{hashlib.sha1(text.encode()).hexdigest()[:12]}"

            unique_id = self._find_go2_audio(audio_name)
            if unique_id is None:
                self._upload_go2_audio(audio_name, wav_bytes)
                unique_id = self._wait_for_go2_audio(audio_name)

            if unique_id is None:
                raise RuntimeError(f"Go2 AudioHub did not expose uploaded file {audio_name}")

            self._play_go2_audio(unique_id)
            return f"Spoke on Go2: {text}"
        except Exception:
            logger.exception("Go2 AudioHub speech failed; falling back to local audio output")
            return None

    def _synthesize_dashscope_audio(self, text: str) -> bytes:
        import dashscope  # type: ignore[import]
        from dashscope.audio.tts_v2 import SpeechSynthesizer  # type: ignore[import]

        dashscope.api_key = os.environ["DASHSCOPE_API_KEY"]
        synthesizer = SpeechSynthesizer(model="cosyvoice-v3-flash", voice="longanyang")
        audio: bytes = synthesizer.call(text)
        if not audio:
            raise RuntimeError("DashScope TTS returned empty audio")
        return audio

    def _convert_audio_to_wav(self, audio_bytes: bytes) -> bytes:
        import soundfile as sf  # type: ignore[import-untyped]

        audio, sample_rate = sf.read(io.BytesIO(audio_bytes), dtype="float32")
        if audio.ndim > 1:
            audio = audio.mean(axis=1)

        target_rate = 44100
        if sample_rate != target_rate:
            duration = len(audio) / float(sample_rate)
            src_t = np.linspace(0.0, duration, num=len(audio), endpoint=False)
            dst_len = max(1, int(duration * target_rate))
            dst_t = np.linspace(0.0, duration, num=dst_len, endpoint=False)
            audio = np.interp(dst_t, src_t, audio).astype(np.float32)

        out = io.BytesIO()
        sf.write(out, audio, target_rate, format="WAV", subtype="PCM_16")
        return out.getvalue()

    def _get_go2_audio_list(self) -> list[dict[str, object]]:
        from unitree_webrtc_connect.constants import AUDIO_API, RTC_TOPIC

        assert self._connection is not None
        response = self._connection.publish_request(
            RTC_TOPIC["AUDIO_HUB_REQ"],
            {"api_id": AUDIO_API["GET_AUDIO_LIST"], "parameter": json.dumps({})},
        )
        data = response.get("data", {}) if isinstance(response, dict) else {}
        payload = data.get("data", "{}") if isinstance(data, dict) else "{}"
        if isinstance(payload, str):
            parsed = json.loads(payload)
        elif isinstance(payload, dict):
            parsed = payload
        else:
            return []
        audio_list = parsed.get("audio_list", [])
        return audio_list if isinstance(audio_list, list) else []

    def _find_go2_audio(self, audio_name: str) -> str | None:
        for item in self._get_go2_audio_list():
            if item.get("CUSTOM_NAME") == audio_name:
                unique_id = item.get("UNIQUE_ID")
                return str(unique_id) if unique_id else None
        return None

    def _wait_for_go2_audio(self, audio_name: str) -> str | None:
        for _ in range(10):
            unique_id = self._find_go2_audio(audio_name)
            if unique_id is not None:
                return unique_id
            time.sleep(0.3)
        return None

    def _upload_go2_audio(self, audio_name: str, wav_bytes: bytes) -> None:
        from unitree_webrtc_connect.constants import AUDIO_API, RTC_TOPIC

        assert self._connection is not None
        file_md5 = hashlib.md5(wav_bytes).hexdigest()
        b64_data = base64.b64encode(wav_bytes).decode("utf-8")
        chunk_size = 4096
        chunks = [b64_data[i : i + chunk_size] for i in range(0, len(b64_data), chunk_size)]

        for index, chunk in enumerate(chunks, start=1):
            parameter = {
                "file_name": audio_name,
                "file_type": "wav",
                "file_size": len(wav_bytes),
                "current_block_index": index,
                "total_block_number": len(chunks),
                "block_content": chunk,
                "current_block_size": len(chunk),
                "file_md5": file_md5,
                "create_time": int(time.time() * 1000),
            }
            self._connection.publish_request(
                RTC_TOPIC["AUDIO_HUB_REQ"],
                {
                    "api_id": AUDIO_API["UPLOAD_AUDIO_FILE"],
                    "parameter": json.dumps(parameter, ensure_ascii=True),
                },
            )
            time.sleep(0.05)

    def _play_go2_audio(self, unique_id: str) -> None:
        from unitree_webrtc_connect.constants import AUDIO_API, RTC_TOPIC

        assert self._connection is not None
        self._connection.publish_request(
            RTC_TOPIC["AUDIO_HUB_REQ"],
            {
                "api_id": AUDIO_API["SELECT_START_PLAY"],
                "parameter": json.dumps({"unique_id": unique_id}),
            },
        )
