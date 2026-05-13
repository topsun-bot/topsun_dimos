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

"""Startup bark module for Unitree Go2 robot.

Plays a "汪汪 汪汪" bark sound on robot startup to confirm connection.
"""

import base64
import hashlib
import json
import os
import tempfile
import threading
import time
from typing import Any

import numpy as np
from openai import OpenAI
import soundfile as sf
from unitree_webrtc_connect.constants import RTC_TOPIC

from dimos.core.core import rpc
from dimos.core.global_config import global_config
from dimos.core.module import Module
from dimos.stream.audio.node_output import SounddeviceAudioOutput
from dimos.stream.audio.tts.node_openai import OpenAITTSNode, Voice
from dimos.utils.logging_config import setup_logger

logger = setup_logger()

# Audio API constants (from go2_webrtc_driver)
AUDIO_API = {
    "GET_AUDIO_LIST": 1001,
    "SELECT_START_PLAY": 1002,
    "PAUSE": 1003,
    "UNSUSPEND": 1004,
    "SET_PLAY_MODE": 1007,
    "UPLOAD_AUDIO_FILE": 2001,
}

PLAY_MODES = {"NO_CYCLE": "no_cycle", "SINGLE_CYCLE": "single_cycle", "LIST_LOOP": "list_loop"}


class StartupBarkModule(Module):
    """Plays a bark sound on Go2 robot startup.

    In real mode (--robot-ip): generates audio via OpenAI TTS and uploads
    to the robot via WebRTC AUDIO_HUB API for playback on the robot speaker.

    In replay/simulation mode: plays audio locally via sounddevice or logs
    a message.
    """

    _tts_node: OpenAITTSNode | None = None
    _audio_output: SounddeviceAudioOutput | None = None
    _openai_client: OpenAI | None = None
    _webrtc_connection: Any | None = None
    _timer: threading.Timer | None = None

    @rpc
    def start(self) -> None:
        super().start()
        logger.info("StartupBarkModule starting, scheduling bark in 2 seconds")
        self._timer = threading.Timer(2.0, self._bark)
        self._timer.start()

    @rpc
    def stop(self) -> None:
        if self._timer:
            self._timer.cancel()
            self._timer = None
        if self._tts_node:
            self._tts_node.dispose()
            self._tts_node = None
        if self._audio_output:
            self._audio_output.stop()
            self._audio_output = None
        if self._webrtc_connection:
            try:
                self._webrtc_connection.close()
            except Exception:
                pass
            self._webrtc_connection = None
        super().stop()

    def _bark(self) -> None:
        """Trigger the bark sound based on current mode."""
        try:
            if global_config.replay or global_config.simulation:
                self._bark_local()
            else:
                self._bark_on_robot()
        except Exception as e:
            logger.error(f"Error during bark: {e}")

    def _bark_local(self) -> None:
        """Play bark sound locally via sounddevice (replay/simulation mode)."""
        logger.info("Playing bark sound locally (replay/simulation mode)")

        try:
            if self._tts_node is None:
                self._tts_node = OpenAITTSNode(speed=1.3, voice=Voice.ONYX)
            if self._audio_output is None:
                self._audio_output = SounddeviceAudioOutput(sample_rate=24000)

            self._audio_output.consume_audio(self._tts_node.emit_audio())

            from reactivex import Subject

            text_subject: Subject[str] = Subject()
            self._tts_node.consume_text(text_subject)
            text_subject.on_next("汪汪 汪汪")
            text_subject.on_completed()

            time.sleep(1.5)
            logger.info("Local bark playback completed")

        except Exception as e:
            logger.warning(f"Local audio playback failed (this is OK if no speaker): {e}")

    def _bark_on_robot(self) -> None:
        """Generate TTS audio and upload to robot via WebRTC for playback."""
        robot_ip = global_config.robot_ip
        if not robot_ip:
            logger.warning("No robot IP configured, skipping bark")
            return

        logger.info(f"Playing bark sound on robot at {robot_ip}")

        try:
            # Lazily create OpenAI client and WebRTC connection
            if self._openai_client is None:
                self._openai_client = OpenAI()

            audio_data = self._generate_audio("汪汪 汪汪")
            uuid = self._upload_audio_to_robot(audio_data, filename="bark.wav")
            self._play_audio_on_robot(uuid)
            logger.info("Robot bark playback triggered successfully")

        except Exception as e:
            logger.error(f"Failed to play bark on robot: {e}")

    def _generate_audio(self, text: str) -> bytes:
        """Generate audio via OpenAI TTS."""
        if self._openai_client is None:
            self._openai_client = OpenAI()

        response = self._openai_client.audio.speech.create(
            model="tts-1",
            voice="echo",
            input=text,
            speed=1.3,
            response_format="mp3",
        )
        return response.content

    def _get_webrtc_connection(self) -> Any:
        """Lazily create and return a UnitreeWebRTCConnection."""
        if self._webrtc_connection is None:
            from unitree_webrtc_connect.webrtc_driver import (
                UnitreeWebRTCConnection as LegionConnection,
                WebRTCConnectionMethod,
            )

            robot_ip = global_config.robot_ip
            self._webrtc_connection = LegionConnection(
                method=WebRTCConnectionMethod.IP,
                ip=robot_ip,
            )
            self._webrtc_connection.start()

            # Wait for connection to establish
            timeout = 5.0
            start = time.time()
            while not self._webrtc_connection.is_connected and (time.time() - start) < timeout:
                time.sleep(0.1)

            if not self._webrtc_connection.is_connected:
                raise RuntimeError("Failed to establish WebRTC connection to robot")

        return self._webrtc_connection

    def _webrtc_request(self, api_id: int, parameter: dict | None = None) -> Any:
        """Send a WebRTC request to the robot."""
        conn = self._get_webrtc_connection()
        request_data = {
            "api_id": api_id,
            "parameter": json.dumps(parameter) if parameter else "{}",
        }
        return conn.publish_request(RTC_TOPIC["AUDIO_HUB_REQ"], request_data)

    def _upload_audio_to_robot(self, audio_data: bytes, filename: str) -> str:
        """Upload audio data to robot and return the audio UUID."""
        # Convert MP3 to WAV at 22050 Hz PCM_16 (matches UnitreeSpeak)
        with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as tmp_mp3:
            tmp_mp3.write(audio_data)
            tmp_mp3_path = tmp_mp3.name

        try:
            audio_array, sample_rate = sf.read(tmp_mp3_path)

            if audio_array.ndim > 1:
                audio_array = np.mean(audio_array, axis=1)

            target_sample_rate = 22050
            if sample_rate != target_sample_rate:
                old_length = len(audio_array)
                new_length = int(old_length * target_sample_rate / sample_rate)
                old_indices = np.arange(old_length)
                new_indices = np.linspace(0, old_length - 1, new_length)
                audio_array = np.interp(new_indices, old_indices, audio_array)
                sample_rate = target_sample_rate

            audio_array = audio_array / np.max(np.abs(audio_array))

            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp_wav:
                sf.write(tmp_wav.name, audio_array, sample_rate, format="WAV", subtype="PCM_16")
                wav_data = open(tmp_wav.name, "rb").read()
                os.unlink(tmp_wav.name)

        finally:
            os.unlink(tmp_mp3_path)

        # Upload via WebRTC chunks
        file_md5 = hashlib.md5(wav_data).hexdigest()
        b64_data = base64.b64encode(wav_data).decode("utf-8")

        chunk_size = 61440
        chunks = [b64_data[i : i + chunk_size] for i in range(0, len(b64_data), chunk_size)]
        total_chunks = len(chunks)

        logger.debug(f"Uploading audio in {total_chunks} chunks")
        for i, chunk in enumerate(chunks, 1):
            parameter = {
                "file_name": filename,
                "file_type": "wav",
                "file_size": len(wav_data),
                "current_block_index": i,
                "total_block_number": total_chunks,
                "block_content": chunk,
                "current_block_size": len(chunk),
                "file_md5": file_md5,
                "create_time": int(time.time() * 1000),
            }
            self._webrtc_request(AUDIO_API["UPLOAD_AUDIO_FILE"], parameter)

        # Find the uploaded audio UUID
        list_response = self._webrtc_request(AUDIO_API["GET_AUDIO_LIST"], {})
        if list_response and "data" in list_response:
            data_str = list_response.get("data", {}).get("data", "{}")
            audio_list = json.loads(data_str).get("audio_list", [])
            for audio in audio_list:
                if audio.get("CUSTOM_NAME") == filename:
                    return audio.get("UNIQUE_ID", filename)

        return filename

    def _play_audio_on_robot(self, uuid: str) -> None:
        """Trigger audio playback on the robot."""
        self._webrtc_request(AUDIO_API["SET_PLAY_MODE"], {"play_mode": PLAY_MODES["NO_CYCLE"]})
        time.sleep(0.1)
        self._webrtc_request(AUDIO_API["SELECT_START_PLAY"], {"unique_id": uuid})
