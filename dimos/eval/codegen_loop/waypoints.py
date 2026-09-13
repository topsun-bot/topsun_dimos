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

"""从 GT 轨迹自动抽 waypoint 序列。

两种方法：
- uniform_by_arclength: 评测 hit_rate 用，按累计弧长每 d_m 取一点。
- rdp_simplify: 给 LLM prompt 注入用，保留拐弯点，丢直线段。
"""

from __future__ import annotations

from typing import cast

import numpy as np
from numpy.typing import NDArray

Trajectory = NDArray[np.float64]


def _trajectory(value: np.ndarray) -> Trajectory:
    arr = np.asarray(value, dtype=np.float64)
    if arr.size == 0:
        return np.empty((0, 4), dtype=np.float64)
    if arr.ndim != 2 or arr.shape[1] != 4:
        raise ValueError(f"trajectory must have shape (N, 4), got {arr.shape}")
    return arr


def cumulative_arclength(xy: np.ndarray) -> NDArray[np.float64]:
    points = np.asarray(xy, dtype=np.float64)
    if points.size == 0:
        return np.empty(0, dtype=np.float64)
    if points.ndim != 2 or points.shape[1] != 2:
        raise ValueError(f"xy must have shape (N, 2), got {points.shape}")
    diffs = np.diff(points, axis=0)
    seg = np.sqrt((diffs**2).sum(axis=1))
    return np.concatenate((np.zeros(1, dtype=np.float64), np.cumsum(seg)))


def uniform_by_arclength(traj: np.ndarray, d_m: float = 3.0) -> Trajectory:
    """每 d_m 米沿轨迹取一点。返回 (K, 4)."""
    if d_m <= 0:
        raise ValueError("d_m must be positive")
    points = _trajectory(traj)
    if len(points) <= 1:
        return points.copy()

    xy = points[:, 1:3]
    arc = cumulative_arclength(xy)
    total = arc[-1]
    if total < d_m:
        return points[[0, -1]]
    targets = np.arange(0, total + 1e-6, d_m)
    if total - targets[-1] > 1e-6:
        targets = np.append(targets, total)
    unique_arc, unique_idx = np.unique(arc, return_index=True)
    unwrapped_yaw = np.unwrap(points[:, 3])
    sampled = np.column_stack(
        (
            np.interp(targets, unique_arc, points[unique_idx, 0]),
            np.interp(targets, unique_arc, points[unique_idx, 1]),
            np.interp(targets, unique_arc, points[unique_idx, 2]),
            np.interp(targets, unique_arc, unwrapped_yaw[unique_idx]),
        )
    )
    sampled[:, 3] = (sampled[:, 3] + np.pi) % (2 * np.pi) - np.pi
    sampled[0] = points[0]
    sampled[-1] = points[-1]
    return cast("Trajectory", sampled)


def rdp_simplify(traj: np.ndarray, epsilon_m: float = 0.5) -> Trajectory:
    """Ramer-Douglas-Peucker：保留偏离直线 > epsilon 的点。"""
    if epsilon_m < 0:
        raise ValueError("epsilon_m must be non-negative")
    points = _trajectory(traj)
    pts = points[:, 1:3]
    n = len(pts)
    if n < 3:
        return points.copy()
    keep = np.zeros(n, dtype=bool)
    keep[0] = keep[-1] = True
    stack = [(0, n - 1)]
    while stack:
        a, b = stack.pop()
        if b - a < 2:
            continue
        p0, p1 = pts[a], pts[b]
        seg = p1 - p0
        L = np.linalg.norm(seg)
        if L < 1e-9:
            d = np.linalg.norm(pts[a + 1 : b] - p0, axis=1)
        else:
            n_hat = np.array([-seg[1], seg[0]]) / L
            d = np.abs((pts[a + 1 : b] - p0) @ n_hat)
        if d.size == 0:
            continue
        k = int(np.argmax(d))
        if d[k] > epsilon_m:
            mid = a + 1 + k
            keep[mid] = True
            stack.append((a, mid))
            stack.append((mid, b))
    return cast("Trajectory", points[keep])
