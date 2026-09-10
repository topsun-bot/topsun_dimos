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

"""DimOS @skill wrappers for a running HoloAgent robot_bridge.

Prefer native DimOS skills (``navigate_with_text``, ``execute_arm_command``,
``move``) unless a HorizonRobotics HoloAgent stack is actually running and
the request needs that scene graph / robot_bridge path.
"""

from __future__ import annotations

import json
from typing import Any

from dimos.agents.annotation import skill
from dimos.agents.skills.holoagent_client import (
    HoloAgentBridgeClient,
    HoloAgentBridgeError,
    check_relative_nav,
    format_semantic_cmd,
)
from dimos.core.core import rpc
from dimos.core.module import Module
from dimos.utils.logging_config import setup_logger

logger = setup_logger()

HOLOAGENT_SKILLS_PROMPT = """
# HoloAgent robot_bridge (optional)
Use these skills only when a HorizonRobotics HoloAgent ``robot_bridge`` is
running (default ``http://127.0.0.1:8000``) and the user wants that stack's
floor/room/object scene-graph navigation or HoloAgent arm FIFO skills.

- `holoagent_semantic_nav` — FSR-VLN / HMSG semantic goal via `/api/semantic_nav`
- `holoagent_relative_move` — short relative pose via `/api/relative_nav`
- `holoagent_stop_nav` — stop HoloAgent navigation
- `holoagent_arm` — G1 arm gesture names from HoloAgent `g1_arm` (e.g. wave_above_head)
- `holoagent_health` — check that robot_bridge is reachable

Otherwise use native DimOS `navigate_with_text`, `move`, and `execute_arm_command`.
"""


def _format_bridge_result(action: str, result: dict[str, Any]) -> str:
    return f"HoloAgent {action} ok: {json.dumps(result, ensure_ascii=False)}"


class HoloAgentNavSkillContainer(Module):
    """HoloAgent robot_bridge navigation skills (Go2 and G1)."""

    _client: HoloAgentBridgeClient | None = None

    @rpc
    def start(self) -> None:
        super().start()
        self._client = HoloAgentBridgeClient.from_global_config(self.config.g)

    @rpc
    def stop(self) -> None:
        self._client = None
        super().stop()

    def _bridge(self) -> HoloAgentBridgeClient:
        if self._client is None:
            self._client = HoloAgentBridgeClient.from_global_config(self.config.g)
        return self._client

    @skill
    def holoagent_health(self) -> str:
        """Check whether a HoloAgent robot_bridge is reachable.

        Calls GET /health on this module's GlobalConfig.holoagent_url
        (CLI ``--holoagent-url``, default http://127.0.0.1:8000). Use this
        before other holoagent_* skills.

        Returns:
            Bridge status JSON, or an error string if the bridge is down.
        """
        try:
            result = self._bridge().health()
        except HoloAgentBridgeError as exc:
            logger.warning("HoloAgent health check failed: %s", exc)
            return f"HoloAgent robot_bridge not reachable: {exc}"
        return _format_bridge_result("health", result)

    @skill
    def holoagent_semantic_nav(
        self,
        object_name: str,
        floor: str = "unknown",
        room: str = "unknown",
    ) -> str:
        """Navigate via HoloAgent FSR-VLN / HMSG semantic map.

        Sends POST /api/semantic_nav with {"cmd": "floor,room,object"} as
        defined by HoloAgent robot_bridge and sem-nav-skill. Use only when
        that stack is running. For native DimOS spatial memory, use
        navigate_with_text instead.

        Args:
            object_name: Target object, e.g. "coffee machine" or "charging station".
            floor: Floor label such as "1F", or "unknown" if not specified.
            room: Room name such as "pantry" or "meeting room", or "unknown".
        """
        try:
            format_semantic_cmd(floor, room, object_name)
        except HoloAgentBridgeError as exc:
            return f"HoloAgent semantic_nav refused: {exc}"
        try:
            result = self._bridge().semantic_nav(floor, room, object_name)
        except HoloAgentBridgeError as exc:
            logger.warning("HoloAgent semantic_nav failed: %s", exc)
            return f"HoloAgent semantic_nav failed: {exc}"
        return _format_bridge_result(
            f"semantic_nav({floor},{room},{object_name})",
            result,
        )

    @skill
    def holoagent_relative_move(
        self,
        forward: float = 0.0,
        left: float = 0.0,
        rotation: float = 0.0,
    ) -> str:
        """Send a short relative move to HoloAgent robot_bridge.

        Sends POST /api/relative_nav with {"cmd": "forward,left,degrees"} as
        defined by HoloAgent rel-move-skill. Use for small adjustments on a
        running HoloAgent nav stack. For native DimOS velocity control, use
        move instead.

        Args:
            forward: Forward displacement in meters. Negative is backward.
                Magnitude must be finite and at most
                ``MAX_RELATIVE_DISPLACEMENT_M`` (3.0 m).
            left: Left displacement in meters. Negative is right. Same bound.
            rotation: Heading change in degrees. Positive is left/CCW.
                Magnitude must be finite and at most
                ``MAX_RELATIVE_ROTATION_DEG`` (180).
        """
        try:
            check_relative_nav(forward, left, rotation)
        except HoloAgentBridgeError as exc:
            return f"HoloAgent relative_move refused: {exc}"
        try:
            result = self._bridge().relative_nav(forward, left, rotation)
        except HoloAgentBridgeError as exc:
            logger.warning("HoloAgent relative_nav failed: %s", exc)
            return f"HoloAgent relative_move failed: {exc}"
        return _format_bridge_result(
            f"relative_nav({forward},{left},{rotation})",
            result,
        )

    @skill
    def holoagent_stop_nav(self) -> str:
        """Stop the current HoloAgent navigation goal.

        Sends POST /api/navigation/stop (robot_bridge → chat_signal_pub "stop").
        Does not stop native DimOS navigation; use stop_all_motion for that.
        """
        try:
            result = self._bridge().stop_navigation()
        except HoloAgentBridgeError as exc:
            logger.warning("HoloAgent stop_nav failed: %s", exc)
            return f"HoloAgent stop_nav failed: {exc}"
        return _format_bridge_result("stop_nav", result)

    @skill
    def holoagent_navigation_signal(self, name: str) -> str:
        """Trigger a named HoloAgent navigation signal.

        Sends POST /api/navigation/{name} (robot_bridge → chat_signal_pub).
        Examples from HoloAgent robot-service skill: one_point_1, stop.

        Args:
            name: Signal name such as "one_point_1" or "stop".
        """
        try:
            result = self._bridge().navigation_signal(name)
        except HoloAgentBridgeError as exc:
            logger.warning("HoloAgent navigation signal failed: %s", exc)
            return f"HoloAgent navigation signal failed: {exc}"
        return _format_bridge_result(f"navigation_signal({name})", result)


class HoloAgentSkillContainer(HoloAgentNavSkillContainer):
    """Navigation skills plus G1 HoloAgent arm FIFO skills."""

    @skill
    def holoagent_arm(self, skill_name: str) -> str:
        """Trigger a HoloAgent G1 arm skill through robot_bridge.

        Sends POST /api/arm/{skill_name}. Names come from HoloAgent
        robots/unitree/src/g1_arm (e.g. wave_above_head, wave_under_head,
        shake_hand, hug, high_five, release_arm). Prefer native
        execute_arm_command when controlling G1 through DimOS WebRTC.

        Args:
            skill_name: HoloAgent arm skill path token, e.g. "wave_above_head".
        """
        try:
            result = self._bridge().arm_skill(skill_name)
        except HoloAgentBridgeError as exc:
            logger.warning("HoloAgent arm skill failed: %s", exc)
            return f"HoloAgent arm skill failed: {exc}"
        return _format_bridge_result(f"arm({skill_name})", result)
