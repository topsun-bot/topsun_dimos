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

"""Startup sound module for Unitree Go2.

Plays a local MP3/WAV through the robot speaker via WebRTC AudioHub
(``GO2Connection.publish_request`` on ``AUDIO_HUB_REQ``).

Default clip: ``go2/assets/output_audio.mp3`` (then ``output_audio.wav``,
``startup_greeting.*``). Override with ``STARTUP_BARK_AUDIO_FILE=/path/to/file``.
"""

import base64
import hashlib
import io
import json
import os
from pathlib import Path
import tempfile
import threading
import time
from typing import Any

import numpy as np
import soundfile as sf  # type: ignore[import-untyped]
from unitree_webrtc_connect.constants import AUDIO_API, RTC_TOPIC

from dimos.core.core import rpc
from dimos.core.global_config import global_config
from dimos.core.module import Module
from dimos.robot.unitree.go2.connection_spec import GO2ConnectionSpec
from dimos.utils.logging_config import setup_logger

logger = setup_logger()

GREETING_AUDIO_FILE_ENV = "STARTUP_BARK_AUDIO_FILE"
GREETING_AUDIO_CHUNK_SIZE = 61440
GREETING_TARGET_SAMPLE_RATE = 22050
PLAY_MODE_NO_CYCLE = "no_cycle"

_GO2_ASSETS_DIR = Path(__file__).resolve().parent.parent / "assets"
_DEFAULT_AUDIO_BASENAMES = (
    "output_audio.mp3",
    "output_audio.wav",
    "startup_greeting.mp3",
    "startup_greeting.wav",
)


def resolve_startup_audio_path() -> Path | None:
    """Return the audio file to play, or None if none is configured."""
    env_path = os.getenv(GREETING_AUDIO_FILE_ENV)
    if env_path:
        path = Path(env_path).expanduser()
        if path.is_file():
            return path
        logger.error(f"{GREETING_AUDIO_FILE_ENV}={env_path!r} is not a readable file")
        return None

    for name in _DEFAULT_AUDIO_BASENAMES:
        candidate = _GO2_ASSETS_DIR / name
        if candidate.is_file():
            return candidate

    asset_clips = sorted(
        p for pattern in ("*.mp3", "*.wav", "*.MP3", "*.WAV") for p in _GO2_ASSETS_DIR.glob(pattern)
    )
    if len(asset_clips) == 1:
        logger.info(f"Using sole audio clip in assets: {asset_clips[0]}")
        return asset_clips[0]
    if len(asset_clips) > 1:
        for preferred in ("output_audio.mp3", "output_audio.wav"):
            p = _GO2_ASSETS_DIR / preferred
            if p.is_file():
                logger.info(f"Using preferred clip among multiple assets: {p}")
                return p
        logger.error(
            f"Multiple audio files in {_GO2_ASSETS_DIR}: "
            f"{[p.name for p in asset_clips]}. "
            f"Keep only output_audio.mp3 (or one other clip), or set {GREETING_AUDIO_FILE_ENV}."
        )
        return None

    logger.error(
        "No startup audio file found. Add "
        f"{_GO2_ASSETS_DIR / 'output_audio.mp3'} "
        f"or set {GREETING_AUDIO_FILE_ENV}=/path/to/your.mp3"
    )
    return None


class StartupBarkModule(Module):
    """Plays a local audio clip once after the Go2 WebRTC connection is up."""

    _connection: GO2ConnectionSpec
    _timer: threading.Timer | None = None

    @rpc
    def start(self) -> None:
        super().start()
        logger.info("StartupBarkModule starting, scheduling playback in 2 seconds")
        self._timer = threading.Timer(2.0, self._play_startup_sound)
        self._timer.start()

    @rpc
    def stop(self) -> None:
        if self._timer:
            self._timer.cancel()
            self._timer = None
        super().stop()

    def _play_startup_sound(self) -> None:
        try:
            if global_config.replay or global_config.simulation:
                logger.info("Skipping startup sound in replay/simulation mode")
                return
            self._play_on_robot()
        except Exception:
            logger.exception("Error during startup sound playback")

    def _play_on_robot(self) -> None:
        audio_path = resolve_startup_audio_path()
        if audio_path is None:
            return

        logger.info(f"Playing startup sound via WebRTC AudioHub: {audio_path}")
        wav_data = self._load_audio_file_as_wav(audio_path)
        filename = f"startup_bark_{int(time.time() * 1000)}"
        uuid = self._upload_audio_to_robot(wav_data, filename)
        self._play_audio_on_robot(uuid)
        logger.info("Startup sound sent to robot")

    def _load_audio_file_as_wav(self, path: Path) -> bytes:
        return self._audio_bytes_to_wav(path.read_bytes(), suffix=path.suffix or ".wav")

    def _audio_bytes_to_wav(self, audio_data: bytes, suffix: str) -> bytes:
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp_audio:
            tmp_audio.write(audio_data)
            tmp_audio_path = tmp_audio.name

        try:
            audio_array, sample_rate = sf.read(tmp_audio_path)
        finally:
            os.unlink(tmp_audio_path)

        if audio_array.ndim > 1:
            audio_array = np.mean(audio_array, axis=1)

        if sample_rate != GREETING_TARGET_SAMPLE_RATE:
            old_length = len(audio_array)
            new_length = int(old_length * GREETING_TARGET_SAMPLE_RATE / sample_rate)
            old_indices = np.arange(old_length)
            new_indices = np.linspace(0, old_length - 1, new_length)
            audio_array = np.interp(new_indices, old_indices, audio_array)
            sample_rate = GREETING_TARGET_SAMPLE_RATE

        max_abs = np.max(np.abs(audio_array)) if len(audio_array) else 0.0
        if max_abs > 0:
            audio_array = audio_array / max_abs

        wav_output = io.BytesIO()
        sf.write(wav_output, audio_array, sample_rate, format="WAV", subtype="PCM_16")
        return wav_output.getvalue()

    def _audiohub_request(
        self,
        api_id: int,
        parameter: dict[str, Any] | None = None,
    ) -> dict[Any, Any]:
        parameter = parameter or {}
        request_data = {
            "api_id": api_id,
            "parameter": json.dumps(parameter) if parameter else "{}",
        }
        return self._connection.publish_request(RTC_TOPIC["AUDIO_HUB_REQ"], request_data)

    def _upload_audio_to_robot(self, audio_data: bytes, filename: str) -> str:
        file_md5 = hashlib.md5(audio_data).hexdigest()
        b64_data = base64.b64encode(audio_data).decode("utf-8")
        chunks = [
            b64_data[i : i + GREETING_AUDIO_CHUNK_SIZE]
            for i in range(0, len(b64_data), GREETING_AUDIO_CHUNK_SIZE)
        ]
        total_chunks = len(chunks)

        logger.info(f"Uploading '{filename}' in {total_chunks} chunk(s)")
        for index, chunk in enumerate(chunks, 1):
            self._audiohub_request(
                AUDIO_API["UPLOAD_AUDIO_FILE"],
                {
                    "file_name": filename,
                    "file_type": "wav",
                    "file_size": len(audio_data),
                    "current_block_index": index,
                    "total_block_number": total_chunks,
                    "block_content": chunk,
                    "current_block_size": len(chunk),
                    "file_md5": file_md5,
                    "create_time": int(time.time() * 1000),
                },
            )

        list_response = self._audiohub_request(AUDIO_API["GET_AUDIO_LIST"])
        uuid = self._find_uploaded_audio_uuid(list_response, filename)
        if uuid:
            return uuid

        logger.warning(
            f"Could not find uploaded audio '{filename}' in AudioHub list; using filename"
        )
        return filename

    def _find_uploaded_audio_uuid(self, response: dict[Any, Any], filename: str) -> str | None:
        data = response.get("data", {}) if response else {}
        data_str = data.get("data", "{}") if isinstance(data, dict) else "{}"
        audio_list = json.loads(data_str).get("audio_list", [])
        for audio in audio_list:
            if audio.get("CUSTOM_NAME") == filename:
                return audio.get("UNIQUE_ID")
        return None

    def _play_audio_on_robot(self, uuid: str) -> None:
        self._audiohub_request(AUDIO_API["SET_PLAY_MODE"], {"play_mode": PLAY_MODE_NO_CYCLE})
        time.sleep(0.1)
        self._audiohub_request(AUDIO_API["SELECT_START_PLAY"], {"unique_id": uuid})
