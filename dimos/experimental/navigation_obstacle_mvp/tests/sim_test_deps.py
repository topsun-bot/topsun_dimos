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

"""Shared skip helpers for MuJoCo / LCM simulation tests."""

from __future__ import annotations

import os

import lcm as lcm_mod
import pytest


def require_lcm_multicast() -> None:
    """Skip when LCM cannot bind (offline host, sandboxed network, missing lo route)."""
    url = os.environ.get("LCM_DEFAULT_URL", "udpm://239.255.76.67:7667?ttl=0")
    try:
        lcm_mod.LCM(url)
    except RuntimeError as exc:
        pytest.skip(
            "LCM multicast unavailable. On Linux without a network: "
            "`sudo ifconfig lo multicast` and "
            "`sudo route add -net 224.0.0.0 netmask 240.0.0.0 dev lo`. "
            f"Details: {exc}"
        )
