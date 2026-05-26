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

"""ClosedLoopCritic unit tests.

``trajectory_metrics.summary`` 由 Unit 3 实现, 本测试通过 ``monkeypatch.setattr``
mock 掉它, 所以本测试不依赖 Unit 3 的真实实现.
"""

import numpy as np
import pytest

import dimos.agents.closed_loop_critic as critic_mod
from dimos.agents.closed_loop_critic import ClosedLoopCritic, CriticDecision, Tolerances


def _line_trajectory(num_points: int = 10, length_m: float = 3.0) -> np.ndarray:
    """构造一条沿 x 轴均匀分布的 (N, 3) 轨迹, 便于测试用."""
    xs = np.linspace(0.0, length_m, num_points)
    ys = np.zeros_like(xs)
    zs = np.zeros_like(xs)
    return np.stack([xs, ys, zs], axis=1)


def _mock_summary(monkeypatch: pytest.MonkeyPatch, **values: float) -> None:
    """把 ``summary`` mock 成返回固定指标字典."""
    defaults = {"ate": 0.0, "frechet": 0.0, "dtw": 0.0}
    defaults.update(values)
    monkeypatch.setattr(
        critic_mod,
        "summary",
        lambda _truth, _pred: dict(defaults),
    )


# ---------------------------------------------------------------------------
# 核心 4 个用例 (plan 中明确要求)
# ---------------------------------------------------------------------------


def test_pass_when_trajectories_identical(monkeypatch: pytest.MonkeyPatch) -> None:
    """完全相同的两条轨迹 -> pass, feedback 含 "通过"."""
    _mock_summary(monkeypatch, ate=0.0, frechet=0.0, dtw=0.0)

    truth = _line_trajectory()
    critic = ClosedLoopCritic(Tolerances(ate_m=0.3, frechet_m=0.5), max_iters=5)
    decision = critic.decide(truth, truth.copy(), attempt_idx=0)

    assert isinstance(decision, CriticDecision)
    assert decision.action == "pass"
    assert "通过" in decision.feedback
    assert decision.metrics == {"ate": 0.0, "frechet": 0.0, "dtw": 0.0}
    assert decision.attempt_idx == 0


def test_retry_when_offset_one_meter(monkeypatch: pytest.MonkeyPatch) -> None:
    """整体平移 1m -> retry; feedback 必须提到 "偏移" 或 "距离"."""
    _mock_summary(monkeypatch, ate=1.0, frechet=1.0, dtw=10.0)

    truth = _line_trajectory()
    generated = truth + np.array([0.0, 1.0, 0.0])  # 整体沿 y 平移 1m
    critic = ClosedLoopCritic(Tolerances(ate_m=0.3, frechet_m=0.5), max_iters=5)
    decision = critic.decide(truth, generated, attempt_idx=0)

    assert decision.action == "retry"
    # feedback 必须包含 "偏移" 或 "距离" 中的至少一个
    assert ("偏移" in decision.feedback) or ("距离" in decision.feedback)
    # 也应该带出哪些指标超阈值
    assert "ate" in decision.feedback
    assert "frechet" in decision.feedback
    # 指标 dict 透传给上游
    assert decision.metrics["ate"] == 1.0
    assert decision.attempt_idx == 0


def test_abort_when_last_attempt_still_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    """attempt_idx == max_iters - 1 且偏差仍大 -> abort, feedback 含 "最大迭代"."""
    _mock_summary(monkeypatch, ate=2.0, frechet=2.5, dtw=20.0)

    truth = _line_trajectory()
    generated = truth + np.array([2.0, 0.0, 0.0])
    critic = ClosedLoopCritic(Tolerances(ate_m=0.3, frechet_m=0.5), max_iters=3)
    decision = critic.decide(truth, generated, attempt_idx=2)  # 最后一次

    assert decision.action == "abort"
    assert "最大迭代" in decision.feedback
    assert decision.attempt_idx == 2


def test_abort_when_truth_is_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    """truth 长度为 0 -> abort, feedback 含 "空".

    此用例不应触发 ``summary``, 因为有效性检查在前. 这里仍然 mock 避免误调.
    """
    summary_calls: list[tuple] = []

    def _spy_summary(truth: np.ndarray, pred: np.ndarray) -> dict:
        summary_calls.append((truth, pred))
        return {"ate": 0.0, "frechet": 0.0, "dtw": 0.0}

    monkeypatch.setattr(critic_mod, "summary", _spy_summary)

    truth = np.zeros((0, 3))
    generated = _line_trajectory()
    critic = ClosedLoopCritic(Tolerances(), max_iters=5)
    decision = critic.decide(truth, generated, attempt_idx=0)

    assert decision.action == "abort"
    assert "空" in decision.feedback
    assert decision.metrics == {}
    # 短路: summary 不应被调用
    assert summary_calls == []


# ---------------------------------------------------------------------------
# 额外用例: 覆盖边界条件
# ---------------------------------------------------------------------------


def test_abort_when_generated_too_short(monkeypatch: pytest.MonkeyPatch) -> None:
    """generated 只有 1 个点也算无效轨迹."""
    _mock_summary(monkeypatch)
    truth = _line_trajectory()
    generated = np.array([[0.0, 0.0, 0.0]])  # 仅 1 个点
    critic = ClosedLoopCritic(Tolerances(), max_iters=5)
    decision = critic.decide(truth, generated, attempt_idx=0)

    assert decision.action == "abort"
    assert "空" in decision.feedback or "短" in decision.feedback


def test_dtw_tolerance_none_skips_dtw_check(monkeypatch: pytest.MonkeyPatch) -> None:
    """``Tolerances.dtw_m = None`` 时, 即使 DTW 很大也不算超标."""
    _mock_summary(monkeypatch, ate=0.1, frechet=0.2, dtw=1_000_000.0)
    truth = _line_trajectory()
    critic = ClosedLoopCritic(Tolerances(ate_m=0.3, frechet_m=0.5, dtw_m=None), max_iters=5)
    decision = critic.decide(truth, truth.copy(), attempt_idx=0)
    assert decision.action == "pass"


def test_dtw_tolerance_enforced_when_set(monkeypatch: pytest.MonkeyPatch) -> None:
    """显式设置 ``dtw_m`` 后必须被检查."""
    _mock_summary(monkeypatch, ate=0.1, frechet=0.2, dtw=5.0)
    truth = _line_trajectory()
    critic = ClosedLoopCritic(Tolerances(ate_m=0.3, frechet_m=0.5, dtw_m=1.0), max_iters=5)
    decision = critic.decide(truth, truth.copy(), attempt_idx=0)
    assert decision.action == "retry"
    assert "dtw" in decision.feedback


def test_retry_when_not_last_attempt(monkeypatch: pytest.MonkeyPatch) -> None:
    """attempt_idx + 1 < max_iters 且超阈值 -> retry (不是 abort)."""
    _mock_summary(monkeypatch, ate=1.0, frechet=1.0, dtw=10.0)
    truth = _line_trajectory()
    critic = ClosedLoopCritic(Tolerances(ate_m=0.3, frechet_m=0.5), max_iters=5)
    decision = critic.decide(truth, truth.copy(), attempt_idx=0)
    assert decision.action == "retry"
    decision = critic.decide(truth, truth.copy(), attempt_idx=3)  # 4-th, 还差最后 1 次
    assert decision.action == "retry"
    decision = critic.decide(truth, truth.copy(), attempt_idx=4)  # 第 5 次 = 最后
    assert decision.action == "abort"


def test_nan_metric_treated_as_breach(monkeypatch: pytest.MonkeyPatch) -> None:
    """指标返回 NaN 应被视作超标, 避免悄悄通过."""
    _mock_summary(monkeypatch, ate=float("nan"), frechet=0.1, dtw=0.0)
    truth = _line_trajectory()
    critic = ClosedLoopCritic(Tolerances(ate_m=0.3, frechet_m=0.5), max_iters=5)
    decision = critic.decide(truth, truth.copy(), attempt_idx=0)
    assert decision.action == "retry"
    assert "ate" in decision.feedback


def test_max_iters_must_be_positive() -> None:
    """``max_iters < 1`` 应直接 raise."""
    with pytest.raises(ValueError):
        ClosedLoopCritic(max_iters=0)


def test_default_tolerances_used_when_none() -> None:
    """构造时传 ``None`` 使用默认 ``Tolerances``."""
    critic = ClosedLoopCritic(tolerances=None)
    assert critic.tolerances.ate_m == pytest.approx(0.3)
    assert critic.tolerances.frechet_m == pytest.approx(0.5)
    assert critic.tolerances.dtw_m is None


def test_decision_dataclass_fields() -> None:
    """简单 sanity check: CriticDecision 字段顺序与默认值."""
    d = CriticDecision(action="pass", feedback="ok", metrics={"ate": 0.0}, attempt_idx=0)
    assert d.action == "pass"
    assert d.feedback == "ok"
    assert d.metrics == {"ate": 0.0}
    assert d.attempt_idx == 0
