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

"""轨迹相似度指标库 (trajectory similarity metrics).

用于闭环评估管线 (closed-loop evaluation pipeline) 对比 AI 生成轨迹与真值轨迹.

所有指标输入约定:
- 形状 (N, 3) 的 ``np.ndarray``, 表示 (x, y, z) 时间序列 (单位: 米).
- 不含时间戳; 时间戳在调用方分离维护.

仅依赖 numpy, 不引入 scipy.
"""

from __future__ import annotations

from typing import Any

import numpy as np

# -----------------------------------------------------------------------------
# 内部工具
# -----------------------------------------------------------------------------


def _validate_traj(traj: np.ndarray, name: str = "traj") -> np.ndarray:
    """校验轨迹形状并转成 float64.

    Args:
        traj: 轨迹 ndarray.
        name: 出错时用的变量名.

    Returns:
        float64 的 (N, 3) 数组 (必要时拷贝).

    Raises:
        ValueError: 形状不是 (N, 3) 或 N == 0.
        TypeError: 不是 ndarray.
    """
    if not isinstance(traj, np.ndarray):
        raise TypeError(f"{name} 必须是 np.ndarray, 得到 {type(traj).__name__}")
    if traj.ndim != 2 or traj.shape[1] != 3:
        raise ValueError(f"{name} 形状必须是 (N, 3), 得到 {traj.shape}")
    if traj.shape[0] == 0:
        raise ValueError(f"{name} 不能为空 (N == 0)")
    return np.asarray(traj, dtype=np.float64)


def _path_length(traj: np.ndarray) -> float:
    """累积折线段长度 (米). N == 1 时返回 0."""
    if traj.shape[0] < 2:
        return 0.0
    deltas = np.diff(traj, axis=0)
    seg = np.linalg.norm(deltas, axis=1)
    return float(np.sum(seg))


# -----------------------------------------------------------------------------
# 重采样
# -----------------------------------------------------------------------------


def resample_to_length(traj: np.ndarray, n: int) -> np.ndarray:
    """把 (N, 3) 轨迹用线性插值重采样到 (n, 3).

    使用原始索引 ``[0, N-1]`` 到目标索引 ``[0, n-1]`` 的等距线性插值,
    对 x/y/z 三个维度独立处理. 当 N == 1 时, 直接复制该点 n 次.

    Args:
        traj: 输入轨迹, 形状 (N, 3).
        n: 目标长度, 必须 >= 1.

    Returns:
        形状 (n, 3) 的 float64 轨迹.

    Raises:
        ValueError: traj 形状不合法或 n < 1.
    """
    traj = _validate_traj(traj, "traj")
    if n < 1:
        raise ValueError(f"目标长度 n 必须 >= 1, 得到 {n}")

    n_src = traj.shape[0]
    if n_src == n:
        return traj.copy()
    if n_src == 1:
        return np.broadcast_to(traj, (n, 3)).copy()
    if n == 1:
        # 简单返回首点, 避免歧义.
        return traj[:1].copy()

    src_idx = np.arange(n_src, dtype=np.float64)
    dst_idx = np.linspace(0.0, n_src - 1, n, dtype=np.float64)
    out = np.empty((n, 3), dtype=np.float64)
    for axis in range(3):
        out[:, axis] = np.interp(dst_idx, src_idx, traj[:, axis])
    return out


# -----------------------------------------------------------------------------
# ATE: Absolute Trajectory Error
# -----------------------------------------------------------------------------


def ate(truth: np.ndarray, pred: np.ndarray) -> float:
    """绝对轨迹误差 (Absolute Trajectory Error), 逐点欧氏距离的 RMSE.

    数学定义:

    .. math::

        \\mathrm{ATE} = \\sqrt{\\frac{1}{N} \\sum_{i=1}^{N} \\| p_i - q_i \\|_2^2}

    若两条轨迹长度不同, 先用 :func:`resample_to_length` 把两条都对齐到
    ``min(len(truth), len(pred))``. 返回单位: 米.

    Args:
        truth: 真值轨迹, 形状 (N, 3).
        pred: 预测轨迹, 形状 (M, 3).

    Returns:
        ATE 标量 (米), 非负.
    """
    truth = _validate_traj(truth, "truth")
    pred = _validate_traj(pred, "pred")

    n = min(truth.shape[0], pred.shape[0])
    t = resample_to_length(truth, n) if truth.shape[0] != n else truth
    p = resample_to_length(pred, n) if pred.shape[0] != n else pred

    diff = t - p
    sq = np.sum(diff * diff, axis=1)
    return float(np.sqrt(np.mean(sq)))


# -----------------------------------------------------------------------------
# 离散 Fréchet 距离
# -----------------------------------------------------------------------------


def frechet_distance(truth: np.ndarray, pred: np.ndarray) -> float:
    """离散 Fréchet 距离 (discrete Fréchet distance), 单位米.

    经典定义 (Eiter & Mannila 1994 的迭代式 DP): 将 ``ca[i, j]`` 定义为
    把 ``truth[:i+1]`` 与 ``pred[:j+1]`` 走完所需的最小 "绳长" ---
    人和狗各自只能向前走或停步时所需的最短绳长上界:

    .. math::

        ca[i, j] = \\max\\bigl(\\min(ca[i-1, j],\\, ca[i-1, j-1],\\, ca[i, j-1]),\\,
                              \\| truth_i - pred_j \\|\\bigr)

    最终结果 ``ca[N-1, M-1]``.

    复杂度 O(N*M) 时间和空间; 适用于几千点以内的轨迹.
    对路径方向敏感 (点序反转通常会导致结果变大).

    Args:
        truth: 真值轨迹 (N, 3).
        pred: 预测轨迹 (M, 3).

    Returns:
        Fréchet 距离 (米), 非负.
    """
    truth = _validate_traj(truth, "truth")
    pred = _validate_traj(pred, "pred")

    n = truth.shape[0]
    m = pred.shape[0]

    # 预计算成对距离矩阵 dist[i, j] = ||truth_i - pred_j||
    # 通过广播一次性算完, 避免内层循环开销.
    diff = truth[:, None, :] - pred[None, :, :]  # (N, M, 3)
    dist = np.sqrt(np.sum(diff * diff, axis=2))  # (N, M)

    ca = np.empty((n, m), dtype=np.float64)
    ca[0, 0] = dist[0, 0]

    # 第一列: 只能从上面下来
    for i in range(1, n):
        ca[i, 0] = max(ca[i - 1, 0], dist[i, 0])
    # 第一行: 只能从左边过来
    for j in range(1, m):
        ca[0, j] = max(ca[0, j - 1], dist[0, j])
    # 主体 DP
    for i in range(1, n):
        for j in range(1, m):
            ca[i, j] = max(
                min(ca[i - 1, j], ca[i - 1, j - 1], ca[i, j - 1]),
                dist[i, j],
            )

    return float(ca[n - 1, m - 1])


# -----------------------------------------------------------------------------
# DTW: Dynamic Time Warping
# -----------------------------------------------------------------------------


def dtw_distance(truth: np.ndarray, pred: np.ndarray) -> float:
    """动态时间规整距离 (Dynamic Time Warping).

    经典 DP, cost 用欧氏距离, 过渡步集合为
    ``{(-1, 0), (0, -1), (-1, -1)}`` (即上、左、左上).
    返回值定义为 **累积路径距离 / 路径长度** (warping path 上的步数),
    单位约等于 "米/对齐对" --- 可以直接和 ATE 比较量级.

    路径长度的下界为 ``max(N, M)``, 上界为 ``N + M - 1``, 这里用真实步数
    回溯还原, 避免大幅度膨胀.

    复杂度 O(N*M) 时间和空间. DTW 对路径方向不敏感 (可以贪心地 "卡住"
    在原地等对方), 因此对反向同一条路径的距离通常远小于 Fréchet.

    Args:
        truth: 真值轨迹 (N, 3).
        pred: 预测轨迹 (M, 3).

    Returns:
        归一化累积距离 (米/帧), 非负.
    """
    truth = _validate_traj(truth, "truth")
    pred = _validate_traj(pred, "pred")

    n = truth.shape[0]
    m = pred.shape[0]

    # 成对欧氏距离矩阵
    diff = truth[:, None, :] - pred[None, :, :]
    cost = np.sqrt(np.sum(diff * diff, axis=2))  # (N, M)

    # DP 累积代价矩阵
    dp = np.full((n, m), np.inf, dtype=np.float64)
    dp[0, 0] = cost[0, 0]
    for i in range(1, n):
        dp[i, 0] = dp[i - 1, 0] + cost[i, 0]
    for j in range(1, m):
        dp[0, j] = dp[0, j - 1] + cost[0, j]
    for i in range(1, n):
        for j in range(1, m):
            dp[i, j] = cost[i, j] + min(
                dp[i - 1, j],
                dp[i, j - 1],
                dp[i - 1, j - 1],
            )

    # 回溯 warping path 拿到真实步数
    i, j = n - 1, m - 1
    steps = 1
    while i > 0 or j > 0:
        if i == 0:
            j -= 1
        elif j == 0:
            i -= 1
        else:
            candidates = (dp[i - 1, j - 1], dp[i - 1, j], dp[i, j - 1])
            choice = int(np.argmin(candidates))
            if choice == 0:
                i -= 1
                j -= 1
            elif choice == 1:
                i -= 1
            else:
                j -= 1
        steps += 1

    return float(dp[n - 1, m - 1] / steps)


# -----------------------------------------------------------------------------
# 一次性汇总
# -----------------------------------------------------------------------------


def summary(truth: np.ndarray, pred: np.ndarray) -> dict[str, Any]:
    """一次性返回所有指标 + 元数据, 方便上层日志/报告.

    Args:
        truth: 真值轨迹 (N, 3).
        pred: 预测轨迹 (M, 3).

    Returns:
        含以下 key 的 dict (数值均为 float / int):

        - ``ate`` (float, 米): :func:`ate` 的结果.
        - ``frechet`` (float, 米): :func:`frechet_distance` 的结果.
        - ``dtw`` (float, 米/帧): :func:`dtw_distance` 的归一化结果.
        - ``truth_len`` (int): 真值轨迹点数.
        - ``pred_len`` (int): 预测轨迹点数.
        - ``path_length_truth`` (float, 米): 真值折线段总长.
        - ``path_length_pred`` (float, 米): 预测折线段总长.
    """
    truth = _validate_traj(truth, "truth")
    pred = _validate_traj(pred, "pred")

    return {
        "ate": ate(truth, pred),
        "frechet": frechet_distance(truth, pred),
        "dtw": dtw_distance(truth, pred),
        "truth_len": int(truth.shape[0]),
        "pred_len": int(pred.shape[0]),
        "path_length_truth": _path_length(truth),
        "path_length_pred": _path_length(pred),
    }
