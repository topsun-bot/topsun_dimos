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

from typing import Any

import requests

from dimos.core.global_config import GlobalConfig

_UNKNOWN = "unknown"
_DEFAULT_TIMEOUT_SEC = 10.0


class HoloAgentBridgeError(RuntimeError):
    """Raised when the HoloAgent robot_bridge HTTP call fails."""


def format_semantic_cmd(floor: str, room: str, object_name: str) -> str:
    """Build the ``cmd`` string used by ``POST /api/semantic_nav``.

    Empty tokens become ``unknown``, matching HoloAgent skill examples such as
    ``unknown,unknown,coffee machine``.
    """
    return ",".join((_token(floor), _token(room), _token(object_name)))


def format_relative_cmd(forward: float, left: float, rotation_deg: float) -> str:
    """Build the ``cmd`` string used by ``POST /api/relative_nav``."""
    return f"{forward},{left},{rotation_deg}"


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
    def from_global_config(
        cls, config: GlobalConfig | None = None
    ) -> HoloAgentBridgeClient:
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
        path_name = name.strip()
        if not path_name:
            raise HoloAgentBridgeError("navigation signal name must be non-empty")
        return self._request("POST", f"/api/navigation/{path_name}")

    def arm_skill(self, skill_name: str) -> dict[str, Any]:
        path_name = skill_name.strip()
        if not path_name:
            raise HoloAgentBridgeError("arm skill name must be non-empty")
        return self._request("POST", f"/api/arm/{path_name}")

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
