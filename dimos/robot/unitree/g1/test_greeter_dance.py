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

from dimos.robot.unitree.g1.greeter_skill import resolve_dance_style


def test_resolve_dance_style_laptop_webrtc() -> None:
    assert (
        resolve_dance_style("auto", "webrtc", onboard_compute=False) == "webrtc_arms"
    )


def test_resolve_dance_style_orin_auto() -> None:
    assert (
        resolve_dance_style("auto", "webrtc", onboard_compute=True) == "onboard_random"
    )


def test_resolve_dance_style_explicit() -> None:
    assert (
        resolve_dance_style("onboard_random", "webrtc", onboard_compute=False)
        == "onboard_random"
    )
    assert (
        resolve_dance_style("webrtc_arms", "webrtc", onboard_compute=True)
        == "webrtc_arms"
    )
