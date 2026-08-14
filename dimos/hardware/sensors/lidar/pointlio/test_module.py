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

from dimos.hardware.sensors.lidar.pointlio.module import PointLioConfig


def test_pointlio_tf_relay_remains_enabled_by_default() -> None:
    assert PointLioConfig().publish_tf is True


def test_pointlio_tf_relay_can_be_disabled_for_an_adapter_owned_tree() -> None:
    assert PointLioConfig(publish_tf=False).publish_tf is False


def test_pointlio_default_odom_rate_matches_source_state_rate() -> None:
    config = PointLioConfig()

    assert config.odom_freq == config.pointcloud_freq == 10.0


def test_build_command_overrides_sibling_inputs_with_absolute_paths() -> None:
    command = PointLioConfig().build_command

    assert command is not None
    assert "--override-input livox-sdk path:/" in command
    assert "--override-input livox-common path:/" in command
    assert "--no-write-lock-file" in command
    assert "--fallback --option connect-timeout 1" in command
