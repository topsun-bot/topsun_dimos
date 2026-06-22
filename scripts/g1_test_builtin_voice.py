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

"""Listen to G1 built-in ASR on DDS ``rt/audio_msg``.

Requires Unitree App → 唤醒对话模式. Usage on Orin::

    bash ~/topsun_dimos/scripts/orin_test_builtin_voice.sh eth0
"""

from __future__ import annotations

import sys
import time

from unitree_sdk2py.core.channel import (  # type: ignore[import-not-found]
    ChannelFactoryInitialize,
    ChannelSubscriber,
)
from unitree_sdk2py.g1.audio.g1_audio_client import AudioClient  # type: ignore[import-not-found]
from unitree_sdk2py.idl.std_msgs.msg.dds_ import String_  # type: ignore[import-not-found]

from dimos.robot.unitree.g1.g1_builtin_voice_input import parse_g1_asr_payload


def main() -> None:
    iface = sys.argv[1] if len(sys.argv) > 1 else "eth0"
    seconds = float(sys.argv[2]) if len(sys.argv) > 2 else 60.0

    print(f"DDS 网卡: {iface}")
    ChannelFactoryInitialize(0, iface)
    client = AudioClient()
    client.SetTimeout(5.0)
    client.Init()
    print("AudioClient 已初始化")
    print("请确保 App 已开启「唤醒对话模式」,对着 G1 说话 …")
    print(f"监听 rt/audio_msg ({seconds:.0f}s), Ctrl+C 提前退出\n")

    def on_msg(msg: String_) -> None:
        text = parse_g1_asr_payload(str(msg.data))
        if text:
            print(f"  ASR: {text!r}")

    sub = ChannelSubscriber("rt/audio_msg", String_)
    sub.Init(on_msg, 10)
    try:
        time.sleep(seconds)
    except KeyboardInterrupt:
        print("\n中断")
    finally:
        sub.Close()
    print("结束")


if __name__ == "__main__":
    main()
