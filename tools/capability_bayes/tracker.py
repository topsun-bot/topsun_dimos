#!/usr/bin/env python3
"""Track evidence that an AI-assisted skill has become independent capability."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
from typing import Any, cast

SCHEMA_VERSION = 1
STOP_CONFIDENCE = 0.9
STAGE_ORDER = ("ai_quality", "recall", "transfer", "pressure", "value", "reward")
PREREGISTRATION_STAGES = {"recall", "transfer", "pressure", "value", "reward"}
STAGE_CONFIG: dict[str, dict[str, Any]] = {
    "ai_quality": {
        "label": "AI甄优",
        "target_rate": 0.9,
        "min_trials": 21,
        "next_action": "选一个公司相关AI结果, 用权威来源加测试或反例完成核验。",
    },
    "recall": {
        "label": "延迟回忆",
        "target_rate": 0.8,
        "min_trials": 10,
        "next_action": "24小时后关闭AI和资料, 重新讲解或重建结果。",
    },
    "transfer": {
        "label": "陌生迁移",
        "target_rate": 0.8,
        "min_trials": 10,
        "next_action": "让他人准备一道未见变式, 关闭AI独立完成。",
    },
    "pressure": {
        "label": "压力提取",
        "target_rate": 0.7,
        "min_trials": 8,
        "next_action": "对陌生变式进行限时、录屏或模拟追问测试。",
    },
    "value": {
        "label": "业务价值",
        "target_rate": 0.6,
        "min_trials": 5,
        "next_action": "交付一个有前后指标且被真实使用者验收的结果。",
    },
    "reward": {
        "label": "收入或权限",
        "target_rate": 0.5,
        "min_trials": 5,
        "next_action": "用已验收成果提出一次明确的薪酬、资源或权限申请。",
    },
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def beta_tail_probability(threshold: float, alpha: int, beta: int) -> float:
    """Return P(theta >= threshold) for integer-parameter Beta(alpha, beta).

    The Beta/Binomial identity avoids a scipy dependency:
    1 - I_x(a, b) = P(Binomial(a+b-1, x) <= a-1).
    """
    if not 0.0 <= threshold <= 1.0:
        raise ValueError("threshold must be between 0 and 1")
    if alpha < 1 or beta < 1:
        raise ValueError("alpha and beta must be positive integers")

    n = alpha + beta - 1
    terms = (math.comb(n, k) * threshold**k * (1.0 - threshold) ** (n - k) for k in range(alpha))
    return min(1.0, max(0.0, math.fsum(terms)))


def new_tracker(capability: str) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "capability": capability,
        "created_at": utc_now(),
        "prior": {"alpha": 1, "beta": 1},
        "stop_confidence": STOP_CONFIDENCE,
        "certification_plans": [],
        "trials": [],
    }


def load_tracker(path: Path) -> dict[str, Any]:
    data = cast("dict[str, Any]", json.loads(path.read_text(encoding="utf-8")))
    if data.get("schema_version") != SCHEMA_VERSION:
        raise ValueError(f"unsupported schema_version: {data.get('schema_version')}")
    if not isinstance(data.get("trials"), list):
        raise ValueError("trials must be a list")
    data.setdefault("certification_plans", [])
    if not isinstance(data["certification_plans"], list):
        raise ValueError("certification_plans must be a list")
    return data


def save_tracker(path: Path, tracker: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(tracker, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def trial_validity(
    trial: dict[str, Any], *, duplicate_test_id: bool = False, duplicate_group: bool = False
) -> tuple[bool, list[str]]:
    stage = trial["stage"]
    reasons: list[str] = []

    if not trial.get("certification"):
        reasons.append("练习记录不是认证盲测")
    if not str(trial.get("test_id", "")).strip():
        reasons.append("认证盲测缺少唯一test_id")
    if duplicate_test_id:
        reasons.append("同阶段test_id重复, 不能作为独立证据")
    if trial.get("certification") and not str(trial.get("evidence_group", "")).strip():
        reasons.append("认证证据缺少evidence_group")
    if duplicate_group:
        reasons.append("同阶段evidence_group重复, 属于相关证据")
    if stage in PREREGISTRATION_STAGES and not trial.get("preregistered"):
        issue = str(trial.get("preregistration_issue", "认证测试没有有效预注册"))
        reasons.extend(reason for reason in issue.split("; ") if reason)
    if not str(trial.get("evidence", "")).strip():
        reasons.append("缺少可复核证据")

    if stage in {"ai_quality", "recall", "transfer", "pressure"}:
        if not trial.get("source_checked"):
            reasons.append("AI结果未与权威来源或事实基准核对")
        if not (trial.get("executable_checked") or trial.get("counterexample_checked")):
            reasons.append("AI结果缺少可执行测试或反例检查")

    if stage in {"recall", "transfer", "pressure"}:
        if not trial.get("unassisted"):
            reasons.append("测试过程中使用了AI或资料")

    if stage == "recall" and float(trial.get("delay_hours", 0.0)) < 24.0:
        reasons.append("延迟不足24小时")

    if stage in {"transfer", "pressure"} and not trial.get("novel"):
        reasons.append("不是未见过的变式")

    if stage == "pressure" and not trial.get("timed"):
        reasons.append("没有限时或模拟追问")

    if stage == "value":
        if not str(trial.get("metric", "")).strip():
            reasons.append("缺少业务指标或前后对比")
        if not trial.get("accepted"):
            reasons.append("结果未被真实使用者验收")

    if stage == "reward" and not trial.get("accepted"):
        reasons.append("收入、资源或权限申请未形成可核实结果")

    return not reasons, reasons


def parse_aware_datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("时间必须包含时区")
    return parsed


def prepare_certification(
    tracker: dict[str, Any],
    *,
    stage: str,
    test_id: str,
    due_at: str,
    prompt_hash: str,
    rubric: str,
    evidence_group: str,
) -> dict[str, Any]:
    if stage not in PREREGISTRATION_STAGES:
        raise ValueError("该阶段不使用延迟预注册")
    if not test_id.strip():
        raise ValueError("test_id不能为空")
    if any(
        plan.get("stage") == stage and plan.get("test_id") == test_id
        for plan in tracker["certification_plans"]
    ):
        raise ValueError("同阶段test_id已经预注册")
    due = parse_aware_datetime(due_at)
    normalized_hash = prompt_hash.strip().lower()
    if len(normalized_hash) != 64 or any(
        character not in "0123456789abcdef" for character in normalized_hash
    ):
        raise ValueError("prompt_hash必须是64位SHA-256十六进制字符串")
    if not rubric.strip():
        raise ValueError("rubric不能为空")
    if not evidence_group.strip():
        raise ValueError("evidence_group不能为空")

    plan = {
        "stage": stage,
        "test_id": test_id,
        "prepared_at": utc_now(),
        "due_at": due.isoformat(timespec="seconds"),
        "prompt_hash": normalized_hash,
        "rubric": rubric,
        "evidence_group": evidence_group,
        "status": "pending",
    }
    tracker["certification_plans"].append(plan)
    return plan


def summarize_stage(tracker: dict[str, Any], stage: str) -> dict[str, Any]:
    prior = tracker["prior"]
    successes = 0
    failures = 0
    invalid = 0
    practice = 0
    correlated = 0
    pending = 0
    overdue = 0
    seen_test_ids: set[str] = set()
    seen_groups: set[str] = set()

    now = datetime.now(timezone.utc)
    for plan in tracker.get("certification_plans", []):
        if plan.get("stage") != stage or plan.get("status") != "pending":
            continue
        if parse_aware_datetime(plan["due_at"]) <= now:
            overdue += 1
        else:
            pending += 1

    for trial in tracker["trials"]:
        if trial.get("stage") != stage:
            continue
        if not trial.get("certification"):
            practice += 1
            continue
        test_id = str(trial.get("test_id", "")).strip()
        evidence_group = str(trial.get("evidence_group", "")).strip()
        duplicate_test_id = bool(test_id and test_id in seen_test_ids)
        duplicate_group = bool(evidence_group and evidence_group in seen_groups)
        if test_id:
            seen_test_ids.add(test_id)
        if evidence_group:
            seen_groups.add(evidence_group)
        valid, _ = trial_validity(
            trial,
            duplicate_test_id=duplicate_test_id,
            duplicate_group=duplicate_group,
        )
        if not valid:
            invalid += 1
            if duplicate_group:
                correlated += 1
        elif trial.get("result") == "pass":
            successes += 1
        else:
            failures += 1

    failures += overdue
    config = STAGE_CONFIG[stage]
    alpha = int(prior["alpha"]) + successes
    beta = int(prior["beta"]) + failures
    confidence = beta_tail_probability(config["target_rate"], alpha, beta)
    valid_trials = successes + failures
    graduated = valid_trials >= config["min_trials"] and confidence > float(
        tracker["stop_confidence"]
    )
    return {
        "stage": stage,
        "label": config["label"],
        "successes": successes,
        "failures": failures,
        "practice": practice,
        "correlated": correlated,
        "pending": pending,
        "overdue": overdue,
        "invalid": invalid,
        "valid_trials": valid_trials,
        "posterior_alpha": alpha,
        "posterior_beta": beta,
        "target_rate": config["target_rate"],
        "confidence": confidence,
        "min_trials": config["min_trials"],
        "graduated": graduated,
    }


def build_report(tracker: dict[str, Any]) -> dict[str, Any]:
    stages = [summarize_stage(tracker, stage) for stage in STAGE_ORDER]
    independent_joint_confidence = math.prod(stage["confidence"] for stage in stages)
    joint_confidence_lower_bound = max(
        0.0,
        1.0 - math.fsum(1.0 - stage["confidence"] for stage in stages),
    )
    joint_confidence_upper_bound = min(stage["confidence"] for stage in stages)
    all_stages_graduated = all(stage["graduated"] for stage in stages)
    overall_stop = all_stages_graduated and joint_confidence_lower_bound > float(
        tracker["stop_confidence"]
    )
    next_stage = next(
        (stage["stage"] for stage in stages if not stage["graduated"]),
        None,
    )
    if not overall_stop and next_stage is None:
        next_stage = min(stages, key=lambda stage: stage["confidence"])["stage"]
    pending_certifications = [
        {
            "stage": plan["stage"],
            "test_id": plan["test_id"],
            "due_at": plan["due_at"],
            "overdue": parse_aware_datetime(plan["due_at"]) <= datetime.now(timezone.utc),
        }
        for plan in tracker.get("certification_plans", [])
        if plan.get("status") == "pending"
    ]
    return {
        "capability": tracker["capability"],
        "decision": "STOP" if overall_stop else "CONTINUE",
        "stop_confidence": tracker["stop_confidence"],
        "all_stages_graduated": all_stages_graduated,
        "independent_joint_confidence": independent_joint_confidence,
        "joint_confidence_lower_bound": joint_confidence_lower_bound,
        "joint_confidence_upper_bound": joint_confidence_upper_bound,
        "pending_certifications": pending_certifications,
        "stages": stages,
        "next_stage": next_stage,
        "next_action": STAGE_CONFIG[next_stage]["next_action"] if next_stage else None,
    }


def render_report(report: dict[str, Any]) -> str:
    lines = [
        f"能力目标: {report['capability']}",
        "",
        "阶段       成功/失败  练习  相关  待/逾  无效  目标成功率  后验置信度  状态",
        "-" * 86,
    ]
    for stage in report["stages"]:
        status = "毕业" if stage["graduated"] else "继续"
        lines.append(
            f"{stage['label']:<10} "
            f"{stage['successes']:>2}/{stage['failures']:<2}     "
            f"{stage['practice']:>2}    "
            f"{stage['correlated']:>2}    "
            f"{stage['pending']:>1}/{stage['overdue']:<1}    "
            f"{stage['invalid']:>2}      "
            f"{stage['target_rate']:>6.0%}       "
            f"{stage['confidence']:>7.3f}       "
            f"{status}"
        )
    lines.extend(
        [
            "",
            f"联合概率(独立估计): {report['independent_joint_confidence']:.3f}",
            f"联合概率保守下界: {report['joint_confidence_lower_bound']:.3f}",
            f"总决策: {report['decision']}",
        ]
    )
    if report["next_action"]:
        lines.append(f"下一步: {report['next_action']}")
    else:
        lines.append("联合概率保守下界超过0.9, 且全部阶段满足样本门禁。")
    for plan in report["pending_certifications"]:
        plan_status = "逾期并已计失败" if plan["overdue"] else "等待到期"
        lines.append(
            f"认证计划: {plan['test_id']} ({plan['stage']}) {plan_status}, due={plan['due_at']}"
        )
    return "\n".join(lines)


def add_trial(tracker: dict[str, Any], args: argparse.Namespace) -> dict[str, Any]:
    preregistration_issues: list[str] = []
    revealed_prompt_hash = ""
    matched_plan: dict[str, Any] | None = None
    if args.certification and args.stage in PREREGISTRATION_STAGES:
        matched_plan = next(
            (
                plan
                for plan in tracker["certification_plans"]
                if plan.get("stage") == args.stage
                and plan.get("test_id") == args.test_id
                and plan.get("status") == "pending"
            ),
            None,
        )
        if matched_plan is None:
            preregistration_issues.append("没有匹配的待完成预注册")
        else:
            if datetime.now(timezone.utc) < parse_aware_datetime(matched_plan["due_at"]):
                preregistration_issues.append("认证测试尚未到预注册时间")
            if args.evidence_group != matched_plan["evidence_group"]:
                preregistration_issues.append("结果证据组与预注册不一致")
            prompt_file = getattr(args, "prompt_file", None)
            if prompt_file is None:
                preregistration_issues.append("结果提交缺少原题文件")
            elif not prompt_file.is_file():
                preregistration_issues.append("结果提交的原题文件不存在")
            else:
                revealed_prompt_hash = hashlib.sha256(prompt_file.read_bytes()).hexdigest()
                if revealed_prompt_hash != matched_plan["prompt_hash"]:
                    preregistration_issues.append("公开题目与预注册SHA-256不一致")

    preregistered = not preregistration_issues
    preregistration_issue = "; ".join(preregistration_issues)

    trial = {
        "recorded_at": utc_now(),
        "stage": args.stage,
        "result": args.result,
        "certification": args.certification,
        "test_id": args.test_id,
        "evidence_group": args.evidence_group,
        "preregistered": preregistered,
        "preregistration_issue": preregistration_issue,
        "revealed_prompt_hash": revealed_prompt_hash,
        "evidence": args.evidence,
        "source_checked": args.source_checked,
        "executable_checked": args.executable_checked,
        "counterexample_checked": args.counterexample_checked,
        "unassisted": args.unassisted,
        "delay_hours": args.delay_hours,
        "novel": args.novel,
        "timed": args.timed,
        "metric": args.metric,
        "accepted": args.accepted,
        "note": args.note,
    }
    duplicate_test_id = any(
        existing.get("certification")
        and existing.get("stage") == args.stage
        and str(existing.get("test_id", "")).strip() == args.test_id.strip()
        for existing in tracker["trials"]
        if args.test_id.strip()
    )
    duplicate_group = any(
        existing.get("certification")
        and existing.get("stage") == args.stage
        and str(existing.get("evidence_group", "")).strip() == args.evidence_group.strip()
        for existing in tracker["trials"]
        if args.evidence_group.strip()
    )
    valid, reasons = trial_validity(
        trial,
        duplicate_test_id=duplicate_test_id,
        duplicate_group=duplicate_group,
    )
    trial["valid_when_recorded"] = valid
    trial["invalid_reasons_when_recorded"] = reasons
    tracker["trials"].append(trial)
    if matched_plan is not None:
        matched_plan["status"] = "resolved"
        matched_plan["resolved_at"] = trial["recorded_at"]
        matched_plan["result"] = args.result
    return trial


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    init_parser = subparsers.add_parser("init", help="创建能力追踪文件")
    init_parser.add_argument("file", type=Path)
    init_parser.add_argument("--capability", required=True)

    add_parser = subparsers.add_parser("add", help="记录一次测试或业务实验")
    add_parser.add_argument("file", type=Path)
    add_parser.add_argument("--stage", choices=STAGE_ORDER, required=True)
    add_parser.add_argument("--result", choices=("pass", "fail"), required=True)
    add_parser.add_argument("--evidence", required=True)
    add_parser.add_argument(
        "--certification",
        action="store_true",
        help="标记为认证盲测; 未设置时仅作为练习日志",
    )
    add_parser.add_argument("--test-id", default="", help="认证盲测的唯一编号")
    add_parser.add_argument("--evidence-group", default="", help="独立证据所属分组")
    add_parser.add_argument("--prompt-file", type=Path, help="预注册时哈希承诺的原题文件")
    add_parser.add_argument("--source-checked", action="store_true")
    add_parser.add_argument("--executable-checked", action="store_true")
    add_parser.add_argument("--counterexample-checked", action="store_true")
    add_parser.add_argument("--unassisted", action="store_true")
    add_parser.add_argument("--delay-hours", type=float, default=0.0)
    add_parser.add_argument("--novel", action="store_true")
    add_parser.add_argument("--timed", action="store_true")
    add_parser.add_argument("--metric", default="")
    add_parser.add_argument("--accepted", action="store_true")
    add_parser.add_argument("--note", default="")

    prepare_parser = subparsers.add_parser("prepare", help="预注册一场认证测试")
    prepare_parser.add_argument("file", type=Path)
    prepare_parser.add_argument(
        "--stage", choices=tuple(sorted(PREREGISTRATION_STAGES)), required=True
    )
    prepare_parser.add_argument("--test-id", required=True)
    prepare_parser.add_argument("--due-at", required=True)
    prepare_parser.add_argument("--prompt-hash", required=True)
    prepare_parser.add_argument("--rubric", required=True)
    prepare_parser.add_argument("--evidence-group", required=True)

    hash_parser = subparsers.add_parser("hash-prompt", help="计算题目文件的SHA-256")
    hash_parser.add_argument("prompt_file", type=Path)

    report_parser = subparsers.add_parser("report", help="计算后验概率和下一步")
    report_parser.add_argument("file", type=Path)
    report_parser.add_argument("--json", action="store_true")
    return parser


def main() -> int:
    args = build_parser().parse_args()

    if args.command == "init":
        if args.file.exists():
            raise SystemExit(f"文件已存在, 不覆盖: {args.file}")
        tracker = new_tracker(args.capability)
        save_tracker(args.file, tracker)
        print(render_report(build_report(tracker)))
        return 0

    if args.command == "hash-prompt":
        print(hashlib.sha256(args.prompt_file.read_bytes()).hexdigest())
        return 0

    tracker = load_tracker(args.file)
    if args.command == "prepare":
        plan = prepare_certification(
            tracker,
            stage=args.stage,
            test_id=args.test_id,
            due_at=args.due_at,
            prompt_hash=args.prompt_hash,
            rubric=args.rubric,
            evidence_group=args.evidence_group,
        )
        save_tracker(args.file, tracker)
        print(json.dumps(plan, ensure_ascii=False, indent=2))
        return 0
    if args.command == "add":
        trial = add_trial(tracker, args)
        save_tracker(args.file, tracker)
        if not trial["certification"]:
            print("练习记录已保存, 不用于认证概率更新。")
        elif trial["valid_when_recorded"]:
            print("记录有效, 已用于贝叶斯更新。")
        else:
            reasons = "; ".join(trial["invalid_reasons_when_recorded"])
            print(f"记录已保存, 但不用于更新: {reasons}")
        print()
        print(render_report(build_report(tracker)))
        return 0

    report = build_report(tracker)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(render_report(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
