#!/usr/bin/env python3
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

"""Analyze Go2 source timestamps without calling host-source network latency."""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
from itertools import pairwise
import json
from pathlib import Path
import statistics
from typing import Any


@dataclass
class Distribution:
    minimum: float
    p01: float
    p50: float
    p95: float
    p99: float
    maximum: float


def _quantile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int(len(ordered) * fraction)))
    return ordered[index]


def _distribution(values: list[float]) -> Distribution:
    if not values:
        raise ValueError("empty distribution")
    return Distribution(
        minimum=min(values),
        p01=_quantile(values, 0.01),
        p50=_quantile(values, 0.50),
        p95=_quantile(values, 0.95),
        p99=_quantile(values, 0.99),
        maximum=max(values),
    )


def _load(path: Path) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    with path.open() as stream:
        for line in stream:
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return events


def _time_bins(odom: list[dict[str, Any]], width_s: float = 20.0) -> list[dict[str, float]]:
    start = float(odom[0]["host_rx_ts"])
    buckets: dict[int, list[float]] = {}
    for event in odom:
        index = int((float(event["host_rx_ts"]) - start) / width_s)
        buckets.setdefault(index, []).append(float(event["host_rx_ts"]) - float(event["source_ts"]))
    return [
        {
            "start_s": index * width_s,
            "end_s": (index + 1) * width_s,
            "samples": float(len(values)),
            "mean_host_source_delta_s": statistics.mean(values),
            "min_host_source_delta_s": min(values),
            "max_host_source_delta_s": max(values),
        }
        for index, values in sorted(buckets.items())
    ]


def analyze(path: Path) -> dict[str, Any]:
    events = _load(path)
    odom = [event for event in events if event.get("event") == "connection_raw_odom"]
    lidar = [event for event in events if event.get("event") == "connection_raw_lidar"]
    heartbeat = [event for event in events if event.get("event") == "webrtc_loop_heartbeat"]
    published = [event for event in events if event.get("event") == "connection_odom_published"]
    if len(odom) < 2:
        raise ValueError("trace has fewer than two raw odom events")

    source = [float(event["source_ts"]) for event in odom]
    host_wall = [float(event["host_rx_ts"]) for event in odom]
    host_monotonic = [int(event["host_rx_monotonic_ns"]) / 1e9 for event in odom]
    deltas = [host - source_ts for host, source_ts in zip(host_wall, source, strict=True)]
    source_intervals = [current - previous for previous, current in pairwise(source)]
    sorted_source_intervals = [current - previous for previous, current in pairwise(sorted(source))]
    host_intervals = [current - previous for previous, current in pairwise(host_monotonic)]
    heartbeat_ms = [float(event["delay_ns"]) / 1e6 for event in heartbeat]
    duration = host_monotonic[-1] - host_monotonic[0]
    baseline = _quantile(deltas, 0.01)
    excess = [value - baseline for value in deltas]
    backward_indices = [index for index, interval in enumerate(source_intervals) if interval < 0]
    backwards = len(backward_indices)
    backward_magnitudes = [-source_intervals[index] for index in backward_indices]
    immediate_recoveries = sum(
        index + 2 < len(source) and source[index + 2] > source[index] for index in backward_indices
    )
    repeated = sum(interval == 0 for interval in source_intervals)
    catchup_pairs = sum(
        source_dt > 0.04 and host_dt < 0.02
        for source_dt, host_dt in zip(source_intervals, host_intervals, strict=True)
    )
    dynamic_confirmed = (
        repeated == 0
        and max(excess) > 1.0
        and catchup_pairs > 0
        and min(deltas[-max(100, len(deltas) // 20) :]) < baseline + 0.20
    )
    if dynamic_confirmed and baseline > 1.0:
        timing_classification = "CLOCK_OFFSET_AND_BACKLOG"
    elif dynamic_confirmed:
        timing_classification = "DYNAMIC_BACKLOG_CONFIRMED"
    else:
        timing_classification = "CLOCK_OFFSET_DOMINANT"
    if repeated > len(source_intervals) * 0.01:
        timestamp_order_classification = "REPEATED_OR_UNUSABLE"
    elif backwards:
        timestamp_order_classification = "OUT_OF_ORDER_ON_ARRIVAL"
    else:
        timestamp_order_classification = "MONOTONIC_ON_ARRIVAL"
    if timestamp_order_classification == "OUT_OF_ORDER_ON_ARRIVAL":
        classification = f"{timing_classification}_WITH_OUT_OF_ORDER_DELIVERY"
    elif timestamp_order_classification == "REPEATED_OR_UNUSABLE":
        classification = "SOURCE_TIMESTAMP_UNUSABLE_OR_INCONCLUSIVE"
    else:
        classification = timing_classification

    return {
        "trace": str(path.resolve()),
        "classification": classification,
        "timing_classification": timing_classification,
        "timestamp_order_classification": timestamp_order_classification,
        "terminology_note": (
            "host-source is a cross-clock time difference, not proven one-way network latency"
        ),
        "duration_s": duration,
        "counts": {
            "events": len(events),
            "raw_odom": len(odom),
            "published_odom": len(published),
            "raw_lidar": len(lidar),
            "heartbeat": len(heartbeat),
        },
        "rates_hz": {
            "raw_odom": len(odom) / duration,
            "raw_lidar": len(lidar) / duration,
            "heartbeat": len(heartbeat) / duration,
        },
        "host_source_delta_s": asdict(_distribution(deltas)),
        "baseline_p01_s": baseline,
        "maximum_excess_over_baseline_s": max(excess),
        "source_interval_s": asdict(_distribution(source_intervals)),
        "sorted_source_interval_s": asdict(_distribution(sorted_source_intervals)),
        "host_interval_s": asdict(_distribution(host_intervals)),
        "source_timestamp": {
            "backwards": backwards,
            "repeated": repeated,
            "unique": len(set(source)) == len(source),
            "backward_magnitude_s": (
                asdict(_distribution(backward_magnitudes)) if backward_magnitudes else None
            ),
            "immediate_recoveries": immediate_recoveries,
            "first": source[0],
            "last": source[-1],
            "advance_s": source[-1] - source[0],
        },
        "host_receive": {
            "advance_s": host_monotonic[-1] - host_monotonic[0],
            "catchup_pairs_source_gt_40ms_host_lt_20ms": catchup_pairs,
            "catchup_pair_fraction": catchup_pairs / len(host_intervals),
        },
        "event_loop_delay_ms": asdict(_distribution(heartbeat_ms)),
        "time_bins": _time_bins(odom),
    }


def _markdown(report: dict[str, Any]) -> str:
    delta = report["host_source_delta_s"]
    heartbeat = report["event_loop_delay_ms"]
    counts = report["counts"]
    rates = report["rates_hz"]
    source = report["source_timestamp"]
    host = report["host_receive"]
    return f"""# Go2 Remote timing trace report

- Classification: `{report["classification"]}`
- Timing classification: `{report["timing_classification"]}`
- Timestamp arrival order: `{report["timestamp_order_classification"]}`
- Duration: {report["duration_s"]:.3f} s
- Raw odom: {counts["raw_odom"]} ({rates["raw_odom"]:.3f} Hz)
- Raw lidar: {counts["raw_lidar"]} ({rates["raw_lidar"]:.3f} Hz)
- Source timestamp backwards/repeated: {source["backwards"]}/{source["repeated"]}
- Source timestamp immediate recoveries: {source["immediate_recoveries"]}
- Host-source delta baseline p01: {report["baseline_p01_s"]:.6f} s
- Host-source delta p50/p95/p99/max: {delta["p50"]:.6f} / {delta["p95"]:.6f} /
  {delta["p99"]:.6f} / {delta["maximum"]:.6f} s
- Maximum excess over p01 baseline: {report["maximum_excess_over_baseline_s"]:.6f} s
- Catch-up pairs: {host["catchup_pairs_source_gt_40ms_host_lt_20ms"]}
  ({host["catchup_pair_fraction"]:.2%})
- Event-loop delay p95/p99/max: {heartbeat["p95"]:.3f} / {heartbeat["p99"]:.3f} /
  {heartbeat["maximum"]:.3f} ms

`host-source` remains a cross-clock time difference. The stable lower envelope combines
unknown clock offset and minimum path delay; it is not a pure clock-offset measurement.
"""


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("trace", type=Path)
    parser.add_argument("--output-dir", type=Path)
    args = parser.parse_args()
    report = analyze(args.trace)
    output_dir = args.output_dir or args.trace.parent
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "go2_timing_analysis.json"
    markdown_path = output_dir / "go2_timing_analysis.md"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2))
    markdown_path.write_text(_markdown(report))
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print(f"json={json_path}")
    print(f"markdown={markdown_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
