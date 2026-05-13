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

"""Minimal SDK2 audio client wrapper for Unitree TTS."""

from __future__ import annotations

import json
from typing import Any

from unitree_sdk2py.core.channel import ChannelFactoryInitialize
from unitree_sdk2py.rpc.client import Client

AUDIO_SERVICE_NAME = "voice"
AUDIO_API_VERSION = "1.0.0.0"

ROBOT_API_ID_AUDIO_TTS = 1001
ROBOT_API_ID_AUDIO_ASR = 1002
ROBOT_API_ID_AUDIO_START_PLAY = 1003
ROBOT_API_ID_AUDIO_STOP_PLAY = 1004
ROBOT_API_ID_AUDIO_GET_VOLUME = 1005
ROBOT_API_ID_AUDIO_SET_VOLUME = 1006
ROBOT_API_ID_AUDIO_SET_RGB_LED = 1010


class AudioClient(Client):
    def __init__(self) -> None:
        super().__init__(AUDIO_SERVICE_NAME, False)
        self.tts_index = 0

    def Init(self) -> None:
        self._SetApiVerson(AUDIO_API_VERSION)
        self._RegistApi(ROBOT_API_ID_AUDIO_TTS, 0)
        self._RegistApi(ROBOT_API_ID_AUDIO_ASR, 0)
        self._RegistApi(ROBOT_API_ID_AUDIO_START_PLAY, 0)
        self._RegistApi(ROBOT_API_ID_AUDIO_STOP_PLAY, 0)
        self._RegistApi(ROBOT_API_ID_AUDIO_GET_VOLUME, 0)
        self._RegistApi(ROBOT_API_ID_AUDIO_SET_VOLUME, 0)
        self._RegistApi(ROBOT_API_ID_AUDIO_SET_RGB_LED, 0)

    def TtsMaker(self, text: str, speaker_id: int) -> int:
        payload = {
            "index": self.tts_index,
            "text": text,
            "speaker_id": speaker_id,
        }
        self.tts_index += 1
        code, _data = self._Call(ROBOT_API_ID_AUDIO_TTS, json.dumps(payload))
        return code

    def GetVolume(self) -> tuple[int, Any | None]:
        code, data = self._Call(ROBOT_API_ID_AUDIO_GET_VOLUME, json.dumps({}))
        if code == 0:
            return code, json.loads(data)
        return code, None

    def SetVolume(self, volume: int) -> int:
        code, _data = self._Call(
            ROBOT_API_ID_AUDIO_SET_VOLUME,
            json.dumps({"volume": volume}),
        )
        return code

    def LedControl(self, red: int, green: int, blue: int) -> int:
        code, _data = self._Call(
            ROBOT_API_ID_AUDIO_SET_RGB_LED,
            json.dumps({"R": red, "G": green, "B": blue}),
        )
        return code

    def PlayStream(self, app_name: str, stream_id: str, pcm_data: bytes) -> tuple[int, Any | None]:
        parameter = json.dumps({"app_name": app_name, "stream_id": stream_id})
        pcm_list = list(pcm_data)
        return self._CallRequestWithParamAndBin(ROBOT_API_ID_AUDIO_START_PLAY, parameter, pcm_list)

    def PlayStop(self, app_name: str) -> int:
        code, _data = self._Call(
            ROBOT_API_ID_AUDIO_STOP_PLAY,
            json.dumps({"app_name": app_name}),
        )
        return code


def tts_maker(text: str, speaker_id: int = 0, network_interface: str | None = None) -> int:
    """Initialize the DDS client and run TtsMaker once."""
    if network_interface:
        ChannelFactoryInitialize(0, network_interface)
    else:
        ChannelFactoryInitialize(0)

    client = AudioClient()
    client.SetTimeout(10.0)
    client.Init()
    return client.TtsMaker(text, speaker_id)
