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

from __future__ import annotations

import json
from pathlib import Path
import sys
from unittest.mock import MagicMock

import pytest

from dimos.navigation.diagnostics import static_capture


def test_interrupted_capture_still_writes_partial_evidence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    transports = [MagicMock() for _ in range(5)]
    monkeypatch.setattr(static_capture, "make_transport", lambda *_args: transports.pop(0))
    monkeypatch.setattr(static_capture, "_resource_sample", lambda _previous: None)
    monkeypatch.setattr(static_capture.time, "sleep", MagicMock(side_effect=KeyboardInterrupt))
    output = tmp_path / "partial.json"

    with pytest.raises(KeyboardInterrupt):
        static_capture.capture_stationary(output, duration_sec=1.0)

    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["interrupted"] is True
    assert payload["read_only"] is True
    assert "capture_ended_monotonic_ns" in payload


def test_stationary_gate_distinguishes_complete_and_interrupted_capture() -> None:
    base = {
        "capture_started_monotonic_ns": 0,
        "capture_ended_monotonic_ns": 600_000_000_000,
        "interrupted": False,
        "odom": [{"x": 1.0, "y": 2.0}, {"x": 1.01, "y": 1.99}],
        "global_map_rx_ns": [1],
        "costmap_rx_ns": [1],
        "nav_cmd_vel": [],
        "mux_cmd_vel": [],
    }
    assert static_capture.evaluate_stationary_capture(base)["passed"] is True

    interrupted = {**base, "interrupted": True}
    result = static_capture.evaluate_stationary_capture(interrupted)
    assert result["passed"] is False
    assert result["checks"]["not_interrupted"] is False


def test_cli_check_reports_interrupted_capture(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    output = tmp_path / "partial.json"
    payload = {
        "capture_started_monotonic_ns": 0,
        "capture_ended_monotonic_ns": 1,
        "interrupted": True,
        "odom": [],
        "global_map_rx_ns": [],
        "costmap_rx_ns": [],
        "nav_cmd_vel": [],
        "mux_cmd_vel": [],
    }
    output.write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setattr(
        static_capture,
        "capture_stationary",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(KeyboardInterrupt),
    )
    monkeypatch.setattr(
        sys,
        "argv",
        ["static_capture", str(output), "--check"],
    )

    with pytest.raises(SystemExit) as exc_info:
        static_capture.main()

    assert exc_info.value.code == 2
    assert '"cli_interrupted": true' in capsys.readouterr().out
