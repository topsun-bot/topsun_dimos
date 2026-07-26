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

from collections.abc import Callable
from datetime import datetime, timezone

from dimos.navigation.diagnostics.schema import (
    NavigationTerminal,
    PlanContext,
    SessionContext,
    SessionTransition,
)


class NavigationSessionTracker:
    """Track planner goal lifecycles without changing navigation messages."""

    def __init__(
        self,
        *,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._now = now or (lambda: datetime.now(timezone.utc))
        self._counter = 0
        self._active_id: str | None = None
        self._entry_source: str | None = None
        self._event_seq = 0
        self._plan_version = 0

    @property
    def active(self) -> bool:
        return self._active_id is not None

    @property
    def current(self) -> SessionContext | None:
        if self._active_id is None:
            return None
        return SessionContext(
            navigation_session_id=self._active_id,
            session_event_seq=self._event_seq,
            plan_version=self._plan_version,
        )

    def begin(self, entry_source: str) -> tuple[SessionTransition, ...]:
        """Begin an external goal, superseding any unfinished goal."""
        transitions: list[SessionTransition] = []
        previous = self.end("superseded", reason="new_external_goal")
        if previous is not None:
            transitions.append(previous)

        self._counter += 1
        timestamp = self._now().astimezone(timezone.utc)
        suffix = timestamp.strftime("%Y%m%dT%H%M%S.") + f"{timestamp.microsecond // 1000:03d}"
        self._active_id = f"nav-{self._counter:04d}-{suffix}"
        self._entry_source = entry_source
        self._event_seq = 1
        self._plan_version = 0
        transitions.append(
            SessionTransition(
                event="navigation_session_started",
                context=self._required_context(),
                entry_source=entry_source,
            )
        )
        return tuple(transitions)

    def next_plan(self, reason: str) -> PlanContext | None:
        """Advance the plan version for the active session."""
        if self._active_id is None:
            return None
        self._event_seq += 1
        self._plan_version += 1
        return PlanContext(
            navigation_session_id=self._active_id,
            session_event_seq=self._event_seq,
            plan_version=self._plan_version,
            plan_reason=reason,
        )

    def next_event(self) -> SessionContext | None:
        """Reserve the next event sequence in the active session."""
        if self._active_id is None:
            return None
        self._event_seq += 1
        return self._required_context()

    def end(
        self,
        terminal: NavigationTerminal,
        *,
        reason: str | None = None,
    ) -> SessionTransition | None:
        """Close the active session, returning None when already idle."""
        if self._active_id is None:
            return None
        self._event_seq += 1
        transition = SessionTransition(
            event="navigation_session_ended",
            context=self._required_context(),
            entry_source=self._entry_source or "unknown",
            terminal=terminal,
            reason=reason,
        )
        self._active_id = None
        self._entry_source = None
        self._event_seq = 0
        self._plan_version = 0
        return transition

    def _required_context(self) -> SessionContext:
        if self._active_id is None:
            raise RuntimeError("navigation session is not active")
        return SessionContext(
            navigation_session_id=self._active_id,
            session_event_seq=self._event_seq,
            plan_version=self._plan_version,
        )
