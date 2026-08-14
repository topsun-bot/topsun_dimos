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

from pydantic import ValidationError
import pytest

from dimos.hardware.sensors.lidar.fastlio2.module import FastLio2Config
from dimos.hardware.sensors.lidar.livox.module import Mid360Config
from dimos.hardware.sensors.lidar.pointlio.module import PointLioConfig


@pytest.mark.parametrize("config_type", [Mid360Config, FastLio2Config, PointLioConfig])
def test_livox_modules_default_to_mid360(config_type: type) -> None:
    assert config_type().device_model == "mid360"


@pytest.mark.parametrize("config_type", [Mid360Config, FastLio2Config, PointLioConfig])
def test_livox_modules_accept_mid360s(config_type: type) -> None:
    assert config_type(device_model="mid360s").device_model == "mid360s"


@pytest.mark.parametrize("config_type", [Mid360Config, FastLio2Config, PointLioConfig])
def test_livox_modules_reject_unknown_device_model(config_type: type) -> None:
    with pytest.raises(ValidationError):
        config_type(device_model="unknown")
