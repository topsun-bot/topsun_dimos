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

"""ClosedLoopCritic: closed-loop evaluation system "judge".

模块定位
--------
纯逻辑组件, 不调 LLM, 不读盘, 不启动仿真. 给定真值轨迹与生成轨迹 (以及当前
迭代次数), 计算 ATE / Frechet / DTW 等指标 (委托给 ``dimos.utils.trajectory_metrics``),
按公差判断本轮结果应该 ``pass`` / ``retry`` / ``abort``, 并生成一段人话 feedback
喂给上游 LLM 用于下一轮重试.

接口契约
--------
轨迹张量遵循 plan 中 "C 节" 约定: ``np.ndarray`` shape ``(N, 3)``, 列为
``(x, y, z)``, 单位米.

调用方典型用法::

    critic = ClosedLoopCritic(Tolerances(ate_m=0.3, frechet_m=0.5), max_iters=5)
    for attempt in range(critic.max_iters):
        gen = run_one_attempt(...)
        decision = critic.decide(truth, gen, attempt)
        if decision.action != "retry":
            break
        feedback_for_llm = decision.feedback
"""

from dataclasses import dataclass
from typing import Any, Literal

import numpy as np

from dimos.utils.trajectory_metrics import summary

CriticAction = Literal["pass", "retry", "abort"]


@dataclass
class CriticDecision:
    """单轮裁判结果.

    Attributes:
        action: 本轮决策. ``"pass"`` 表示生成轨迹达标可结束闭环;
            ``"retry"`` 表示偏差仍大但仍有重试次数, 需要让 LLM 重新生成;
            ``"abort"`` 表示放弃 (要么轨迹无效, 要么用完重试次数).
        feedback: 人话总结, 会被原样塞回下一轮 LLM prompt. ``pass``/``abort``
            时也会写明原因, 方便上层报告引用.
        metrics: ``trajectory_metrics.summary`` 返回的指标字典; 轨迹无效时为
            空 dict.
        attempt_idx: 触发本次决策的迭代序号 (从 0 开始).
    """

    action: CriticAction
    feedback: str
    metrics: dict[str, Any]
    attempt_idx: int


@dataclass
class Tolerances:
    """各项相似度指标的通过阈值, 单位均为米.

    Attributes:
        ate_m: Absolute Trajectory Error (RMSE) 阈值.
        frechet_m: 离散 Frechet 距离阈值.
        dtw_m: DTW 累计距离阈值. ``None`` 表示不检查此项.
    """

    ate_m: float = 0.3
    frechet_m: float = 0.5
    dtw_m: float | None = None


@dataclass
class _DivergenceInfo:
    """对齐后两条轨迹偏差最大的索引区间, 仅用于内部生成 feedback."""

    start_idx: int = 0
    end_idx: int = 0
    max_distance: float = 0.0
    aligned_length: int = 0


class ClosedLoopCritic:
    """闭环评估系统的状态机.

    将一对 ``(truth, generated)`` 轨迹打分, 并依据公差与剩余重试次数输出
    ``pass`` / ``retry`` / ``abort`` 决策. 该类是无状态的 (多次 ``decide`` 之
    间不共享状态), 所有判断只依赖入参, 便于在 CLI 的 for 循环里复用.
    """

    def __init__(
        self,
        tolerances: Tolerances | None = None,
        max_iters: int = 5,
    ) -> None:
        """初始化裁判.

        Args:
            tolerances: 通过阈值. ``None`` 时使用默认值
                (``ate_m=0.3``, ``frechet_m=0.5``, ``dtw_m=None``).
            max_iters: 闭环最大迭代次数. 当 ``attempt_idx + 1 >= max_iters``
                且本轮仍未通过时返回 ``abort``. 必须 ``>= 1``.

        Raises:
            ValueError: ``max_iters`` 小于 1.
        """
        if max_iters < 1:
            raise ValueError(f"max_iters must be >= 1, got {max_iters}")
        self.tolerances = tolerances or Tolerances()
        self.max_iters = max_iters

    def decide(
        self,
        truth: np.ndarray,
        generated: np.ndarray,
        attempt_idx: int,
    ) -> CriticDecision:
        """对单轮生成轨迹打分并给出后续动作.

        Args:
            truth: 真值轨迹, shape ``(N, 3)``, 单位米.
            generated: 本轮生成轨迹, shape ``(M, 3)``, 单位米.
            attempt_idx: 当前迭代序号, 从 0 开始; 用于判断是否已达
                ``max_iters - 1`` (即最后一次机会).

        Returns:
            :class:`CriticDecision`: 本轮动作, 人话 feedback, 原始指标.
        """
        # 1) 轨迹有效性检查: 少于 2 个点的轨迹无法计算指标, 直接 abort
        if not self._is_valid_trajectory(truth) or not self._is_valid_trajectory(generated):
            return CriticDecision(
                action="abort",
                feedback="轨迹为空或太短 (少于 2 个点), 无法进行评估, 闭环终止.",
                metrics={},
                attempt_idx=attempt_idx,
            )

        # 2) 计算各项指标
        metrics = summary(truth, generated)

        # 3) 检查公差
        breaches = self._collect_breaches(metrics)
        if not breaches:
            return CriticDecision(
                action="pass",
                feedback=self._format_pass_feedback(metrics),
                metrics=metrics,
                attempt_idx=attempt_idx,
            )

        # 4) 超阈值但已是最后一次机会 -> abort
        if attempt_idx + 1 >= self.max_iters:
            return CriticDecision(
                action="abort",
                feedback=self._format_abort_feedback(metrics, breaches, attempt_idx),
                metrics=metrics,
                attempt_idx=attempt_idx,
            )

        # 5) 否则告诉 LLM 应该改什么
        return CriticDecision(
            action="retry",
            feedback=self._format_retry_feedback(truth, generated, breaches),
            metrics=metrics,
            attempt_idx=attempt_idx,
        )

    # ------------------------------------------------------------------
    # 内部工具方法
    # ------------------------------------------------------------------

    @staticmethod
    def _is_valid_trajectory(traj: np.ndarray) -> bool:
        """判断轨迹是否可用于度量. 要求形状为 ``(N, 3)`` 且 ``N >= 2``."""
        if traj is None:
            return False
        arr = np.asarray(traj)
        if arr.ndim != 2 or arr.shape[1] != 3:
            return False
        return bool(arr.shape[0] >= 2)

    def _collect_breaches(self, metrics: dict[str, Any]) -> list[tuple[str, float, float]]:
        """收集所有超阈值的指标, 每项为 ``(name, value, limit)``.

        ``dtw_m`` 为 ``None`` 时跳过对 DTW 的检查. 若指标字典中缺失某项或值
        为 ``NaN``/``Inf``, 也按 "超标" 处理 -- 直接对 NaN 做 ``> limit`` 会返回
        ``False`` (IEEE 754 语义), 会把无效结果当成 pass 偷偷放过, 所以
        先单独走 ``isfinite`` 这一支.
        """
        checks: list[tuple[str, float | None]] = [
            ("ate", self.tolerances.ate_m),
            ("frechet", self.tolerances.frechet_m),
            ("dtw", self.tolerances.dtw_m),
        ]
        breaches: list[tuple[str, float, float]] = []
        for name, limit in checks:
            if limit is None:
                continue
            value = metrics.get(name)
            numeric = float(value) if value is not None else float("nan")
            if not np.isfinite(numeric) or numeric > limit:
                breaches.append((name, numeric, limit))
        return breaches

    @staticmethod
    def _format_pass_feedback(metrics: dict[str, Any]) -> str:
        """生成 ``pass`` 时的 feedback 文本."""
        return (
            "轨迹评估通过: 所有指标均在公差范围内. "
            f"ATE={metrics.get('ate', float('nan')):.3f} m, "
            f"Frechet={metrics.get('frechet', float('nan')):.3f} m, "
            f"DTW={metrics.get('dtw', float('nan')):.3f} m."
        )

    def _format_abort_feedback(
        self,
        metrics: dict[str, Any],
        breaches: list[tuple[str, float, float]],
        attempt_idx: int,
    ) -> str:
        """生成 ``abort`` 时 (用完次数) 的 feedback 文本."""
        breach_desc = ", ".join(
            f"{name}={value:.3f}m (limit {limit:.3f}m)" for name, value, limit in breaches
        )
        return (
            f"已达最大迭代次数 {self.max_iters} (当前 attempt_idx={attempt_idx}), "
            f"仍有指标超阈值: {breach_desc}. 放弃闭环."
        )

    def _format_retry_feedback(
        self,
        truth: np.ndarray,
        generated: np.ndarray,
        breaches: list[tuple[str, float, float]],
    ) -> str:
        """生成 ``retry`` 时给 LLM 的人话 feedback.

        内容包含:
        1) 哪些指标超阈值 (具体数值 vs 限制);
        2) 对齐后偏差最大的轨迹段索引区间与最大距离;
        3) 整体偏移方向粗描 (首末点的平均偏移向量), 帮助 LLM 决定怎么改.
        """
        breach_lines = [
            f"  - {name}: {value:.3f} m (limit {limit:.3f} m, exceeds by {(value - limit):.3f} m)"
            for name, value, limit in breaches
        ]
        divergence = self._find_max_divergence(truth, generated)
        offset_desc = self._describe_overall_offset(truth, generated)
        return (
            "轨迹评估未通过, 需要重新生成.\n"
            "超阈值指标:\n" + "\n".join(breach_lines) + "\n"
            f"偏差最大的轨迹段: 对齐后索引 [{divergence.start_idx}, {divergence.end_idx}], "
            f"最大点对距离 {divergence.max_distance:.3f} m.\n"
            f"整体偏差: {offset_desc}\n"
            "建议: 根据上述偏差方向调整 skill 序列参数 (步长, 转角或顺序), "
            "尽量贴合真值轨迹."
        )

    @staticmethod
    def _find_max_divergence(truth: np.ndarray, generated: np.ndarray) -> _DivergenceInfo:
        """把两条轨迹按比例对齐到同样的长度后, 找点对距离最大的索引区间.

        采用最简单的 "按归一化进度线性插值" 方式重采样到 ``max(N, M, 2)`` 个点,
        然后在距离序列上取最大值附近 +/-10% 长度作为 "问题段". 这是给 LLM 看
        的提示, 不是严谨的对齐算法.
        """
        truth_arr = np.asarray(truth, dtype=float)
        gen_arr = np.asarray(generated, dtype=float)
        n = max(truth_arr.shape[0], gen_arr.shape[0], 2)
        truth_resampled = ClosedLoopCritic._resample(truth_arr, n)
        gen_resampled = ClosedLoopCritic._resample(gen_arr, n)
        distances = np.linalg.norm(truth_resampled - gen_resampled, axis=1)
        if distances.size == 0:
            return _DivergenceInfo()
        peak_idx = int(np.argmax(distances))
        half_window = max(1, n // 10)
        start = max(0, peak_idx - half_window)
        end = min(n - 1, peak_idx + half_window)
        return _DivergenceInfo(
            start_idx=start,
            end_idx=end,
            max_distance=float(distances[peak_idx]),
            aligned_length=n,
        )

    @staticmethod
    def _resample(traj: np.ndarray, n: int) -> np.ndarray:
        """按归一化进度把 ``traj`` 线性插值到 ``n`` 个点 (每个维度独立)."""
        m = traj.shape[0]
        if m == n:
            return traj.copy()
        src = np.linspace(0.0, 1.0, m)
        dst = np.linspace(0.0, 1.0, n)
        out = np.empty((n, traj.shape[1]), dtype=float)
        for d in range(traj.shape[1]):
            out[:, d] = np.interp(dst, src, traj[:, d])
        return out

    @staticmethod
    def _describe_overall_offset(truth: np.ndarray, generated: np.ndarray) -> str:
        """粗描整体偏移方向: 用首末点的差值平均估算一个 (dx, dy, dz) 偏移向量."""
        truth_arr = np.asarray(truth, dtype=float)
        gen_arr = np.asarray(generated, dtype=float)
        start_offset = gen_arr[0] - truth_arr[0]
        end_offset = gen_arr[-1] - truth_arr[-1]
        mean_offset = (start_offset + end_offset) / 2.0
        magnitude = float(np.linalg.norm(mean_offset))
        return (
            f"生成轨迹相对真值整体偏移约 {magnitude:.3f} m, "
            f"方向 (dx, dy, dz)=({mean_offset[0]:+.3f}, {mean_offset[1]:+.3f}, "
            f"{mean_offset[2]:+.3f}) m"
        )
