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

"""Deterministic local microbenchmark for the TraceSink synchronous path."""

from __future__ import annotations

import argparse
import asyncio
import gc
import json
from pathlib import Path
import tempfile
import time
from typing import Any, Literal

import numpy as np

from dimos.core.global_config import GlobalConfig
from dimos.navigation.diagnostics.sink import TraceSink


def run_trace_sink_microbenchmark(samples: int = 10_000) -> dict[str, Any]:
    """Measure only the caller-visible ``record()`` duration."""
    count = max(100, samples)
    config = GlobalConfig(
        navigation_trace_level="full",
        navigation_trace_scalar_queue_items=count + 1024,
        navigation_trace_scalar_max_bytes_per_producer=256 * 1024 * 1024,
        navigation_trace_min_free_disk_bytes=0,
    )
    with tempfile.TemporaryDirectory(prefix="dimos-nav-trace-benchmark-") as directory:
        sink = TraceSink("planner", config=config, run_log_dir=Path(directory))
        durations = np.empty(count, dtype=np.int64)
        accepted = 0
        gc_was_enabled = gc.isenabled()
        gc.disable()
        try:
            for index in range(count):
                started_ns = time.perf_counter_ns()
                success = sink.record(
                    "benchmark_scalar",
                    {
                        "sample": index,
                        "linear_x": 0.2,
                        "angular_z": -0.1,
                    },
                    estimated_bytes=256,
                )
                durations[index] = time.perf_counter_ns() - started_ns
                accepted += int(success)
        finally:
            if gc_was_enabled:
                gc.enable()
        drain_deadline = time.monotonic() + 5.0
        while time.monotonic() < drain_deadline:
            scalar_empty = sink._scalar_queue is None or sink._scalar_queue.empty()
            blob_empty = sink._blob_queue is None or sink._blob_queue.empty()
            if scalar_empty and blob_empty:
                break
            time.sleep(0.001)
        sink.close()
        duration_ms = durations.astype(np.float64) / 1_000_000
        return {
            "samples": count,
            "accepted": accepted,
            "p50_ms": float(np.percentile(duration_ms, 50)),
            "p95_ms": float(np.percentile(duration_ms, 95)),
            "p99_ms": float(np.percentile(duration_ms, 99)),
            "max_ms": float(np.max(duration_ms)),
            "target_p99_ms": 0.5,
            "target_max_ms": 2.0,
            "cyclic_gc_disabled_during_measurement": True,
            "scope_note": (
                "isolated synchronous call cost; replay A/B covers scheduler and GC effects"
            ),
            "p99_pass": float(np.percentile(duration_ms, 99)) < 0.5,
            "max_pass": float(np.max(duration_ms)) < 2.0,
            "writer_error": sink.writer_error,
        }


def run_event_loop_heartbeat_microbenchmark(
    samples: int = 500,
    interval_sec: float = 0.002,
) -> dict[str, Any]:
    """Compare identical event-loop probes with tracing off and full.

    A normal replay connection does not create the WebRTC asyncio loop. This
    isolated A/B keeps the probe itself identical in both trials and changes
    only whether the callback records the same heartbeat event as the live
    connection.
    """
    count = max(20, samples)
    interval = max(0.0005, interval_sec)
    with tempfile.TemporaryDirectory(prefix="dimos-nav-loop-benchmark-") as directory:
        root = Path(directory)
        off = _run_event_loop_trial(
            root / "off",
            level="off",
            samples=count,
            interval_sec=interval,
        )
        full = _run_event_loop_trial(
            root / "full",
            level="full",
            samples=count,
            interval_sec=interval,
        )

    p99_delta_ms = full["p99_ms"] - off["p99_ms"]
    return {
        "samples_per_mode": count,
        "interval_sec": interval,
        "off": off,
        "full": full,
        "p99_delta_ms": p99_delta_ms,
        "target_p99_delta_ms": 2.0,
        "p99_delta_pass": p99_delta_ms <= 2.0,
        "scope_note": (
            "isolated identical asyncio callback schedule; production replay has no "
            "WebRTC event loop, while live full traces the same callback"
        ),
    }


def _run_event_loop_trial(
    run_log_dir: Path,
    *,
    level: Literal["off", "full"],
    samples: int,
    interval_sec: float,
) -> dict[str, Any]:
    config = GlobalConfig(
        navigation_trace_level=level,
        navigation_trace_scalar_queue_items=samples + 128,
        navigation_trace_scalar_max_bytes_per_producer=16 * 1024 * 1024,
        navigation_trace_min_free_disk_bytes=0,
    )
    sink = TraceSink("connection", config=config, run_log_dir=run_log_dir)
    delays_ns = np.empty(samples, dtype=np.int64)
    loop = asyncio.new_event_loop()
    completed = loop.create_future()

    def heartbeat(index: int, expected_loop_time: float) -> None:
        actual_loop_time = loop.time()
        delays_ns[index] = max(
            0,
            int((actual_loop_time - expected_loop_time) * 1_000_000_000),
        )
        if sink.accepts("full"):
            sink.record(
                "webrtc_loop_heartbeat",
                {
                    "scheduled_monotonic_ns": int(expected_loop_time * 1_000_000_000),
                    "callback_monotonic_ns": int(actual_loop_time * 1_000_000_000),
                    "delay_ns": int(delays_ns[index]),
                    "interval_sec": interval_sec,
                },
                estimated_bytes=512,
            )
        next_index = index + 1
        if next_index >= samples:
            completed.set_result(None)
            return
        next_loop_time = actual_loop_time + interval_sec
        loop.call_at(next_loop_time, heartbeat, next_index, next_loop_time)

    first_loop_time = loop.time() + interval_sec
    loop.call_at(first_loop_time, heartbeat, 0, first_loop_time)
    try:
        loop.run_until_complete(completed)
    finally:
        loop.close()
        sink.close()

    delays_ms = delays_ns.astype(np.float64) / 1_000_000
    return {
        "samples": samples,
        "p50_ms": float(np.percentile(delays_ms, 50)),
        "p95_ms": float(np.percentile(delays_ms, 95)),
        "p99_ms": float(np.percentile(delays_ms, 99)),
        "max_ms": float(np.max(delays_ms)),
        "writer_error": sink.writer_error,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--samples", type=int, default=10_000)
    parser.add_argument(
        "--event-loop-samples",
        type=int,
        default=0,
        help="also run an off/full asyncio heartbeat A/B with this many samples",
    )
    args = parser.parse_args()
    result: dict[str, Any] = {
        "trace_sink": run_trace_sink_microbenchmark(args.samples),
    }
    if args.event_loop_samples > 0:
        result["event_loop_heartbeat"] = run_event_loop_heartbeat_microbenchmark(
            args.event_loop_samples
        )
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
