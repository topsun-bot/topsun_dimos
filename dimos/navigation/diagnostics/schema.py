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

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal, TypeAlias

from dimos.core.global_config import NavigationTraceLevel

NAVIGATION_TRACE_SCHEMA_VERSION = "1.0"

TraceProducer: TypeAlias = Literal[
    "planner",
    "mux",
    "connection",
    "costmapper",
    "relocalization",
    "recharge",
]
NavigationTerminal: TypeAlias = Literal[
    "arrived",
    "cancelled",
    "failed",
    "superseded",
]
BlobKind: TypeAlias = Literal["costmap", "pointcloud"]

TRACE_LEVEL_ORDER: tuple[NavigationTraceLevel, ...] = (
    "off",
    "summary",
    "full",
    "forensic",
)


def trace_level_at_least(
    level: NavigationTraceLevel,
    minimum: NavigationTraceLevel,
) -> bool:
    """Return whether level contains all events from minimum."""
    return TRACE_LEVEL_ORDER.index(level) >= TRACE_LEVEL_ORDER.index(minimum)


def utc_wall_timestamp() -> str:
    """Return an ISO-8601 UTC timestamp with millisecond precision."""
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


@dataclass(frozen=True, slots=True)
class SessionContext:
    """Immutable correlation fields for one planner event."""

    navigation_session_id: str
    session_event_seq: int
    plan_version: int


@dataclass(frozen=True, slots=True)
class SessionTransition:
    """A session lifecycle event ready to be passed to a TraceSink."""

    event: Literal["navigation_session_started", "navigation_session_ended"]
    context: SessionContext
    entry_source: str
    terminal: NavigationTerminal | None = None
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class PlanContext:
    """Correlation fields for one global-plan version."""

    navigation_session_id: str
    session_event_seq: int
    plan_version: int
    plan_reason: str
