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
import requests
import soundfile as sf
from unitree_webrtc_connect.constants import RTC_TOPIC

from dimos.core.core import rpc
from dimos.core.global_config import global_config
from dimos.core.module import Module
from dimos.core.stream import In
from dimos.stream.audio.node_output import SounddeviceAudioOutput
from dimos.utils.logging_config import setup_logger

logger = setup_logger()

# MiniMax TTS constants
MINIMAX_TTS_URL = "https://api.minimaxi.com/v1/t2a_v2"
BARK_TEXT = "汪汪 汪汪 汪汪 汪汪汪"

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

    In real mode (--robot-ip): generates audio via MiniMax TTS and uploads
    to the robot via the shared GO2Connection WebRTC session for playback on the robot speaker.

    In replay/simulation mode: plays audio locally via sounddevice or logs
    a message.
    """

    go2_conn: In[Any]  # Receives the shared UnitreeWebRTCConnection from GO2Connection
    _audio_output: SounddeviceAudioOutput | None = None
    _timer: threading.Timer | None = None
    _go2_connection: Any | None = None

    @rpc
    def start(self) -> None:
        super().start()
        self.go2_conn.subscribe(self._on_go2_conn)
        logger.info("StartupBarkModule starting, scheduling bark in 2 seconds")
        self._timer = threading.Timer(2.0, self._bark)
        self._timer.start()

    def _on_go2_conn(self, conn: Any) -> None:
        """Called when GO2Connection publishes its LegionConnection."""
        self._go2_connection = conn

    @rpc
    def stop(self) -> None:
        if self._timer:
            self._timer.cancel()
            self._timer = None
        if self._audio_output:
            self._audio_output.stop()
            self._audio_output = None
        # Note: do NOT close _go2_connection — it belongs to GO2Connection and is closed by it
        self._go2_connection = None
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
            # Generate audio via MiniMax API
            audio_bytes = self._generate_audio(BARK_TEXT)

            # Read MP3 bytes as numpy array via soundfile
            with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as tmp_mp3:
                tmp_mp3.write(audio_bytes)
                tmp_mp3_path = tmp_mp3.name

            try:
                audio_array, sample_rate = sf.read(tmp_mp3_path)
            finally:
                os.unlink(tmp_mp3_path)

            if audio_array.ndim > 1:
                audio_array = np.mean(audio_array, axis=1)

            # Play via sounddevice at native sample rate (32000 Hz from MiniMax)
            if self._audio_output is None:
                self._audio_output = SounddeviceAudioOutput(sample_rate=sample_rate)
            self._audio_output.consume_audio(audio_array)

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
            audio_data = self._generate_audio(BARK_TEXT)
            uuid = self._upload_audio_to_robot(audio_data, filename="bark.wav")
            self._play_audio_on_robot(uuid)
            logger.info("Robot bark playback triggered successfully")

        except Exception as e:
            logger.error(f"Failed to play bark on robot: {e}")

    def _generate_audio(self, text: str) -> bytes:
        """Generate audio via MiniMax TTS API (hex-encoded MP3 response)."""
        api_key = os.environ["MINIMAX_API_KEY"]

        payload = {
            "model": "speech-2.8-hd",
            "text": text,
            "stream": False,
            "voice_setting": {
                "voice_id": "female-tianmei",
                "speed": 1.0,
                "vol": 1.0,
                "pitch": 0,
                "text_normalization": True,
            },
            "audio_setting": {
                "sample_rate": 32000,
                "bitrate": 256000,
                "format": "mp3",
                "channel": 1,
            },
            "language_boost": "Chinese",
        }

        response = requests.post(
            MINIMAX_TTS_URL,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=60,
        )
        response.raise_for_status()

        data = response.json()
        base = data.get("base_resp") or {}
        if base.get("status_code") != 0:
            raise RuntimeError(f"MiniMax error: {base}")

        audio_hex = data["data"]["audio"].strip().replace(" ", "")
        return bytes.fromhex(audio_hex)

    def _webrtc_request(self, api_id: int, parameter: dict | None = None) -> Any:
        """Send a WebRTC request to the robot via the shared GO2Connection session."""
        if self._go2_connection is None:
            raise RuntimeError(
                "GO2Connection not available — bark may have fired before connection was established"
            )
        conn = self._go2_connection
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
