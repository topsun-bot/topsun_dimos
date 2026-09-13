from tools.capability_bayes.business_case import (
    business_success_probability,
    evaluate_business_case,
    new_business_case,
    permission_recommendation,
    validate_business_case,
)


def valid_case() -> dict:
    case = new_business_case("缩短机器人故障定位时间")
    case.update(
        {
            "problem": "人工分析运行日志耗时过长",
            "sponsor": "机器人平台负责人",
            "success_probability": 0.6,
            "implementation_cost": 2000.0,
            "failure_cost": 500.0,
            "acceptance_criteria": "连续20次诊断的中位时间小于30分钟",
            "attribution_method": "同一批回放数据进行A/B测试",
            "permission_ask": "两周沙箱和只读日志权限",
        }
    )
    case["metric"] = {
        "name": "每次故障定位分钟数",
        "direction": "decrease",
        "baseline": 120.0,
        "target": 30.0,
        "unit_value": 2.0,
        "volume_per_period": 20.0,
        "horizon_periods": 3,
    }
    return case


def test_positive_expected_value_case_is_runnable() -> None:
    result = evaluate_business_case(valid_case())

    assert result["decision"] == "RUN"
    assert result["gross_upside"] == 10800.0
    assert result["expected_value"] == 4280.0
    assert result["permission_recommendation"]["level"] == "L0"


def test_wrong_metric_direction_requires_revision() -> None:
    case = valid_case()
    case["metric"]["target"] = 150.0

    errors = validate_business_case(case)

    assert "decrease指标的target必须小于baseline" in errors


def test_missing_organizational_evidence_requires_revision() -> None:
    case = valid_case()
    case["sponsor"] = ""
    case["attribution_method"] = ""
    case["permission_ask"] = ""

    result = evaluate_business_case(case)

    assert result["decision"] == "REVISE"
    assert "sponsor不能为空" in result["errors"]
    assert "attribution_method不能为空" in result["errors"]
    assert "permission_ask不能为空" in result["errors"]


def test_negative_expected_value_requires_revision() -> None:
    case = valid_case()
    case["implementation_cost"] = 7000.0

    result = evaluate_business_case(case)

    assert result["decision"] == "REVISE"
    assert result["expected_value"] == -720.0


def test_permission_ladder_uses_verified_value_evidence() -> None:
    report = {
        "stages": [
            {"stage": "value", "successes": 3, "graduated": False},
            {"stage": "reward", "successes": 0, "graduated": False},
        ]
    }

    recommendation = permission_recommendation(report)

    assert recommendation["level"] == "L2"
    assert "生产所有权" in recommendation["ask"]


def test_tracker_posterior_overrides_optimistic_manual_probability() -> None:
    case = valid_case()
    case["success_probability"] = 0.99
    report = {
        "stages": [
            {
                "stage": "value",
                "successes": 0,
                "graduated": False,
                "posterior_alpha": 1,
                "posterior_beta": 1,
            },
            {"stage": "reward", "successes": 0, "graduated": False},
        ]
    }

    probability, source = business_success_probability(case, report)
    result = evaluate_business_case(case, report)

    assert probability == 0.5
    assert source == "value_stage_posterior_mean"
    assert result["success_probability"] == 0.5
    assert result["expected_value"] == 3150.0
