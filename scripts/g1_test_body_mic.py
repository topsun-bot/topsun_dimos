#!/usr/bin/env python3
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

"""Probe G1 body microphones (UDP multicast PCM, not ALSA/pulse).

G1 streams 16 kHz mono int16 PCM on multicast 239.168.123.161:5555.
``orin_test_mic.sh`` tests ALSA only — body mics will show RMS=0 there.

Usage on Orin::

    bash ~/topsun_dimos/scripts/orin_test_body_mic.sh eth0
"""

from __future__ import annotations

import json
import socket
import struct
import sys
import time

import numpy as np

_G1_MIC_GROUP = "239.168.123.161"
_G1_MIC_PORT = 5555
_SAMPLE_RATE = 16000
_MAX_WAIT_NO_DATA_S = 12.0


def _print_mic_enable_help() -> None:
    print(
        "\n机身麦 UDP 组播没有数据。常见原因:\n"
        "  1. 宇树 App 未开启「唤醒对话模式」:\n"
        "     连接 G1 → 设备 → 数据 → 语音助手 → 唤醒对话模式 (Wake-up Conversation)\n"
        "  2. 部分 G1 固件即使用户开启，PC2/Orin 仍收不到有效麦数据 (宇树社区已知问题)\n"
        "  3. 实际部署建议: USB 麦克风 + orin_test_mic.sh scan\n"
        "  4. 临时绕过: bash ~/topsun_dimos/scripts/orin_dimos.sh agent-send \"你好\"\n"
    )


def _wake_voice_service(iface: str) -> None:
    """Init DDS AudioClient so the voice service is up (may help mic multicast)."""
    try:
        from unitree_sdk2py.core.channel import ChannelFactoryInitialize  # type: ignore
        from unitree_sdk2py.g1.audio.g1_audio_api import ROBOT_API_ID_AUDIO_ASR  # type: ignore
        from unitree_sdk2py.g1.audio.g1_audio_client import AudioClient  # type: ignore

        ChannelFactoryInitialize(0, iface)
        client = AudioClient()
        client.SetTimeout(5.0)
        client.Init()
        code, _ = client._Call(ROBOT_API_ID_AUDIO_ASR, json.dumps({}))
        print(f"已初始化 voice 服务 (ASR api call code={code})")
    except Exception as exc:
        print(f"(跳过 voice 服务初始化: {exc})")


def _find_robot_subnet_ip(iface: str) -> str:
    import fcntl

    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        ip = socket.inet_ntoa(
            fcntl.ioctl(
                s.fileno(),
                0x8915,  # SIOCGIFADDR
                struct.pack("256s", iface.encode()[:15]),
            )[20:24]
        )
    finally:
        s.close()
    if not ip.startswith("192.168.123."):
        raise RuntimeError(f"接口 {iface!r} 地址 {ip} 不在 192.168.123.* 机器人网段")
    return ip


def _record_multicast(local_ip: str, seconds: float) -> np.ndarray:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("", _G1_MIC_PORT))

    mreq = struct.pack(
        "4s4s",
        socket.inet_aton(_G1_MIC_GROUP),
        socket.inet_aton(local_ip),
    )
    sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)
    sock.settimeout(1.0)

    target_bytes = int(seconds * _SAMPLE_RATE * 2)
    chunks: list[bytes] = []
    total = 0
    no_data_since = time.monotonic()
    print(f"监听 {_G1_MIC_GROUP}:{_G1_MIC_PORT} via {local_ip}，请对着 G1 说话 …")
    while total < target_bytes:
        if time.monotonic() - no_data_since > _MAX_WAIT_NO_DATA_S:
            sock.close()
            _print_mic_enable_help()
            raise RuntimeError(f"{_MAX_WAIT_NO_DATA_S:.0f}s 内未收到任何组播包")
        try:
            data, _ = sock.recvfrom(4096)
        except socket.timeout:
            continue
        if not data:
            continue
        if total == 0:
            print("  收到组播数据，录音中…")
        no_data_since = time.monotonic()
        chunks.append(data)
        total += len(data)

    sock.close()
    pcm = b"".join(chunks)[:target_bytes]
    return np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0


def _rms(audio: np.ndarray) -> float:
    if audio.size == 0:
        return 0.0
    return float(np.sqrt(np.mean(audio.astype(np.float32) ** 2)))


def main() -> None:
    iface = sys.argv[1] if len(sys.argv) > 1 else "eth0"
    local_ip = _find_robot_subnet_ip(iface)
    print(f"机器人网口 {iface} -> {local_ip}")

    _wake_voice_service(iface)

    try:
        audio = _record_multicast(local_ip, 3.0)
    except RuntimeError as exc:
        print(f"失败: {exc}")
        sys.exit(1)

    peak = _rms(audio)
    print(f"录音完成: {len(audio)} samples @ {_SAMPLE_RATE}Hz, RMS={peak:.6f}")

    if peak < 0.003:
        print("机身麦组播有数据但几乎无有效信号 (可能全是零)。")
        _print_mic_enable_help()
        sys.exit(1)

    print("机身麦组播有信号。")
    try:
        from faster_whisper import WhisperModel  # type: ignore[import-untyped]

        print("Whisper tiny 识别中…")
        model = WhisperModel("tiny", device="cpu", compute_type="int8")
        segments, _ = model.transcribe(audio, language="zh")
        text = "".join(s.text for s in segments).strip()
        print(f"识别结果: {text!r}")
    except Exception as exc:
        print(f"(跳过 Whisper: {exc})")


if __name__ == "__main__":
    main()
