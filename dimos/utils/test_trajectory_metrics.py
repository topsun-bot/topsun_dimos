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

"""``trajectory_metrics`` 单元测试.

测试用例覆盖:
1. 完全相同 -> ATE/Fréchet/DTW 均 ~ 0
2. 整体平移 1m -> ATE ~ 1.0, Fréchet ~ 1.0, DTW ~ 1.0
3. 反向同一直线路径 -> Fréchet/DTW 显式校验 (直线场景两者都 != 0)
4. 长度不同 (100 vs 50 点) -> 重采样后能算且 summary() 不崩
5. 输入校验: 错误形状 / 类型 -> 抛 ValueError/TypeError
6. resample_to_length 基础正确性
"""

from __future__ import annotations

import numpy as np
import pytest

from dimos.utils.trajectory_metrics import (
    ate,
    dtw_distance,
    frechet_distance,
    resample_to_length,
    summary,
)

# -----------------------------------------------------------------------------
# 辅助构造
# -----------------------------------------------------------------------------


def _line(n: int = 100, length: float = 10.0) -> np.ndarray:
    """从原点出发沿 +x 走 length 米, n 个点, z=0."""
    xs = np.linspace(0.0, length, n)
    ys = np.zeros(n)
    zs = np.zeros(n)
    return np.stack([xs, ys, zs], axis=1).astype(np.float64)


def _arc(n: int = 100, radius: float = 2.0) -> np.ndarray:
    """半圆 (x, y) = (r cos t, r sin t), t in [0, pi], z=0."""
    theta = np.linspace(0.0, np.pi, n)
    xs = radius * np.cos(theta)
    ys = radius * np.sin(theta)
    zs = np.zeros(n)
    return np.stack([xs, ys, zs], axis=1).astype(np.float64)


# -----------------------------------------------------------------------------
# 1. 完全相同
# -----------------------------------------------------------------------------


def test_identity_line_all_metrics_zero():
    traj = _line(n=50)
    assert ate(traj, traj) == pytest.approx(0.0, abs=1e-12)
    assert frechet_distance(traj, traj) == pytest.approx(0.0, abs=1e-12)
    assert dtw_distance(traj, traj) == pytest.approx(0.0, abs=1e-12)


def test_identity_arc_all_metrics_zero():
    traj = _arc(n=80)
    assert ate(traj, traj) == pytest.approx(0.0, abs=1e-12)
    assert frechet_distance(traj, traj) == pytest.approx(0.0, abs=1e-12)
    assert dtw_distance(traj, traj) == pytest.approx(0.0, abs=1e-12)


# -----------------------------------------------------------------------------
# 2. 平移 1m
# -----------------------------------------------------------------------------


def test_translation_1m_y_axis():
    truth = _line(n=100)
    pred = truth + np.array([0.0, 1.0, 0.0])  # 整体平移 +1m y 轴
    assert ate(truth, pred) == pytest.approx(1.0, abs=1e-9)
    assert frechet_distance(truth, pred) == pytest.approx(1.0, abs=1e-9)
    assert dtw_distance(truth, pred) == pytest.approx(1.0, abs=1e-9)


def test_translation_arbitrary_3d():
    truth = _arc(n=60, radius=3.0)
    shift = np.array([0.3, -0.4, 1.2])  # ||shift|| = sqrt(0.09 + 0.16 + 1.44) = 1.3
    pred = truth + shift
    expected = float(np.linalg.norm(shift))
    assert ate(truth, pred) == pytest.approx(expected, abs=1e-9)
    assert frechet_distance(truth, pred) == pytest.approx(expected, abs=1e-9)


# -----------------------------------------------------------------------------
# 3. 反向同一条路径
# -----------------------------------------------------------------------------


def test_reversed_straight_line_metrics_nonzero():
    """直线反向: 两个路径是同一个点集但顺序颠倒.

    两条轨迹的端点在空间上互换, 时间索引 0 处一个在 [0,0,0], 另一个在
    [10,0,0]. Fréchet/DTW 都必须从 (0, 0) 对齐开始 (且每步只能向前),
    因此初始成对距离 ~ 10m 会传染整条 DP 路径.

    断言: Fréchet 等于端点距离 10, 归一化 DTW 严格小于 Fréchet.
    """
    truth = _line(n=20, length=10.0)
    pred = truth[::-1].copy()  # 反向, 仍然是同一空间路径

    f = frechet_distance(truth, pred)
    d = dtw_distance(truth, pred)

    # Fréchet 一定 == 端点距离 10: DP 中 max(min(...), dist(i,j)) 最大值
    # 出现在两端, dist(0, 0) = ||truth[0] - truth[N-1]|| = 10.
    assert f == pytest.approx(10.0, abs=1e-9)
    # DTW 归一化到 "米/帧" --- 按 warping path 平均, 首步 cost 是 10m,
    # 后续 cost 单调下降到 0 又上升, 平均下来必然是正数且小于 10.
    assert d > 0.0
    assert d < f  # 归一化后 DTW 必然小于 Fréchet 的端点距离


def test_reversed_arc_dtw_smaller_than_frechet():
    """半圆反向: 路径关于 y 轴对称.

    truth[i] 与 pred[i] = truth[N-1-i] 关于 y 轴成镜像. Fréchet 距离 ==
    端点距离 == 2*radius. DTW 归一化值会显著更小. 这给出 DTW <= Fréchet
    的语义对比.
    """
    radius = 2.0
    truth = _arc(n=40, radius=radius)
    pred = truth[::-1].copy()

    f = frechet_distance(truth, pred)
    d = dtw_distance(truth, pred)
    # 两端点距离 = 2*radius = 4m
    assert f == pytest.approx(2 * radius, abs=1e-9)
    # 归一化 DTW 应该明显小于 Fréchet
    assert d < f


# -----------------------------------------------------------------------------
# 4. 长度不同
# -----------------------------------------------------------------------------


def test_different_lengths_ate_resamples_and_close_to_zero():
    """同一条直线、不同采样密度 -> resample 后 ATE 接近 0."""
    truth = _line(n=100, length=5.0)
    pred = _line(n=50, length=5.0)
    # 线性插值的两条直线重采样后应完全重合
    assert ate(truth, pred) == pytest.approx(0.0, abs=1e-9)


def test_different_lengths_summary_does_not_crash():
    truth = _arc(n=100)
    pred = _arc(n=50)  # 同样路径但点更少
    s = summary(truth, pred)

    # key 完整
    assert set(s.keys()) == {
        "ate",
        "frechet",
        "dtw",
        "truth_len",
        "pred_len",
        "path_length_truth",
        "path_length_pred",
    }
    # 元数据正确
    assert s["truth_len"] == 100
    assert s["pred_len"] == 50
    # 同一条半圆路径, 长度应该都接近 pi * r = pi * 2 ~ 6.283
    assert s["path_length_truth"] == pytest.approx(np.pi * 2, rel=0.01)
    assert s["path_length_pred"] == pytest.approx(np.pi * 2, rel=0.05)
    # 同一条路径, 三种距离都应该很小
    assert s["ate"] < 0.05
    assert s["frechet"] < 0.2
    assert s["dtw"] < 0.05


# -----------------------------------------------------------------------------
# 5. 输入校验
# -----------------------------------------------------------------------------


def test_validate_rejects_wrong_shape():
    bad = np.zeros((10, 2), dtype=np.float64)
    good = _line(n=10)
    with pytest.raises(ValueError, match="形状"):
        ate(bad, good)
    with pytest.raises(ValueError, match="形状"):
        frechet_distance(good, bad)
    with pytest.raises(ValueError, match="形状"):
        dtw_distance(bad, good)


def test_validate_rejects_empty():
    empty = np.zeros((0, 3), dtype=np.float64)
    good = _line(n=10)
    with pytest.raises(ValueError, match="不能为空"):
        ate(empty, good)


def test_validate_rejects_non_ndarray():
    good = _line(n=10)
    with pytest.raises(TypeError, match="ndarray"):
        ate([[0, 0, 0], [1, 0, 0]], good)  # type: ignore[arg-type]


def test_resample_rejects_invalid_n():
    traj = _line(n=10)
    with pytest.raises(ValueError, match="n 必须 >= 1"):
        resample_to_length(traj, 0)


# -----------------------------------------------------------------------------
# 6. resample_to_length 基础正确性
# -----------------------------------------------------------------------------


def test_resample_same_length_returns_copy():
    traj = _line(n=10, length=5.0)
    out = resample_to_length(traj, 10)
    assert out.shape == (10, 3)
    np.testing.assert_allclose(out, traj)
    # 是新对象 (不影响原数据)
    out[0, 0] = 999.0
    assert traj[0, 0] != 999.0


def test_resample_upsample_preserves_endpoints():
    traj = _line(n=5, length=4.0)
    out = resample_to_length(traj, 17)
    assert out.shape == (17, 3)
    # 端点严格保留
    np.testing.assert_allclose(out[0], traj[0])
    np.testing.assert_allclose(out[-1], traj[-1])
    # x 应该单调递增到 4
    assert out[-1, 0] == pytest.approx(4.0)


def test_resample_downsample_preserves_endpoints():
    traj = _line(n=100, length=10.0)
    out = resample_to_length(traj, 11)
    assert out.shape == (11, 3)
    np.testing.assert_allclose(out[0], traj[0])
    np.testing.assert_allclose(out[-1], traj[-1])


def test_resample_single_point_broadcasts():
    """N == 1 时输出 n 份同样的点 (明确的退化语义)."""
    traj = np.array([[1.0, 2.0, 3.0]])
    out = resample_to_length(traj, 5)
    assert out.shape == (5, 3)
    for i in range(5):
        np.testing.assert_allclose(out[i], [1.0, 2.0, 3.0])


# -----------------------------------------------------------------------------
# 7. 边界一致性: N == 1 vs M == 1
# -----------------------------------------------------------------------------


def test_single_point_vs_line():
    """单点 vs 多点: 不应崩.

    ATE 的对齐策略是 ``min(len(truth), len(pred))``, 因此 truth 长度为 1
    时整段对比退化到 "两条都只剩首点", 距离就是 ``||truth[0] - pred[0]||``.
    summary 应能正常返回.
    """
    truth = np.array([[0.0, 0.0, 0.0]])
    pred = _line(n=10, length=3.0)
    # truth[0] = [0,0,0], pred[0] = [0,0,0] -> ATE 退化到 0
    assert ate(truth, pred) == pytest.approx(0.0, abs=1e-12)

    # summary 不崩 + 元数据正确
    s = summary(truth, pred)
    assert s["truth_len"] == 1
    assert s["pred_len"] == 10
    assert s["path_length_truth"] == 0.0
    assert s["path_length_pred"] == pytest.approx(3.0, abs=1e-9)
