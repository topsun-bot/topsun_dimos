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

"""轨迹对比 metrics — 用 memory2 SqliteStore 读 odom 流，纯 numpy 计算。

设计意图见 CODEGEN_LOOP_SPEC.md §2 Step ③。
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
import math
from typing import Any, cast

import numpy as np
from numpy.typing import NDArray

from dimos.memory2.store.sqlite import SqliteStore
from dimos.memory2.type.observation import Observation

Trajectory = NDArray[np.float64]


def _trajectory(value: np.ndarray) -> Trajectory:
    """将输入规范为 ``(N, 4)`` 轨迹数组。"""
    arr = np.asarray(value, dtype=np.float64)
    if arr.size == 0:
        return np.empty((0, 4), dtype=np.float64)
    if arr.ndim != 2 or arr.shape[1] != 4:
        raise ValueError(f"trajectory must have shape (N, 4), got {arr.shape}")
    return arr


def load_traj(db_path: str) -> Trajectory:
    """读 odom 流 → (N, 4) numpy: [ts, x, y, yaw]，按 ts 升序."""
    store = SqliteStore(path=db_path, must_exist=True)
    store.start()
    rows: list[tuple[float, float, float, float]] = []
    try:
        observations = cast(
            "Iterator[Observation[Any]]",
            store.stream("odom").materialize(),
        )
        for obs in observations:
            pose: Any = obs.data
            rows.append((float(obs.ts), float(pose.x), float(pose.y), float(pose.yaw)))
    finally:
        store.stop()
    if not rows:
        return np.empty((0, 4), dtype=np.float64)
    arr = np.asarray(rows, dtype=np.float64)
    return arr[np.argsort(arr[:, 0])]


def _nearest_time_indices(
    timestamps: NDArray[np.float64],
    targets: NDArray[np.float64],
) -> NDArray[np.intp]:
    right = np.searchsorted(timestamps, targets).clip(0, len(timestamps) - 1)
    left = (right - 1).clip(0, len(timestamps) - 1)
    use_left = np.abs(timestamps[left] - targets) < np.abs(timestamps[right] - targets)
    return np.where(use_left, left, right)


def align(
    gt: np.ndarray,
    sim: np.ndarray,
    tolerance_s: float = 0.2,
) -> tuple[Trajectory, Trajectory, NDArray[np.bool_]]:
    """按各自首帧后的相对时间对齐 GT 与 sim。

    现场真值与后续仿真必然来自不同 wall-clock 时段，因此不能直接比较
    绝对时间戳。返回值仍保留原始时间戳，便于在报告中定位真值帧。
    """
    if tolerance_s < 0:
        raise ValueError("tolerance_s must be non-negative")

    gt_arr = _trajectory(gt)
    sim_arr = _trajectory(sim)
    kept = np.zeros(len(gt_arr), dtype=np.bool_)
    if len(gt_arr) == 0 or len(sim_arr) == 0:
        return gt_arr[:0], sim_arr[:0], kept

    gt_ts = gt_arr[:, 0] - gt_arr[0, 0]
    sim_ts = sim_arr[:, 0] - sim_arr[0, 0]
    idx = _nearest_time_indices(sim_ts, gt_ts)
    kept = np.abs(sim_ts[idx] - gt_ts) <= tolerance_s
    return gt_arr[kept], sim_arr[idx[kept]], kept


def ate(gt: np.ndarray, sim: np.ndarray, tolerance_s: float = 0.2) -> float:
    """Absolute Trajectory Error: 对齐后 (gt_xy - sim_xy) 的 RMSE."""
    g, s, _ = align(gt, sim, tolerance_s)
    if len(g) == 0:
        return float("inf")
    err = np.linalg.norm(g[:, 1:3] - s[:, 1:3], axis=1)
    return float(np.sqrt((err**2).mean()))


def rpe(
    gt: np.ndarray,
    sim: np.ndarray,
    delta_s: float = 1.0,
    tolerance_s: float = 0.2,
) -> float:
    """Relative Pose Error: 时间窗 delta_s 内相对位移之差的 RMSE.

    RPE 对常数平移免疫，适合 sim 与 GT 起始位姿对不齐的场景。
    """
    if delta_s <= 0:
        raise ValueError("delta_s must be positive")
    g, s, _ = align(gt, sim, tolerance_s)
    if len(g) < 2:
        return float("inf")
    target = g[:, 0] + delta_s
    j = _nearest_time_indices(g[:, 0], target)
    keep = (np.abs(g[j, 0] - target) <= tolerance_s) & (j > np.arange(len(g)))
    if keep.sum() == 0:
        return float("inf")
    dg = g[j][keep, 1:3] - g[:, 1:3][keep]
    ds = s[j][keep, 1:3] - s[:, 1:3][keep]
    err = np.linalg.norm(dg - ds, axis=1)
    return float(np.sqrt((err**2).mean()))


def _waypoints_by_arclength(gt: Trajectory, k: int) -> NDArray[np.float64]:
    """Interpolate exactly ``k`` XY waypoints at uniform path-length intervals."""
    xy = gt[:, 1:3]
    segment_lengths = np.linalg.norm(np.diff(xy, axis=0), axis=1)
    arclength = np.concatenate((np.zeros(1, dtype=np.float64), np.cumsum(segment_lengths)))
    keep = np.concatenate((np.ones(1, dtype=np.bool_), np.diff(arclength) > 1e-9))
    unique_arclength = arclength[keep]
    unique_xy = xy[keep]
    if unique_arclength[-1] <= 1e-9:
        return np.repeat(unique_xy[:1], k, axis=0)
    targets = np.linspace(0.0, unique_arclength[-1], k)
    return np.column_stack(
        (
            np.interp(targets, unique_arclength, unique_xy[:, 0]),
            np.interp(targets, unique_arclength, unique_xy[:, 1]),
        )
    )


def hit_rate(
    gt: np.ndarray,
    sim: np.ndarray,
    k: int = 10,
    tolerance_m: float = 1.5,
) -> float:
    """从 GT 等距采 k 个 waypoint，统计 sim 是否按顺序通过."""
    if k <= 0:
        raise ValueError("k must be positive")
    if tolerance_m < 0:
        raise ValueError("tolerance_m must be non-negative")

    gt_arr = _trajectory(gt)
    sim_arr = _trajectory(sim)
    if len(gt_arr) == 0 or len(sim_arr) == 0:
        return 0.0
    waypoints = _waypoints_by_arclength(gt_arr, k)
    sim_xy = sim_arr[:, 1:3]
    if np.allclose(waypoints, waypoints[0], atol=1e-9, rtol=0.0):
        return float(np.linalg.norm(sim_xy - waypoints[0], axis=1).min() <= tolerance_m)
    hits = 0
    search_start = 0
    for wp in waypoints:
        distances = np.linalg.norm(sim_xy[search_start:] - wp, axis=1)
        candidates = np.flatnonzero(distances <= tolerance_m)
        if candidates.size:
            hits += 1
            search_start += int(candidates[0]) + 1
    return hits / k


@dataclass
class Divergence:
    """首次发散点，喂给 LLM 做错误反思。"""

    ts: float
    gt_xy: tuple[float, float]
    sim_xy: tuple[float, float]
    gt_yaw_deg: float
    sim_yaw_deg: float
    distance_m: float


def first_divergence(
    gt: np.ndarray,
    sim: np.ndarray,
    threshold_m: float = 0.5,
    tolerance_s: float = 0.2,
) -> Divergence | None:
    g, s, _ = align(gt, sim, tolerance_s)
    if len(g) == 0:
        return None
    err = np.linalg.norm(g[:, 1:3] - s[:, 1:3], axis=1)
    bad = np.where(err > threshold_m)[0]
    if len(bad) == 0:
        return None
    i = bad[0]
    return Divergence(
        ts=float(g[i, 0]),
        gt_xy=(float(g[i, 1]), float(g[i, 2])),
        sim_xy=(float(s[i, 1]), float(s[i, 2])),
        gt_yaw_deg=math.degrees(float(g[i, 3])),
        sim_yaw_deg=math.degrees(float(s[i, 3])),
        distance_m=float(err[i]),
    )


@dataclass
class TrajReport:
    ate_m: float
    rpe_m: float
    hit: float
    first_div: Divergence | None
    gt_n: int
    sim_n: int

    def passed(self, ate_thr: float = 1.0, hit_thr: float = 0.8) -> bool:
        return self.ate_m < ate_thr and self.hit >= hit_thr


def evaluate(
    gt_db: str,
    sim_db: str,
    *,
    k_waypoints: int = 10,
    div_threshold_m: float = 0.5,
) -> TrajReport:
    gt = load_traj(gt_db)
    sim = load_traj(sim_db)
    return TrajReport(
        ate_m=ate(gt, sim),
        rpe_m=rpe(gt, sim, delta_s=1.0),
        hit=hit_rate(gt, sim, k=k_waypoints),
        first_div=first_divergence(gt, sim, threshold_m=div_threshold_m),
        gt_n=len(gt),
        sim_n=len(sim),
    )
