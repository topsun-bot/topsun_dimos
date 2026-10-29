# Copyright 2026 Dimensional Inc.
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

"""LowCmd / LowState CDR: SDK CRC samples and the older reserve-only layout."""

from __future__ import annotations

from pathlib import Path
import struct

import pytest

from dimos.robot.unitree.go2.dds.codec import CdrStructCodec
from dimos.robot.unitree.go2.dds.msgs.LowCmd import LowCmd
from dimos.robot.unitree.go2.dds.msgs.LowState import LowState

_FIXTURES = Path(__file__).parent / "rust" / "tests"
_CRC_SIZE = 4
_LOWCMD = CdrStructCodec(LowCmd)
_LOWSTATE = CdrStructCodec(LowState)


def test_lowcmd_with_crc_decodes_the_sdk_sample() -> None:
    data = (_FIXTURES / "lowcmd.cdr").read_bytes()
    cmd = _LOWCMD.decode(data)
    assert cmd.crc == struct.unpack_from("<I", data, len(data) - _CRC_SIZE)[0]
    assert cmd.crc != 0
    assert all((m.mode, m.kp, m.kd) == (1, 40.0, 1.0) for m in cmd.motor_cmd[:12])


def test_lowcmd_without_crc_decodes_the_legacy_recording_layout() -> None:
    data = (_FIXTURES / "lowcmd.cdr").read_bytes()[:-_CRC_SIZE]
    cmd = _LOWCMD.decode(data)
    assert cmd.crc == 0
    assert all((m.mode, m.kp, m.kd) == (1, 40.0, 1.0) for m in cmd.motor_cmd[:12])


def test_lowstate_with_crc_decodes_the_sdk_sample() -> None:
    data = (_FIXTURES / "lowstate.cdr").read_bytes()
    state = _LOWSTATE.decode(data)
    assert state.crc == struct.unpack_from("<I", data, len(data) - _CRC_SIZE)[0]
    assert state.crc != 0
    assert all(m.mode == 1 for m in state.motor_state[:12])
    assert 20.0 < state.power_v < 35.0


def test_lowstate_without_crc_decodes_the_legacy_recording_layout() -> None:
    data = (_FIXTURES / "lowstate.cdr").read_bytes()[:-_CRC_SIZE]
    state = _LOWSTATE.decode(data)
    assert state.crc == 0
    assert all(m.mode == 1 for m in state.motor_state[:12])
    assert 20.0 < state.power_v < 35.0


def test_a_short_lowstate_body_still_fails() -> None:
    data = (_FIXTURES / "lowstate.cdr").read_bytes()[:40]
    with pytest.raises((struct.error, ValueError)):
        _LOWSTATE.decode(data)


def test_leftover_bytes_that_are_not_a_crc_still_fail() -> None:
    data = (_FIXTURES / "lowcmd.cdr").read_bytes()[:-_CRC_SIZE] + b"\x00\x00"
    with pytest.raises(AssertionError, match="LowCmd"):
        _LOWCMD.decode(data)
