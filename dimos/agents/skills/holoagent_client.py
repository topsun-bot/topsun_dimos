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

"""HTTP client for HorizonRobotics HoloAgent ``robot_bridge``.

Payloads match the live bridge contract in
``agentic_robot/services/src/robot_bridge/config/bridge_config.yaml`` and
``agentic_robot/agentOS/holoagent_skills/skills/*/SKILL.md``:

- ``POST /api/semantic_nav`` body ``{"cmd": "floor,room,object"}``
- ``POST /api/relative_nav`` body ``{"cmd": "forward,left,degrees"}``
- ``POST /api/navigation/{name}``
- ``POST /api/navigation/stop``
- ``POST /api/arm/{skill}``
- ``GET /health``

The helper scripts under ``holoagent_skills/skills/*/scripts/`` send a
different JSON shape (structured keys). ``robot_bridge`` reads the ``cmd``
key only, so this client follows the YAML/SKILL.md contract, not those
scripts.
"""

from __future__ import annotations

import math
import re
from typing import Any

import requests

from dimos.core.global_config import GlobalConfig

_UNKNOWN = "unknown"
_DEFAULT_TIMEOUT_SEC = 10.0
# DimOS-side short-adjustment limits. HoloAgent relative_nav turns these
# values into an absolute pose; the upstream skill does not document bounds.
MAX_RELATIVE_DISPLACEMENT_M = 3.0
MAX_RELATIVE_ROTATION_DEG = 180.0
_PATH_TOKEN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


class HoloAgentBridgeError(RuntimeError):
    """Raised when the HoloAgent robot_bridge HTTP call fails."""


def format_semantic_cmd(floor: str, room: str, object_name: str) -> str:
    """Build the ``cmd`` string used by ``POST /api/semantic_nav``.

    Empty floor/room tokens become ``unknown``, matching HoloAgent skill
    examples such as ``unknown,unknown,coffee machine``. ``object_name``
    must be non-empty.
    """
    target = object_name.strip()
    if not target:
        raise HoloAgentBridgeError("semantic_nav object_name must be non-empty")
    return ",".join((_token(floor), _token(room), target))


def format_relative_cmd(forward: float, left: float, rotation_deg: float) -> str:
    """Build the ``cmd`` string used by ``POST /api/relative_nav``."""
    check_relative_nav(forward, left, rotation_deg)
    return f"{forward},{left},{rotation_deg}"


def check_relative_nav(forward: float, left: float, rotation_deg: float) -> None:
    """Reject non-finite, all-zero, or oversized relative-nav commands."""
    for name, value in (
        ("forward", forward),
        ("left", left),
        ("rotation", rotation_deg),
    ):
        if not math.isfinite(value):
            raise HoloAgentBridgeError(f"{name} must be a finite number, got {value!r}")
    if forward == 0.0 and left == 0.0 and rotation_deg == 0.0:
        raise HoloAgentBridgeError("forward, left, and rotation are all zero")
    if abs(forward) > MAX_RELATIVE_DISPLACEMENT_M or abs(left) > MAX_RELATIVE_DISPLACEMENT_M:
        raise HoloAgentBridgeError(
            f"relative displacement exceeds ±{MAX_RELATIVE_DISPLACEMENT_M} m "
            "(short adjustment only; use semantic nav for longer goals)"
        )
    if abs(rotation_deg) > MAX_RELATIVE_ROTATION_DEG:
        raise HoloAgentBridgeError(
            f"relative rotation exceeds ±{MAX_RELATIVE_ROTATION_DEG} degrees "
            "(short adjustment only)"
        )


def safe_path_token(value: str, kind: str) -> str:
    """Return a single URL path token or raise ``HoloAgentBridgeError``."""
    token = value.strip()
    if not token:
        raise HoloAgentBridgeError(f"{kind} name must be non-empty")
    if not _PATH_TOKEN_RE.fullmatch(token):
        raise HoloAgentBridgeError(
            f"{kind} name must be a single token (letters, digits, '.', '_' or '-'), got {token!r}"
        )
    return token


def _token(value: str) -> str:
    stripped = value.strip()
    return stripped if stripped else _UNKNOWN


class HoloAgentBridgeClient:
    """Thin ``requests`` wrapper around HoloAgent ``robot_bridge``."""

    def __init__(
        self,
        base_url: str,
        *,
        timeout_sec: float = _DEFAULT_TIMEOUT_SEC,
        session: requests.Session | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_sec = timeout_sec
        self._session = session or requests.Session()

    @classmethod
    def from_global_config(cls, config: GlobalConfig | None = None) -> HoloAgentBridgeClient:
        if config is None:
            from dimos.core.global_config import global_config

            config = global_config
        return cls(config.holoagent_url)

    def health(self) -> dict[str, Any]:
        return self._request("GET", "/health")

    def semantic_nav(self, floor: str, room: str, object_name: str) -> dict[str, Any]:
        return self._request(
            "POST",
            "/api/semantic_nav",
            {"cmd": format_semantic_cmd(floor, room, object_name)},
        )

    def relative_nav(self, forward: float, left: float, rotation_deg: float) -> dict[str, Any]:
        return self._request(
            "POST",
            "/api/relative_nav",
            {"cmd": format_relative_cmd(forward, left, rotation_deg)},
        )

    def stop_navigation(self) -> dict[str, Any]:
        return self._request("POST", "/api/navigation/stop")

    def navigation_signal(self, name: str) -> dict[str, Any]:
        return self._request(
            "POST", f"/api/navigation/{safe_path_token(name, 'navigation signal')}"
        )

    def arm_skill(self, skill_name: str) -> dict[str, Any]:
        return self._request("POST", f"/api/arm/{safe_path_token(skill_name, 'arm skill')}")

    def _request(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        url = f"{self.base_url}{path}"
        try:
            response = self._session.request(
                method=method,
                url=url,
                json=payload,
                timeout=self.timeout_sec,
            )
            response.raise_for_status()
        except requests.RequestException as exc:
            raise HoloAgentBridgeError(f"{method} {url} failed: {exc}") from exc
        if not response.content:
            return {"success": True}
        try:
            parsed: Any = response.json()
        except ValueError:
            return {"success": True, "text": response.text}
        if isinstance(parsed, dict):
            return parsed
        return {"success": True, "data": parsed}
