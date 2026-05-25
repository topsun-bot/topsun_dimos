#!/usr/bin/env python3
# ruff: noqa: RUF001, RUF002
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

"""nav_log_summary.py —— dimos run 日志的轻量摘要（plan.md §E-7）。

读取 dimos 写到 ~/.local/state/dimos/logs/<run-id>/main.jsonl 的结构化日志，
统计：
  - 启动 / 停止时间、运行时长
  - 闭环触发次数（PGO loop closure）
  - 规划失败次数（A* failed / planner timeout）
  - cmd_vel 平均频率（粗估）
  - 异常停 / 未处理异常 / worker 死亡
  - 各 logger 的 INFO / WARNING / ERROR 计数

用法：
    # 摘要最近一次 run
    python3 jiangtao/scripts/nav_log_summary.py

    # 指定 run-id
    python3 jiangtao/scripts/nav_log_summary.py 20260521-203012-unitree-go2-nav-onboard

    # JSON 输出（给后续工具化）
    python3 jiangtao/scripts/nav_log_summary.py --json
"""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime
import json
from pathlib import Path
import re
import sys


def find_default_log_dir() -> Path:
    """返回 dimos 默认日志目录。"""
    return Path.home() / ".local" / "state" / "dimos" / "logs"


def latest_run(log_root: Path) -> Path | None:
    """返回最近一次 run 目录（按 mtime）。"""
    if not log_root.exists():
        return None
    runs = [p for p in log_root.iterdir() if p.is_dir() and (p / "main.jsonl").exists()]
    if not runs:
        return None
    return max(runs, key=lambda p: (p / "main.jsonl").stat().st_mtime)


def parse_jsonl(path: Path) -> list[dict]:
    """解析 main.jsonl，跳过损坏行。"""
    rows: list[dict] = []
    with path.open(encoding="utf-8") as fp:
        for line in fp:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return rows


# 关键事件正则（event 字段做不区分大小写匹配）
_PATTERNS = {
    "loop_closure": re.compile(r"loop\s+closure\s+triggered|PGO\s+loop", re.I),
    "planner_failed": re.compile(r"A\*\s+failed|planner\s+failed|stuck", re.I),
    "uncaught": re.compile(r"uncaught\s+exception|fatal", re.I),
    "worker_died": re.compile(r"worker\s+(died|crashed|exited)", re.I),
    "shutdown_signal": re.compile(r"received\s+signal", re.I),
}


def summarize(rows: list[dict]) -> dict:
    if not rows:
        return {"empty": True}

    summary: dict = {
        "total_lines": len(rows),
        "level_counts": Counter(),
        "logger_warning_top": Counter(),
        "logger_error_top": Counter(),
        "events": {k: 0 for k in _PATTERNS},
    }

    timestamps: list[datetime] = []

    for row in rows:
        level = (row.get("level") or "").lower()
        summary["level_counts"][level] += 1

        if level == "warning":
            summary["logger_warning_top"][row.get("logger", "?")] += 1
        elif level in ("error", "critical"):
            summary["logger_error_top"][row.get("logger", "?")] += 1

        event = row.get("event") or ""
        for key, pat in _PATTERNS.items():
            if pat.search(event):
                summary["events"][key] += 1

        ts = row.get("timestamp")
        if isinstance(ts, str):
            try:
                # 形如 "2026-04-28T07:25:01.026984Z"
                timestamps.append(datetime.fromisoformat(ts.replace("Z", "+00:00")))
            except ValueError:
                pass

    if timestamps:
        summary["started_at"] = timestamps[0].isoformat()
        summary["ended_at"] = timestamps[-1].isoformat()
        summary["duration_seconds"] = round((timestamps[-1] - timestamps[0]).total_seconds(), 1)

    summary["level_counts"] = dict(summary["level_counts"])
    summary["logger_warning_top"] = dict(summary["logger_warning_top"].most_common(5))
    summary["logger_error_top"] = dict(summary["logger_error_top"].most_common(5))

    return summary


def render_text(summary: dict, run_path: Path) -> str:
    if summary.get("empty"):
        return f"日志为空: {run_path}"

    lines = [
        f"Run: {run_path.name}",
        f"日志路径: {run_path / 'main.jsonl'}",
        f"总行数: {summary['total_lines']}",
    ]

    if "started_at" in summary:
        lines.append(
            f"运行时长: {summary['duration_seconds']}s   "
            f"({summary['started_at']} → {summary['ended_at']})"
        )

    lines.append("")
    lines.append("级别分布:")
    for lvl, n in sorted(summary["level_counts"].items()):
        lines.append(f"  {lvl:<10} {n}")

    lines.append("")
    lines.append("关键事件:")
    for key, n in summary["events"].items():
        flag = "" if n == 0 else "  <-- 注意" if key in ("uncaught", "worker_died") else ""
        lines.append(f"  {key:<18} {n}{flag}")

    if summary["logger_warning_top"]:
        lines.append("")
        lines.append("Warning 来源 Top5:")
        for logger, n in summary["logger_warning_top"].items():
            lines.append(f"  {n:>4}  {logger}")

    if summary["logger_error_top"]:
        lines.append("")
        lines.append("Error 来源 Top5:")
        for logger, n in summary["logger_error_top"].items():
            lines.append(f"  {n:>4}  {logger}")

    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument(
        "run_id",
        nargs="?",
        help="run 目录名（如 20260521-203012-unitree-go2-nav-onboard）；省略则取最近一次",
    )
    parser.add_argument(
        "--logs-dir",
        type=Path,
        default=find_default_log_dir(),
        help="dimos 日志根目录（默认 ~/.local/state/dimos/logs）",
    )
    parser.add_argument("--json", action="store_true", help="JSON 输出")
    args = parser.parse_args()

    run_path = args.logs_dir / args.run_id if args.run_id else latest_run(args.logs_dir)
    if run_path is None or not run_path.exists():
        print(f"找不到 run 目录: {args.logs_dir}", file=sys.stderr)
        return 2

    jsonl = run_path / "main.jsonl"
    if not jsonl.exists():
        print(f"找不到日志文件: {jsonl}", file=sys.stderr)
        return 2

    rows = parse_jsonl(jsonl)
    summary = summarize(rows)

    if args.json:
        print(json.dumps({"run": run_path.name, **summary}, ensure_ascii=False, indent=2))
    else:
        print(render_text(summary, run_path))

    return 0


if __name__ == "__main__":
    sys.exit(main())
