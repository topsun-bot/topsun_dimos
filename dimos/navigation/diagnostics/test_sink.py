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

import json
from pathlib import Path
import queue
import threading
import time
from typing import Any

import numpy as np
import pytest

from dimos.core.global_config import GlobalConfig
from dimos.navigation.diagnostics.sink import TraceSink, redact_sensitive


def _config(level: str, **overrides: Any) -> GlobalConfig:
    settings = {
        "navigation_trace_level": level,
        "navigation_trace_min_free_disk_bytes": 0,
        **overrides,
    }
    return GlobalConfig(
        **settings,
    )


def _read_events(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_off_creates_no_resources_directory_or_clock_reads(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_clock() -> int:
        raise AssertionError("trace off must not read clocks")

    monkeypatch.setattr(time, "monotonic_ns", fail_clock)
    run_dir = tmp_path / "run"
    sink = TraceSink("planner", config=_config("off"), run_log_dir=run_dir)

    assert not sink.enabled
    assert not sink.has_background_resources
    assert sink.output_path is None
    assert not sink.record("must_not_allocate", {"value": 1})
    sink.close()
    assert not run_dir.exists()


def test_scalar_jsonl_and_blob_index_are_parseable(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    sink = TraceSink("planner", config=_config("full"), run_log_dir=run_dir)
    assert sink.record("goal", {"x": np.float64(1.25)})
    assert sink.record_blob(
        "costmap",
        np.arange(12, dtype=np.uint8).reshape(3, 4),
        {"costmap_id": "costmap-1"},
        stem="costmap",
    )

    sink.close()

    assert sink.output_path is not None
    events = _read_events(sink.output_path)
    assert [event["event"] for event in events] == [
        "trace_header",
        "goal",
        "blob_saved",
        "trace_footer",
    ]
    blob_event = events[2]
    blob_path = run_dir / "navigation" / blob_event["blob_path"]
    assert np.array_equal(np.load(blob_path, allow_pickle=False), np.arange(12).reshape(3, 4))


def test_json_artifact_is_written_in_background_and_indexed(tmp_path: Path) -> None:
    sink = TraceSink("planner", config=_config("summary"), run_log_dir=tmp_path)
    accepted = sink.record_json_artifact(
        Path("plans/nav-0001-plan-0001-raw.json"),
        {"poses": [{"x": 1.0, "y": 2.0}]},
        {"navigation_session_id": "nav-0001", "plan_version": 1},
        estimated_bytes=256,
    )

    sink.close()

    assert accepted
    assert sink.output_path is not None
    events = _read_events(sink.output_path)
    index = next(event for event in events if event["event"] == "json_artifact_saved")
    artifact = tmp_path / "navigation" / index["artifact_path"]
    assert json.loads(artifact.read_text(encoding="utf-8"))["poses"] == [{"x": 1.0, "y": 2.0}]


def test_json_artifact_factory_runs_only_in_writer_thread(tmp_path: Path) -> None:
    sink = TraceSink("planner", config=_config("summary"), run_log_dir=tmp_path)
    caller_thread = threading.get_ident()
    factory_threads: list[int] = []

    def make_payload() -> dict[str, object]:
        factory_threads.append(threading.get_ident())
        return {"poses": [{"x": 1.0, "y": 2.0}]}

    assert sink.record_json_artifact(
        Path("plans/nav-0001-plan-0001-smoothed.json"),
        make_payload,
        {"navigation_session_id": "nav-0001", "plan_version": 1},
        estimated_bytes=256,
        redact_payload=False,
    )
    sink.close()

    assert factory_threads
    assert all(thread_id != caller_thread for thread_id in factory_threads)


def test_forensic_pointcloud_roi_is_processed_by_writer(tmp_path: Path) -> None:
    sink = TraceSink(
        "connection",
        config=_config("forensic", navigation_trace_forensic_ack=True),
        run_log_dir=tmp_path,
    )
    points = np.array(
        [
            [0.0, 0.0, 0.0],
            [0.01, 0.01, 0.0],
            [6.0, 0.0, 0.0],
            [np.nan, 0.0, 0.0],
        ],
        dtype=np.float64,
    )

    assert sink.record_blob(
        "pointcloud",
        points,
        {
            "roi_bounds_m": [-5.0, 5.0, -5.0, 5.0, -2.0, 2.0],
            "voxel_size_m": 0.1,
        },
        stem="pointcloud-roi",
    )
    sink.close()

    assert sink.output_path is not None
    event = next(
        event for event in _read_events(sink.output_path) if event["event"] == "blob_saved"
    )
    saved = np.load(
        tmp_path / "navigation" / event["blob_path"],
        allow_pickle=False,
    )
    assert saved.shape == (1, 3)
    assert saved.dtype == np.float32
    assert event["source_nbytes"] == points.nbytes
    assert event["metadata"]["roi_processing_thread"] == "trace_writer"
    assert event["metadata"]["source_point_count"] == 4


def test_forensic_requires_explicit_ack_and_allocates_nothing(tmp_path: Path) -> None:
    sink = TraceSink("planner", config=_config("forensic"), run_log_dir=tmp_path)

    assert not sink.enabled
    assert not sink.has_background_resources
    assert sink.writer_error == "forensic_ack_required"
    assert not (tmp_path / "navigation").exists()


def test_scalar_queue_full_is_nonblocking_and_degrades(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sink = TraceSink(
        "planner",
        config=_config("full", navigation_trace_scalar_queue_items=1),
        run_log_dir=tmp_path,
    )
    assert sink._scalar_queue is not None

    def always_full(item: Any) -> None:
        raise queue.Full

    monkeypatch.setattr(sink._scalar_queue, "put_nowait", always_full)
    started = time.perf_counter()
    accepted = sink.record("control", {"linear_x": 0.1})
    elapsed = time.perf_counter() - started

    assert not accepted
    assert elapsed < 0.01
    assert sink.effective_level == "summary"
    sink.close()


def test_sink_exception_is_isolated_from_caller(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sink = TraceSink("planner", config=_config("full"), run_log_dir=tmp_path)
    assert sink._scalar_queue is not None

    def explode(item: Any) -> None:
        raise OSError("injected queue failure")

    monkeypatch.setattr(sink._scalar_queue, "put_nowait", explode)

    assert not sink.record("control", {"linear_x": 0.1})
    assert not sink.enabled
    assert sink.effective_level == "off"
    assert sink.writer_error is not None
    sink.close()


def test_unwritable_directory_disables_sink_without_raising(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_dir = tmp_path / "unwritable"
    navigation_dir = run_dir / "navigation"
    original_mkdir = Path.mkdir

    def reject_navigation_dir(
        path: Path,
        mode: int = 0o777,
        parents: bool = False,
        exist_ok: bool = False,
    ) -> None:
        if path == navigation_dir:
            raise PermissionError("injected unwritable directory")
        original_mkdir(path, mode=mode, parents=parents, exist_ok=exist_ok)

    monkeypatch.setattr(Path, "mkdir", reject_navigation_dir)

    sink = TraceSink("planner", config=_config("full"), run_log_dir=run_dir)

    assert not sink.enabled
    assert sink.effective_level == "off"
    assert sink.writer_error == "PermissionError: injected unwritable directory"
    assert not sink.record("control", {"linear_x": 0.1})


def test_writer_exception_disables_sink_without_raising(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def explode(_stream: Any, _payload: Any) -> None:
        raise OSError("injected writer failure")

    monkeypatch.setattr(TraceSink, "_write_json_line", staticmethod(explode))

    sink = TraceSink("planner", config=_config("full"), run_log_dir=tmp_path)

    assert not sink.enabled
    assert sink.effective_level == "off"
    assert sink.writer_error == "OSError: injected writer failure"
    assert not sink.record("control", {"linear_x": 0.1})
    sink.close()


def test_insufficient_disk_drops_blob_but_keeps_scalar_tracing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sink = TraceSink(
        "planner",
        config=_config(
            "full",
            navigation_trace_min_free_disk_bytes=1,
        ),
        run_log_dir=tmp_path,
    )
    disk_usage = type("DiskUsage", (), {"free": 0})()
    monkeypatch.setattr(
        "dimos.navigation.diagnostics.sink.shutil.disk_usage",
        lambda _path: disk_usage,
    )

    assert sink.record_blob(
        "costmap",
        np.zeros((4, 4), dtype=np.int8),
        {},
        stem="costmap",
    )
    assert sink.record("control_after_blob_drop", {"linear_x": 0.1})
    sink.close()

    assert sink.effective_level == "full"
    assert sink.output_path is not None
    events = _read_events(sink.output_path)
    assert any(event["event"] == "control_after_blob_drop" for event in events)
    assert not any(event["event"] == "blob_saved" for event in events)
    drops = next(event for event in events if event["event"] == "trace_drop_summary")
    assert drops["drops"]["insufficient_free_disk"]["count"] == 1


def test_shutdown_uses_fixed_100ms_join_budget(tmp_path: Path) -> None:
    sink = TraceSink("planner", config=_config("off"), run_log_dir=tmp_path)

    class StillRunningWriter:
        timeout: float | None = None

        def join(self, timeout: float | None = None) -> None:
            self.timeout = timeout

        def is_alive(self) -> bool:
            return True

    writer = StillRunningWriter()
    sink._stop_event = threading.Event()
    sink._writer_thread = writer  # type: ignore[assignment]

    sink.close()

    assert writer.timeout == 0.1
    assert sink.writer_error == "writer_shutdown_timeout"


def test_writer_flushes_header_before_shutdown(tmp_path: Path) -> None:
    sink = TraceSink("planner", config=_config("summary"), run_log_dir=tmp_path)
    assert sink.output_path is not None

    # The header is flushed as soon as the writer is ready, so an abrupt worker
    # exit still leaves a parseable partial artifact instead of a zero-byte file.
    deadline = time.monotonic() + 1.0
    while time.monotonic() < deadline and sink.output_path.stat().st_size == 0:
        time.sleep(0.01)
    assert sink.output_path.stat().st_size > 0
    sink.close()


def test_blob_limits_do_not_copy_or_convert_array(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sink = TraceSink(
        "planner",
        config=_config("full", navigation_trace_blob_max_item_bytes=4),
        run_log_dir=tmp_path,
    )
    array = np.arange(8, dtype=np.uint8)

    def forbidden(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("hot path must not copy or convert arrays")

    monkeypatch.setattr(np, "copy", forbidden)

    assert not sink.record_blob("costmap", array, {}, stem="too-large")
    assert sink.effective_level == "full"
    sink.close()


def test_redaction_removes_nested_secrets_and_command_flags() -> None:
    payload = {
        "unitree_password": "secret-password",
        "nested": {
            "token": "secret-token",
            "command": "dimos --unitree-password p4ss --api-key=abc run go2",
        },
        "safe": "visible",
    }

    redacted = redact_sensitive(payload)

    assert redacted["unitree_password"] == "<redacted>"
    assert redacted["nested"]["token"] == "<redacted>"
    assert "p4ss" not in redacted["nested"]["command"]
    assert "abc" not in redacted["nested"]["command"]
    assert redacted["safe"] == "visible"


def test_writer_redacts_configured_credentials_inside_arbitrary_text(
    tmp_path: Path,
) -> None:
    sink = TraceSink(
        "connection",
        config=_config(
            "summary",
            unitree_username="owner@example.com",
            unitree_password="not-for-logs",
        ),
        run_log_dir=tmp_path,
    )

    assert sink.record(
        "connection_error",
        {"error_message": ("login owner@example.com failed with password not-for-logs")},
    )
    sink.close()

    assert sink.output_path is not None
    text = sink.output_path.read_text(encoding="utf-8")
    assert "owner@example.com" not in text
    assert "not-for-logs" not in text
    assert text.count("<redacted>") >= 2


def test_scalar_budget_degrades_diagnostics_only(tmp_path: Path) -> None:
    sink = TraceSink(
        "planner",
        config=_config(
            "full",
            navigation_trace_scalar_max_bytes_per_producer=5,
        ),
        run_log_dir=tmp_path,
    )

    assert not sink.record("too-large", estimated_bytes=6)
    assert sink.effective_level == "summary"
    sink.close()
