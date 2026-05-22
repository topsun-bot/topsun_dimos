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

"""CrowInput — receives natural-language commands from a running CROW
OrchestratorAgent and forwards them to the dimos agent pipeline via the
/human_input LCM topic.

CROW connects to dimos by calling this module's ``receive`` skill through
the dimos MCP server, replacing the need for a human at the HumanCLI
terminal.

Typical setup:

    # dimos side (no HumanCLI needed)
    dimos run unitree-go2-crow-agentic

    # crow side
    cd ~/Projects/crow && python main.py --dimos http://127.0.0.1:9990/mcp
"""

from dimos.agents.annotation import skill
from dimos.core.core import rpc
from dimos.core.module import Module
from dimos.core.transport import pLCMTransport
from dimos.utils.logging_config import setup_logger

logger = setup_logger()

_HUMAN_INPUT_TOPIC = "/human_input"


class CrowInput(Module):
    """Dimos input module driven by CROW OrchestratorAgent.

    Exposes a ``receive`` MCP skill that CROW calls to inject natural-language
    commands into the dimos agent pipeline.  Functionally equivalent to
    ``WebInput`` or ``HumanCLI``, but designed for autonomous robot-to-robot
    or agent-to-agent operation without human interaction.
    """

    _human_transport: pLCMTransport[str] | None = None

    @rpc
    def start(self) -> None:
        super().start()
        self._human_transport = pLCMTransport(_HUMAN_INPUT_TOPIC)
        logger.info("CrowInput ready — awaiting commands from CROW via MCP skill 'receive'")

    @rpc
    def stop(self) -> None:
        if self._human_transport:
            self._human_transport.lcm.stop()
            self._human_transport = None
        super().stop()

    # ------------------------------------------------------------------
    # MCP skill — called by crow's DimosBridge
    # ------------------------------------------------------------------

    @skill
    def receive(self, text: str) -> str:
        """Forward a CROW command to the dimos agent via /human_input.

        Args:
            text: Natural-language command from CROW OrchestratorAgent.

        Returns:
            Acknowledgement string.
        """
        if not text or not text.strip():
            return "ignored: empty command"

        if self._human_transport is None:
            logger.warning("CrowInput.receive called before start()")
            return "error: CrowInput not started"

        self._human_transport.publish(text.strip())
        logger.info("CrowInput → /human_input: %r", text[:120])
        return f"forwarded: {text[:100]}"
