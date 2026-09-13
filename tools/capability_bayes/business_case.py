#!/usr/bin/env python3
"""Validate an AI-assisted business experiment and calculate expected value."""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any, cast

from tools.capability_bayes.tracker import build_report, load_tracker

SCHEMA_VERSION = 1
DIRECTIONS = ("increase", "decrease")


def new_business_case(name: str) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "name": name,
        "problem": "",
        "sponsor": "",
        "metric": {
            "name": "",
            "direction": "decrease",
            "baseline": 0.0,
            "target": 0.0,
            "unit_value": 0.0,
            "volume_per_period": 0.0,
            "horizon_periods": 1,
        },
        "success_probability": 0.5,
        "implementation_cost": 0.0,
        "failure_cost": 0.0,
        "acceptance_criteria": "",
        "attribution_method": "",
        "permission_ask": "",
        "income_ask": "",
    }


def load_business_case(path: Path) -> dict[str, Any]:
    case = cast("dict[str, Any]", json.loads(path.read_text(encoding="utf-8")))
    if case.get("schema_version") != SCHEMA_VERSION:
        raise ValueError(f"unsupported schema_version: {case.get('schema_version')}")
    return case


def save_business_case(path: Path, case: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(case, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _required_text(data: dict[str, Any], key: str, errors: list[str]) -> None:
    if not str(data.get(key, "")).strip():
        errors.append(f"{key}不能为空")


def _finite_number(
    data: dict[str, Any], key: str, errors: list[str], *, nonnegative: bool = False
) -> float | None:
    value = data.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        errors.append(f"{key}必须是数字")
        return None
    number = float(value)
    if not math.isfinite(number):
        errors.append(f"{key}必须是有限数字")
        return None
    if nonnegative and number < 0.0:
        errors.append(f"{key}不能为负数")
        return None
    return number


def validate_business_case(case: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    for field in (
        "name",
        "problem",
        "sponsor",
        "acceptance_criteria",
        "attribution_method",
        "permission_ask",
    ):
        _required_text(case, field, errors)

    metric = case.get("metric")
    if not isinstance(metric, dict):
        errors.append("metric必须是对象")
        return errors
    metric = cast("dict[str, Any]", metric)
    _required_text(metric, "name", errors)

    direction = metric.get("direction")
    if direction not in DIRECTIONS:
        errors.append("metric.direction必须是increase或decrease")

    baseline = _finite_number(metric, "baseline", errors)
    target = _finite_number(metric, "target", errors)
    unit_value = _finite_number(metric, "unit_value", errors, nonnegative=True)
    volume = _finite_number(metric, "volume_per_period", errors, nonnegative=True)
    horizon = _finite_number(metric, "horizon_periods", errors, nonnegative=True)
    probability = _finite_number(case, "success_probability", errors)
    _finite_number(case, "implementation_cost", errors, nonnegative=True)
    _finite_number(case, "failure_cost", errors, nonnegative=True)

    if probability is not None and not 0.0 <= probability <= 1.0:
        errors.append("success_probability必须在0和1之间")
    if horizon is not None and horizon < 1.0:
        errors.append("metric.horizon_periods必须至少为1")
    if unit_value == 0.0:
        errors.append("metric.unit_value必须大于0")
    if volume == 0.0:
        errors.append("metric.volume_per_period必须大于0")
    if baseline is not None and target is not None:
        if direction == "increase" and target <= baseline:
            errors.append("increase指标的target必须大于baseline")
        if direction == "decrease" and target >= baseline:
            errors.append("decrease指标的target必须小于baseline")
    return errors


def permission_recommendation(tracker_report: dict[str, Any] | None) -> dict[str, str]:
    if tracker_report is None:
        return {
            "level": "L0",
            "ask": "申请可回滚的沙箱、两周试点时间和一位验收人。",
        }

    stages = {stage["stage"]: stage for stage in tracker_report["stages"]}
    value = stages["value"]
    reward = stages["reward"]
    if reward["graduated"]:
        return {"level": "L4", "ask": "维持授权并定期重新校准能力和价值概率。"}
    if value["graduated"]:
        return {
            "level": "L3",
            "ask": "用多次KPI证据申请正式职责、预算、职级或薪酬调整。",
        }
    if value["successes"] >= 3:
        return {
            "level": "L2",
            "ask": "申请有边界的生产所有权、数据权限和小额预算。",
        }
    if value["successes"] >= 1:
        return {
            "level": "L1",
            "ask": "申请有限真实数据、受控用户和下一轮生产试点。",
        }
    return {
        "level": "L0",
        "ask": "申请可回滚的沙箱、两周试点时间和一位验收人。",
    }


def business_success_probability(
    case: dict[str, Any], tracker_report: dict[str, Any] | None
) -> tuple[float, str]:
    if tracker_report is None:
        return float(case["success_probability"]), "case_prior"

    value_stage = next(stage for stage in tracker_report["stages"] if stage["stage"] == "value")
    alpha = float(value_stage["posterior_alpha"])
    beta = float(value_stage["posterior_beta"])
    return alpha / (alpha + beta), "value_stage_posterior_mean"


def evaluate_business_case(
    case: dict[str, Any], tracker_report: dict[str, Any] | None = None
) -> dict[str, Any]:
    errors = validate_business_case(case)
    recommendation = permission_recommendation(tracker_report)
    if errors:
        return {
            "name": case.get("name", ""),
            "decision": "REVISE",
            "errors": errors,
            "permission_recommendation": recommendation,
        }

    metric = cast("dict[str, Any]", case["metric"])
    improvement = abs(float(metric["target"]) - float(metric["baseline"]))
    gross_upside = (
        improvement
        * float(metric["unit_value"])
        * float(metric["volume_per_period"])
        * float(metric["horizon_periods"])
    )
    probability, probability_source = business_success_probability(case, tracker_report)
    expected_value = (
        probability * gross_upside
        - float(case["implementation_cost"])
        - (1.0 - probability) * float(case["failure_cost"])
    )
    return {
        "name": case["name"],
        "decision": "RUN" if expected_value > 0.0 else "REVISE",
        "errors": [] if expected_value > 0.0 else ["期望价值不大于0"],
        "improvement": improvement,
        "gross_upside": gross_upside,
        "success_probability": probability,
        "probability_source": probability_source,
        "expected_value": expected_value,
        "permission_recommendation": recommendation,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    template_parser = subparsers.add_parser("template", help="创建业务实验模板")
    template_parser.add_argument("file", type=Path)
    template_parser.add_argument("--name", required=True)

    evaluate_parser = subparsers.add_parser("evaluate", help="校验并计算期望价值")
    evaluate_parser.add_argument("file", type=Path)
    evaluate_parser.add_argument("--tracker", type=Path)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.command == "template":
        if args.file.exists():
            raise SystemExit(f"文件已存在, 不覆盖: {args.file}")
        save_business_case(args.file, new_business_case(args.name))
        print(f"已创建模板: {args.file}")
        return 0

    case = load_business_case(args.file)
    tracker_report = None
    if args.tracker:
        tracker_report = build_report(load_tracker(args.tracker))
    result = evaluate_business_case(case, tracker_report)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["decision"] == "RUN" else 2


if __name__ == "__main__":
    raise SystemExit(main())
