from argparse import Namespace
import hashlib
from pathlib import Path

import pytest

from tools.capability_bayes.tracker import (
    STAGE_CONFIG,
    STAGE_ORDER,
    add_trial,
    beta_tail_probability,
    build_report,
    new_tracker,
    prepare_certification,
    summarize_stage,
)


def make_args(stage: str, result: str = "pass", test_id: str = "test-001") -> Namespace:
    return Namespace(
        stage=stage,
        result=result,
        certification=True,
        test_id=test_id,
        evidence_group=test_id,
        prompt_file=Path(__file__),
        evidence="reviewable artifact",
        source_checked=True,
        executable_checked=True,
        counterexample_checked=False,
        unassisted=True,
        delay_hours=24.0,
        novel=True,
        timed=True,
        metric="lead time: 10h -> 6h",
        accepted=True,
        note="",
    )


def prepare_test(tracker: dict, stage: str, test_id: str) -> None:
    if stage == "ai_quality":
        return
    prepare_certification(
        tracker,
        stage=stage,
        test_id=test_id,
        due_at="2000-01-01T00:00:00+00:00",
        prompt_hash=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        rubric="predefined rubric",
        evidence_group=test_id,
    )


def add_certified_trial(
    tracker: dict, stage: str, result: str = "pass", test_id: str = "test-001"
) -> dict:
    prepare_test(tracker, stage, test_id)
    return add_trial(tracker, make_args(stage, result=result, test_id=test_id))


def test_beta_tail_matches_known_uniform_prior_examples() -> None:
    assert beta_tail_probability(0.8, 1, 1) == pytest.approx(0.2)
    assert beta_tail_probability(0.8, 11, 1) == pytest.approx(0.91410065408)
    assert beta_tail_probability(0.8, 9, 3) == pytest.approx(0.3825984512)


def test_assisted_immediate_memory_is_logged_but_not_counted() -> None:
    tracker = new_tracker("AI辅助能力")
    args = make_args("recall")
    prepare_test(tracker, "recall", args.test_id)
    args.unassisted = False
    args.delay_hours = 0.0

    trial = add_trial(tracker, args)
    summary = summarize_stage(tracker, "recall")

    assert trial["valid_when_recorded"] is False
    assert summary["valid_trials"] == 0
    assert summary["invalid"] == 1
    assert summary["confidence"] == pytest.approx(0.2)


def test_practice_is_logged_but_never_updates_certification_posterior() -> None:
    tracker = new_tracker("AI辅助能力")
    for index in range(20):
        args = make_args("recall", test_id=f"practice-{index}")
        args.certification = False
        add_trial(tracker, args)

    summary = summarize_stage(tracker, "recall")

    assert summary["practice"] == 20
    assert summary["valid_trials"] == 0
    assert summary["confidence"] == pytest.approx(0.2)


def test_duplicate_certification_id_cannot_inflate_posterior() -> None:
    tracker = new_tracker("AI辅助能力")
    add_certified_trial(tracker, "recall", test_id="same-test")
    for _ in range(9):
        add_trial(tracker, make_args("recall", test_id="same-test"))

    summary = summarize_stage(tracker, "recall")

    assert summary["successes"] == 1
    assert summary["invalid"] == 9
    assert summary["graduated"] is False


def test_related_outputs_in_same_evidence_group_count_once() -> None:
    tracker = new_tracker("AI辅助能力")
    first = make_args("ai_quality", test_id="revision-1")
    second = make_args("ai_quality", test_id="revision-2")
    first.evidence_group = "same-artifact"
    second.evidence_group = "same-artifact"

    add_trial(tracker, first)
    add_trial(tracker, second)
    summary = summarize_stage(tracker, "ai_quality")

    assert summary["successes"] == 1
    assert summary["correlated"] == 1
    assert summary["invalid"] == 1


def test_unverified_ai_output_cannot_update_ai_quality_posterior() -> None:
    tracker = new_tracker("AI辅助能力")
    args = make_args("ai_quality", test_id="ai-output-001")
    args.source_checked = False
    args.executable_checked = False

    trial = add_trial(tracker, args)
    summary = summarize_stage(tracker, "ai_quality")

    assert trial["valid_when_recorded"] is False
    assert summary["valid_trials"] == 0
    assert summary["confidence"] == pytest.approx(0.1)


def test_twenty_one_verified_ai_outputs_clear_ai_quality_gate() -> None:
    tracker = new_tracker("AI辅助能力")
    for index in range(21):
        add_trial(
            tracker,
            make_args("ai_quality", test_id=f"ai-output-{index}"),
        )

    summary = summarize_stage(tracker, "ai_quality")

    assert summary["confidence"] == pytest.approx(1.0 - 0.9**22)
    assert summary["graduated"] is True


def test_ten_clean_recall_passes_clear_recall_gate() -> None:
    tracker = new_tracker("AI辅助能力")
    for index in range(10):
        add_certified_trial(tracker, "recall", test_id=f"recall-{index}")

    summary = summarize_stage(tracker, "recall")

    assert summary["confidence"] == pytest.approx(0.91410065408)
    assert summary["graduated"] is True
    assert build_report(tracker)["decision"] == "CONTINUE"


def test_stage_minimums_do_not_falsely_claim_global_stop() -> None:
    tracker = new_tracker("AI辅助能力")
    for stage in STAGE_ORDER:
        for index in range(STAGE_CONFIG[stage]["min_trials"]):
            add_certified_trial(tracker, stage, test_id=f"{stage}-{index}")

    report = build_report(tracker)

    assert all(stage["confidence"] > 0.9 for stage in report["stages"])
    assert all(stage["graduated"] for stage in report["stages"])
    assert report["all_stages_graduated"] is True
    assert report["joint_confidence_lower_bound"] < 0.9
    assert report["decision"] == "CONTINUE"


def test_overall_stop_requires_joint_lower_bound_above_threshold() -> None:
    tracker = new_tracker("AI辅助能力")
    success_counts = {
        "ai_quality": 38,
        "recall": 18,
        "transfer": 18,
        "pressure": 11,
        "value": 8,
        "reward": 5,
    }
    for stage, count in success_counts.items():
        for index in range(count):
            add_certified_trial(tracker, stage, test_id=f"{stage}-{index}")

    report = build_report(tracker)

    assert report["joint_confidence_lower_bound"] > 0.9
    assert report["decision"] == "STOP"
    assert report["next_action"] is None


def test_human_certification_without_preregistration_is_invalid() -> None:
    tracker = new_tracker("AI辅助能力")

    trial = add_trial(tracker, make_args("recall", test_id="unplanned"))
    summary = summarize_stage(tracker, "recall")

    assert trial["valid_when_recorded"] is False
    assert "没有匹配的待完成预注册" in trial["invalid_reasons_when_recorded"]
    assert summary["valid_trials"] == 0


def test_certification_cannot_be_resolved_before_due_time() -> None:
    tracker = new_tracker("AI辅助能力")
    prepare_certification(
        tracker,
        stage="recall",
        test_id="future-test",
        due_at="2999-01-01T00:00:00+00:00",
        prompt_hash="b" * 64,
        rubric="predefined rubric",
        evidence_group="future-test",
    )

    trial = add_trial(tracker, make_args("recall", test_id="future-test"))

    assert trial["valid_when_recorded"] is False
    assert "认证测试尚未到预注册时间" in trial["invalid_reasons_when_recorded"]
    assert tracker["certification_plans"][0]["status"] == "resolved"


def test_revealed_prompt_must_match_preregistered_hash() -> None:
    tracker = new_tracker("AI辅助能力")
    prepare_certification(
        tracker,
        stage="recall",
        test_id="changed-prompt",
        due_at="2000-01-01T00:00:00+00:00",
        prompt_hash="b" * 64,
        rubric="predefined rubric",
        evidence_group="changed-prompt",
    )

    trial = add_trial(tracker, make_args("recall", test_id="changed-prompt"))

    assert trial["valid_when_recorded"] is False
    assert "公开题目与预注册SHA-256不一致" in trial["invalid_reasons_when_recorded"]


def test_result_without_original_prompt_file_is_invalid() -> None:
    tracker = new_tracker("AI辅助能力")
    prepare_test(tracker, "recall", "missing-prompt")
    args = make_args("recall", test_id="missing-prompt")
    args.prompt_file = None

    trial = add_trial(tracker, args)

    assert trial["valid_when_recorded"] is False
    assert "结果提交缺少原题文件" in trial["invalid_reasons_when_recorded"]


def test_future_pending_plan_does_not_change_posterior() -> None:
    tracker = new_tracker("AI辅助能力")
    prepare_certification(
        tracker,
        stage="recall",
        test_id="future-pending",
        due_at="2999-01-01T00:00:00+00:00",
        prompt_hash="c" * 64,
        rubric="predefined rubric",
        evidence_group="future-pending",
    )

    summary = summarize_stage(tracker, "recall")

    assert summary["pending"] == 1
    assert summary["overdue"] == 0
    assert summary["failures"] == 0
    assert summary["confidence"] == pytest.approx(0.2)


def test_overdue_unresolved_plan_is_counted_as_failure() -> None:
    tracker = new_tracker("AI辅助能力")
    prepare_certification(
        tracker,
        stage="recall",
        test_id="overdue",
        due_at="2000-01-01T00:00:00+00:00",
        prompt_hash="d" * 64,
        rubric="predefined rubric",
        evidence_group="overdue",
    )

    summary = summarize_stage(tracker, "recall")

    assert summary["pending"] == 0
    assert summary["overdue"] == 1
    assert summary["failures"] == 1
    assert summary["posterior_beta"] == 2
