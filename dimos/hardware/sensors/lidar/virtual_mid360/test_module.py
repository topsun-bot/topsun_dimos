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

from pathlib import Path

from pydantic import ValidationError
import pytest

from dimos.hardware.sensors.lidar.virtual_mid360 import module


def test_build_command_uses_current_repository_rust_crates() -> None:
    config = module.VirtualMid360Config()
    expected = Path(module.__file__).resolve().parents[5] / "native" / "rust"

    assert config.build_command is not None
    assert "--no-write-lock-file" in config.build_command
    assert f"--override-input dimos-rust path:{expected}" in config.build_command


def test_linux_alias_iface_rejects_names_longer_than_ifnamesiz() -> None:
    with pytest.raises(ValidationError, match="1-15 bytes"):
        module.VirtualMid360Config(alias_iface="dimos-mid360-test")


def test_virtual_lidar_accepts_both_supported_device_models() -> None:
    assert module.VirtualMid360Config(device_model="mid360").device_model == "mid360"
    assert module.VirtualMid360Config(device_model="mid360s").device_model == "mid360s"


@pytest.mark.parametrize("alias_iface", ["bad/name", "bad name", ""])
def test_linux_alias_iface_rejects_invalid_characters(alias_iface: str) -> None:
    with pytest.raises(ValidationError):
        module.VirtualMid360Config(alias_iface=alias_iface)
