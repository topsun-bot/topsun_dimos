#!/usr/bin/env python3
"""Save and restore the minimum context needed after an interruption."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import tempfile
from typing import Any, cast

SCHEMA_VERSION = 1


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def require_text(value: str, field: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{field}不能为空")
    return normalized


def load_checkpoint(path: Path) -> dict[str, Any]:
    data = cast("dict[str, Any]", json.loads(path.read_text(encoding="utf-8")))
    if data.get("schema_version") != SCHEMA_VERSION:
        raise ValueError(f"unsupported schema_version: {data.get('schema_version')}")
    if data.get("status") not in {"active", "complete"}:
        raise ValueError("status必须是active或complete")
    if not isinstance(data.get("history"), list):
        raise ValueError("history必须是列表")
    return data


def atomic_save(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
            temporary_path = Path(handle.name)
        os.replace(temporary_path, path)
    finally:
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink()


def _snapshot(checkpoint: dict[str, Any]) -> dict[str, Any]:
    return {
        "updated_at": checkpoint["updated_at"],
        "status": checkpoint["status"],
        "last_done": checkpoint["last_done"],
        "next_action": checkpoint["next_action"],
        "blockers": checkpoint["blockers"],
        "evidence": checkpoint["evidence"],
    }


def save_task_checkpoint(
    path: Path,
    *,
    task_id: str,
    goal: str,
    last_done: str,
    next_action: str,
    blockers: list[str] | None = None,
    evidence: list[str] | None = None,
) -> dict[str, Any]:
    task_id = require_text(task_id, "task_id")
    goal = require_text(goal, "goal")
    last_done = require_text(last_done, "last_done")
    next_action = require_text(next_action, "next_action")
    history: list[dict[str, Any]] = []

    if path.exists():
        previous = load_checkpoint(path)
        if previous["task_id"] != task_id:
            raise ValueError("已有检查点属于不同task_id")
        if previous["status"] == "complete":
            raise ValueError("任务已经完成, 不能继续覆盖")
        history = [*previous["history"], _snapshot(previous)]

    checkpoint = {
        "schema_version": SCHEMA_VERSION,
        "task_id": task_id,
        "status": "active",
        "goal": goal,
        "last_done": last_done,
        "next_action": next_action,
        "blockers": [item.strip() for item in blockers or [] if item.strip()],
        "evidence": [item.strip() for item in evidence or [] if item.strip()],
        "updated_at": utc_now(),
        "history": history,
    }
    atomic_save(path, checkpoint)
    return checkpoint


def complete_task(path: Path, evidence: str) -> dict[str, Any]:
    checkpoint = load_checkpoint(path)
    if checkpoint["status"] == "complete":
        raise ValueError("任务已经完成")
    completion_evidence = require_text(evidence, "evidence")
    checkpoint["history"] = [*checkpoint["history"], _snapshot(checkpoint)]
    checkpoint["status"] = "complete"
    checkpoint["last_done"] = completion_evidence
    checkpoint["next_action"] = ""
    checkpoint["evidence"] = [*checkpoint["evidence"], completion_evidence]
    checkpoint["updated_at"] = utc_now()
    atomic_save(path, checkpoint)
    return checkpoint


def resume_view(checkpoint: dict[str, Any]) -> str:
    blocker_text = "; ".join(checkpoint["blockers"]) or "无"
    evidence_text = "; ".join(checkpoint["evidence"]) or "无"
    return "\n".join(
        [
            f"任务: {checkpoint['task_id']} [{checkpoint['status']}]",
            f"目标: {checkpoint['goal']}",
            f"最后完成: {checkpoint['last_done']}",
            f"下一动作: {checkpoint['next_action'] or '任务已完成'}",
            f"阻碍: {blocker_text}",
            f"证据: {evidence_text}",
            f"更新时间: {checkpoint['updated_at']}",
        ]
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    checkpoint_parser = subparsers.add_parser("checkpoint", help="保存中断检查点")
    checkpoint_parser.add_argument("file", type=Path)
    checkpoint_parser.add_argument("--task-id", required=True)
    checkpoint_parser.add_argument("--goal", required=True)
    checkpoint_parser.add_argument("--last-done", required=True)
    checkpoint_parser.add_argument("--next-action", required=True)
    checkpoint_parser.add_argument("--blocker", action="append", default=[])
    checkpoint_parser.add_argument("--evidence", action="append", default=[])

    resume_parser = subparsers.add_parser("resume", help="恢复任务线索")
    resume_parser.add_argument("file", type=Path)
    resume_parser.add_argument("--json", action="store_true")

    complete_parser = subparsers.add_parser("complete", help="完成任务")
    complete_parser.add_argument("file", type=Path)
    complete_parser.add_argument("--evidence", required=True)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.command == "checkpoint":
        checkpoint = save_task_checkpoint(
            args.file,
            task_id=args.task_id,
            goal=args.goal,
            last_done=args.last_done,
            next_action=args.next_action,
            blockers=args.blocker,
            evidence=args.evidence,
        )
    elif args.command == "complete":
        checkpoint = complete_task(args.file, args.evidence)
    else:
        checkpoint = load_checkpoint(args.file)

    if args.command == "resume" and args.json:
        print(json.dumps(checkpoint, ensure_ascii=False, indent=2))
    else:
        print(resume_view(checkpoint))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
