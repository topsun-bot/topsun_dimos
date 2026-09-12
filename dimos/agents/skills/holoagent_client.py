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
from typing import Any, ClassVar

import requests

from dimos.core.global_config import GlobalConfig, global_config

_DEFAULT_TIMEOUT_SEC = 10.0
# Worker/CLI teardown is about 5s. A hung bridge must not outlive that.
SHUTDOWN_TIMEOUT_SEC = 2.0


class HoloAgentBridgeError(RuntimeError):
    """Raised when the HoloAgent robot_bridge HTTP call fails."""


class HoloAgentBridgeContract:
    """Validate and format HoloAgent ``robot_bridge`` request payloads."""

    UNKNOWN: ClassVar[str] = "unknown"
    # DimOS-side short-adjustment limits. HoloAgent relative_nav turns these
    # values into an absolute pose; the upstream skill does not document bounds.
    MAX_RELATIVE_DISPLACEMENT_M: ClassVar[float] = 3.0
    MAX_RELATIVE_ROTATION_DEG: ClassVar[float] = 180.0
    _PATH_TOKEN_RE: ClassVar[re.Pattern[str]] = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")

    @staticmethod
    def format_semantic_cmd(floor: str, room: str, object_name: str) -> str:
        """Build the ``cmd`` string used by ``POST /api/semantic_nav``.

        Empty floor/room tokens become ``unknown``, matching HoloAgent skill
        examples such as ``unknown,unknown,coffee machine``. ``object_name``
        must be non-empty.
        """
        target = HoloAgentBridgeContract._semantic_field(object_name, "object_name")
        if not target:
            raise HoloAgentBridgeError("semantic_nav object_name must be non-empty")
        return ",".join(
            (
                HoloAgentBridgeContract._token(floor, "floor"),
                HoloAgentBridgeContract._token(room, "room"),
                target,
            )
        )

    @staticmethod
    def format_relative_cmd(forward: float, left: float, rotation_deg: float) -> str:
        """Build the ``cmd`` string used by ``POST /api/relative_nav``."""
        HoloAgentBridgeContract.check_relative_nav(forward, left, rotation_deg)
        return f"{forward},{left},{rotation_deg}"

    @staticmethod
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
        max_disp = HoloAgentBridgeContract.MAX_RELATIVE_DISPLACEMENT_M
        if abs(forward) > max_disp or abs(left) > max_disp:
            raise HoloAgentBridgeError(
                f"relative displacement exceeds ±{max_disp} m "
                "(short adjustment only; use semantic nav for longer goals)"
            )
        max_rot = HoloAgentBridgeContract.MAX_RELATIVE_ROTATION_DEG
        if abs(rotation_deg) > max_rot:
            raise HoloAgentBridgeError(
                f"relative rotation exceeds ±{max_rot} degrees (short adjustment only)"
            )

    @staticmethod
    def safe_path_token(value: str, kind: str) -> str:
        """Return a single URL path token or raise ``HoloAgentBridgeError``."""
        token = value.strip()
        if not token:
            raise HoloAgentBridgeError(f"{kind} name must be non-empty")
        if token in {".", ".."}:
            raise HoloAgentBridgeError(f"{kind} name must not be '.' or '..'")
        if not HoloAgentBridgeContract._PATH_TOKEN_RE.fullmatch(token):
            raise HoloAgentBridgeError(
                f"{kind} name must be a single token "
                f"(letters, digits, '.', '_' or '-'), got {token!r}"
            )
        return token

    @staticmethod
    def _semantic_field(value: str, field: str) -> str:
        stripped = value.strip()
        if "," in stripped:
            raise HoloAgentBridgeError(
                f"semantic_nav {field} must not contain commas (bridge uses ',' as the field separator)"
            )
        return stripped

    @staticmethod
    def _token(value: str, field: str) -> str:
        stripped = HoloAgentBridgeContract._semantic_field(value, field)
        return stripped if stripped else HoloAgentBridgeContract.UNKNOWN


class HoloAgentBridgeClient:
    """Thin ``requests`` wrapper around HoloAgent ``robot_bridge``.

    ``requests`` is a core runtime dependency (``[project].dependencies`` in
    ``pyproject.toml``), not Docker-extra-only.
    """

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
            config = global_config
        return cls(config.holoagent_url)

    def health(self) -> dict[str, Any]:
        return self._request("GET", "/health")

    def semantic_nav(self, floor: str, room: str, object_name: str) -> dict[str, Any]:
        return self._request(
            "POST",
            "/api/semantic_nav",
            {"cmd": HoloAgentBridgeContract.format_semantic_cmd(floor, room, object_name)},
        )

    def relative_nav(self, forward: float, left: float, rotation_deg: float) -> dict[str, Any]:
        return self._request(
            "POST",
            "/api/relative_nav",
            {"cmd": HoloAgentBridgeContract.format_relative_cmd(forward, left, rotation_deg)},
        )

    def stop_navigation(self, *, timeout_sec: float | None = None) -> dict[str, Any]:
        return self._request("POST", "/api/navigation/stop", timeout_sec=timeout_sec)

    def navigation_signal(self, name: str) -> dict[str, Any]:
        token = HoloAgentBridgeContract.safe_path_token(name, "navigation signal")
        return self._request("POST", f"/api/navigation/{token}")

    def arm_skill(self, skill_name: str) -> dict[str, Any]:
        """POST /api/arm/{skill} as in bridge_config.yaml.

        HoloAgent ``g1_arm`` ``getcmd.cpp`` subscribes to ``chat_signal_pub``,
        not ``arm_signal_pub``. This method is kept for the documented HTTP
        contract and is not exposed as an LLM ``@skill``.
        """
        token = HoloAgentBridgeContract.safe_path_token(skill_name, "arm skill")
        return self._request("POST", f"/api/arm/{token}")

    def close(self) -> None:
        self._session.close()

    def _request(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
        *,
        timeout_sec: float | None = None,
    ) -> dict[str, Any]:
        url = f"{self.base_url}{path}"
        timeout = self.timeout_sec if timeout_sec is None else timeout_sec
        try:
            response = self._session.request(
                method=method,
                url=url,
                json=payload,
                timeout=timeout,
            )
            response.raise_for_status()
            content = response.content
            if not content:
                return {"success": True}
            try:
                parsed: Any = response.json()
            except ValueError:
                return {"success": True, "text": response.text}
        except requests.RequestException as exc:
            raise HoloAgentBridgeError(f"{method} {url} failed: {exc}") from exc
        if isinstance(parsed, dict):
            return parsed
        return {"success": True, "data": parsed}
