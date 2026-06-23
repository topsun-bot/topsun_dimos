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

"""Quick G1 body-speaker test on Orin (DDS AudioClient, no DimOS stack).

Usage on Orin::

    bash ~/topsun_dimos/scripts/orin_test_speaker.sh eth0
"""

from __future__ import annotations

import sys
import time

from unitree_sdk2py.core.channel import ChannelFactoryInitialize  # type: ignore[import-not-found]
from unitree_sdk2py.g1.audio.g1_audio_client import AudioClient  # type: ignore[import-not-found]


def main() -> None:
    iface = sys.argv[1] if len(sys.argv) > 1 else "eth0"
    ChannelFactoryInitialize(0, iface)

    client = AudioClient()
    client.SetTimeout(10.0)
    client.Init()
    client.SetVolume(85)

    text = "语音测试，如果你听到这句话，说明 G1 喇叭正常。"
    print(f"Playing via G1 AudioClient.TtsMaker on {iface!r} ...")
    code = client.TtsMaker(text, 0)
    print(f"TtsMaker code={code}")
    if code != 0:
        sys.exit(code)
    time.sleep(8)
    print("Done.")


if __name__ == "__main__":
    main()
