# Copyright 2026 Dimensional Inc.
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

from __future__ import annotations

import sqlite3
from typing import Any

import pytest

from dimos.memory2.module import Recorder


class _ClosedStream:
    def append(self, *_args: Any, **_kwargs: Any) -> None:
        raise sqlite3.ProgrammingError("Cannot operate on a closed database.")


def test_recorder_ignores_closed_database_only_during_teardown() -> None:
    recorder = Recorder()
    try:
        recorder._stopping = True

        recorder._append_observation(
            _ClosedStream(),  # type: ignore[arg-type]
            object(),
            ts=1.0,
            pose=None,
            tags={},
        )

        recorder._stopping = False
        with pytest.raises(sqlite3.ProgrammingError, match="closed database"):
            recorder._append_observation(
                _ClosedStream(),  # type: ignore[arg-type]
                object(),
                ts=1.0,
                pose=None,
                tags={},
            )
    finally:
        recorder.stop()
