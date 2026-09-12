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
from dimos.agents.capabilities import CAP_MOVEMENT
from dimos.agents.skills.holoagent_client import (
    HoloAgentBridgeClient,
    HoloAgentBridgeContract,
    HoloAgentBridgeError,
)
from dimos.core.core import rpc
from dimos.core.module import Module
from dimos.utils.logging_config import setup_logger

logger = setup_logger()

# Shared tool-stream name so holoagent_stop_nav can release whichever
# HoloAgent nav skill currently holds CAP_MOVEMENT.
_HOLOAGENT_NAV_TOOL = "holoagent_nav"

HOLOAGENT_SKILLS_PROMPT = """
# HoloAgent robot_bridge (optional)
Use these skills only when a HorizonRobotics HoloAgent ``robot_bridge`` is
running (default ``http://127.0.0.1:8000``) and the user wants that stack's
floor/room/object scene-graph navigation.

These calls publish to the bridge and return when HTTP is accepted. They do
not wait for the robot to reach a goal. They hold the `movement` capability
until `holoagent_stop_nav` (or `holoagent_navigation_signal` with name
`stop`). Do not start another movement skill until then.

- `holoagent_semantic_nav` — FSR-VLN / HMSG semantic goal via `/api/semantic_nav`
- `holoagent_relative_move` — short relative pose via `/api/relative_nav`
- `holoagent_stop_nav` — stop HoloAgent navigation
- `holoagent_health` — check that robot_bridge is reachable

When the user asks to stop, halt, or cancel motion, call `holoagent_stop_nav`
and native `stop_all_motion`. Native `stop_all_motion` does not cancel a
HoloAgent robot_bridge goal.

HoloAgent `/api/arm/{skill}` is not exposed: it publishes `arm_signal_pub`,
but `g1_arm` `getcmd.cpp` only subscribes to `chat_signal_pub`. Prefer native
`execute_arm_command`. FIFO names such as `wave_above_head` can be sent with
`holoagent_navigation_signal` (that path maps to `chat_signal_pub`).

Otherwise use native DimOS `navigate_with_text`, `move`, and `execute_arm_command`.
"""


def _format_bridge_result(action: str, result: dict[str, Any]) -> str:
    return (
        f"HoloAgent {action} published (bridge accepted; not waiting for "
        f"arrival): {json.dumps(result, ensure_ascii=False)}"
    )


class HoloAgentNavSkillContainer(Module):
    """HoloAgent robot_bridge navigation skills (Go2 and G1)."""

    _client: HoloAgentBridgeClient | None = None

    @rpc
    def start(self) -> None:
        super().start()
        self._client = HoloAgentBridgeClient.from_global_config(self.config.g)

    @rpc
    def stop(self) -> None:
        self._stop_bridge_best_effort()
        super().stop()

    def _bridge(self) -> HoloAgentBridgeClient:
        if self._client is None:
            self._client = HoloAgentBridgeClient.from_global_config(self.config.g)
        return self._client

    def _begin_nav_hold(self) -> None:
        self.start_tool(_HOLOAGENT_NAV_TOOL)

    def _release_nav_hold(self) -> None:
        self.stop_tool(_HOLOAGENT_NAV_TOOL)

    def _stop_bridge_best_effort(self) -> None:
        """POST /api/navigation/stop if a client exists, then drop it.

        Shutdown still proceeds if the bridge call fails. ``super().stop()``
        errors are not swallowed.
        """
        client = self._client
        if client is not None:
            try:
                client.stop_navigation()
            except HoloAgentBridgeError as exc:
                logger.warning("HoloAgent bridge stop during shutdown failed: %s", exc)
            except Exception:
                logger.exception("HoloAgent bridge stop during shutdown failed")
        self._release_nav_hold()
        self._client = None

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
            return f"HoloAgent health check failed: {exc}"
        return f"HoloAgent robot_bridge health: {json.dumps(result, ensure_ascii=False)}"

    @skill(uses=[CAP_MOVEMENT], lifecycle="background")
    def holoagent_semantic_nav(
        self,
        object_name: str,
        floor: str = "unknown",
        room: str = "unknown",
    ) -> str:
        """Navigate via HoloAgent FSR-VLN / HMSG semantic map.

        Sends POST /api/semantic_nav with {"cmd": "floor,room,object"} as
        defined by HoloAgent robot_bridge and sem-nav-skill. The bridge
        publishes to ROS and returns immediately; this skill does not wait
        for arrival. Holds CAP_MOVEMENT until holoagent_stop_nav. For native
        DimOS spatial memory, use navigate_with_text instead.

        Args:
            object_name: Target object, e.g. "coffee machine" or "charging station".
            floor: Floor label such as "1F", or "unknown" if not specified.
            room: Room name such as "pantry" or "meeting room", or "unknown".
        """
        self._begin_nav_hold()
        keep_hold = False
        try:
            try:
                HoloAgentBridgeContract.format_semantic_cmd(floor, room, object_name)
            except HoloAgentBridgeError as exc:
                return f"HoloAgent semantic_nav refused: {exc}"
            try:
                result = self._bridge().semantic_nav(floor, room, object_name)
            except HoloAgentBridgeError as exc:
                logger.warning("HoloAgent semantic_nav failed: %s", exc)
                return f"HoloAgent semantic_nav failed: {exc}"
            keep_hold = True
            return _format_bridge_result(
                f"semantic_nav({floor},{room},{object_name})",
                result,
            )
        finally:
            if not keep_hold:
                self._release_nav_hold()

    @skill(uses=[CAP_MOVEMENT], lifecycle="background")
    def holoagent_relative_move(
        self,
        forward: float = 0.0,
        left: float = 0.0,
        rotation: float = 0.0,
    ) -> str:
        """Send a short relative move to HoloAgent robot_bridge.

        Sends POST /api/relative_nav with {"cmd": "forward,left,degrees"} as
        defined by HoloAgent rel-move-skill. The bridge publishes and
        returns immediately; this skill does not wait for arrival. Holds
        CAP_MOVEMENT until holoagent_stop_nav. For native DimOS velocity
        control, use move instead.

        Args:
            forward: Forward displacement in meters. Negative is backward.
                Magnitude must be finite and at most
                ``HoloAgentBridgeContract.MAX_RELATIVE_DISPLACEMENT_M`` (3.0 m).
            left: Left displacement in meters. Negative is right. Same bound.
            rotation: Heading change in degrees. Positive is left/CCW.
                Magnitude must be finite and at most
                ``HoloAgentBridgeContract.MAX_RELATIVE_ROTATION_DEG`` (180).
        """
        self._begin_nav_hold()
        keep_hold = False
        try:
            try:
                HoloAgentBridgeContract.check_relative_nav(forward, left, rotation)
            except HoloAgentBridgeError as exc:
                return f"HoloAgent relative_move refused: {exc}"
            try:
                result = self._bridge().relative_nav(forward, left, rotation)
            except HoloAgentBridgeError as exc:
                logger.warning("HoloAgent relative_nav failed: %s", exc)
                return f"HoloAgent relative_move failed: {exc}"
            keep_hold = True
            return _format_bridge_result(
                f"relative_nav({forward},{left},{rotation})",
                result,
            )
        finally:
            if not keep_hold:
                self._release_nav_hold()

    @skill
    def holoagent_stop_nav(self) -> str:
        """Stop the current HoloAgent navigation goal.

        Sends POST /api/navigation/stop (robot_bridge → chat_signal_pub "stop")
        and releases the CAP_MOVEMENT hold. Does not stop native DimOS
        navigation; use stop_all_motion for that.
        """
        try:
            result = self._bridge().stop_navigation()
        except HoloAgentBridgeError as exc:
            logger.warning("HoloAgent stop_nav failed: %s", exc)
            return f"HoloAgent stop_nav failed: {exc}"
        self._release_nav_hold()
        return _format_bridge_result("stop_nav", result)

    @skill(uses=[CAP_MOVEMENT], lifecycle="background")
    def holoagent_navigation_signal(self, name: str) -> str:
        """Trigger a named HoloAgent navigation signal.

        Sends POST /api/navigation/{name} (robot_bridge → chat_signal_pub).
        Examples from HoloAgent robot-service skill: one_point_1, stop.
        Name ``stop`` releases the CAP_MOVEMENT hold; other names hold it
        until holoagent_stop_nav.

        Args:
            name: Signal name such as "one_point_1" or "stop".
        """
        self._begin_nav_hold()
        keep_hold = False
        try:
            try:
                result = self._bridge().navigation_signal(name)
            except HoloAgentBridgeError as exc:
                logger.warning("HoloAgent navigation signal failed: %s", exc)
                return f"HoloAgent navigation signal failed: {exc}"
            keep_hold = name.strip() != "stop"
            return _format_bridge_result(f"navigation_signal({name})", result)
        finally:
            if not keep_hold:
                self._release_nav_hold()


class HoloAgentSkillContainer(HoloAgentNavSkillContainer):
    """Same skills as ``HoloAgentNavSkillContainer``.

    ``holoagent_arm`` is not registered. HoloAgent
    ``bridge_config.yaml`` maps ``POST /api/arm/{skill}`` to
    ``arm_signal_pub``, but ``robots/unitree/src/g1_arm/src/getcmd.cpp``
    only subscribes to ``chat_signal_pub`` (plus ``waypoint_reached`` and
    ``motion_tracking``). There is no ``arm_signal_pub`` consumer, so the
    HTTP call can return success without writing ``/tmp/arm_fifo``.
    """
