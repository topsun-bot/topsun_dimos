#!/usr/bin/env python3
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

# https://github.com/dimensionalOS/dimos/blob/2c069c8ac3dbc677fbba31fddd2f68291f21a50a/dimos/mapping/relocalization/relocalize.py
# auto research from ivan sloptimization/ransac

from __future__ import annotations

from typing import Any

import numpy as np
import open3d as o3d  # type: ignore[import-untyped]

_reg = o3d.pipelines.registration

# 多尺度全局搜索计划：(voxel_size, 该尺度 RANSAC 重启次数)。
# (voxel_size, total RANSAC runs at that scale). 0.8m is the coarsest, cheapest
# scale; it provides anchor candidates that don't need as many restarts.
SCALE_PLAN: list[tuple[float, int]] = [
    (0.2, 8),
    (0.3, 8),
    (0.8, 1),
]
# 每次 RANSAC 的最大迭代数；这是全局重定位最重的搜索预算之一。
RANSAC_ITERS = 500_000  # RANSAC iteration budget per scale
# final ICP 使用的精细体素尺寸；越小越精细，也越慢。
FINE_VOXEL = 0.1  # voxel for the final ICP refinement
# 细尺度候选重排和 ICP 的最大对应点距离。
RERANK_DIST = FINE_VOXEL * 1.5  # inlier dist for fine-scale candidate scoring
# 候选姿态的重力方向约束，避免把机器人匹配成明显倾斜的姿态。
GRAVITY_TILT_MAX_DEG = 10.0  # reject candidates whose z-axis tilts more than this


def _preprocess(
    pcd: o3d.geometry.PointCloud, voxel_size: float
) -> tuple[o3d.geometry.PointCloud, Any]:
    """Downsample, estimate normals, compute FPFH descriptors."""
    # 先按 voxel_size 下采样，降低点数，让 FPFH/RANSAC 可承受。
    down = pcd.voxel_down_sample(voxel_size)
    # FPFH 依赖法向；半径取 2 个 voxel，局部几何比较稳定。
    down.estimate_normals(o3d.geometry.KDTreeSearchParamHybrid(radius=voxel_size * 2, max_nn=30))
    # 计算 FPFH 特征，用于后续跨点云的特征匹配 RANSAC。
    fpfh = _reg.compute_fpfh_feature(
        down,
        o3d.geometry.KDTreeSearchParamHybrid(radius=voxel_size * 5, max_nn=100),
    )
    return down, fpfh


# Per-process cache of the global map's downsampled cloud + FPFH features and
# the fine-voxel cloud used for ICP. The evaluator forks workers and reuses
# the same global map across all 20 frames per worker, so the first call in
# each worker pays the cost; the remaining 4-5 frames it handles get it free.
# Allowed per program.md: "caching the global map's FPFH features across calls
# is fine *within one run*; the evaluator instantiates fresh state per process."
_GLOBAL_CACHE: dict[tuple[str, float, int], Any] = {}


def _global_preprocess(
    global_map: o3d.geometry.PointCloud, voxel_size: float
) -> tuple[o3d.geometry.PointCloud, Any]:
    # cache key 使用用途、尺度和点数；同一进程同一 premap 可复用特征。
    key = ("ransac", voxel_size, len(global_map.points))
    cached = _GLOBAL_CACHE.get(key)
    # 第一次遇到该尺度时才真正做 downsample/normals/FPFH。
    if cached is None:
        # 对全局 premap 运行预处理；这是首次 relocalize 的主要成本之一。
        cached = _preprocess(global_map, voxel_size)
        _GLOBAL_CACHE[key] = cached
    return cached  # type: ignore[no-any-return]


def _global_fine(global_map: o3d.geometry.PointCloud, voxel_size: float) -> o3d.geometry.PointCloud:
    # fine cache 专供候选评分和 final ICP 使用，避免每次重算 premap 细尺度点云。
    key = ("fine", voxel_size, len(global_map.points))
    cached = _GLOBAL_CACHE.get(key)
    # 细尺度 premap 第一次使用时才下采样和估计法向。
    if cached is None:
        # 细尺度下采样保留更多几何细节，给 ICP 精配准使用。
        down = global_map.voxel_down_sample(voxel_size)
        # point-to-plane ICP 和 wall-only 过滤都需要法向。
        down.estimate_normals(
            o3d.geometry.KDTreeSearchParamHybrid(radius=voxel_size * 2, max_nn=30)
        )
        cached = down
        _GLOBAL_CACHE[key] = cached
    return cached  # type: ignore[no-any-return]


def _ransac(
    src_down: o3d.geometry.PointCloud,
    tgt_down: o3d.geometry.PointCloud,
    src_fpfh: Any,
    tgt_fpfh: Any,
    voxel_size: float,
) -> Any:
    """Open3D feature-matching RANSAC. Returns a RegistrationResult.

    Docs:
      https://www.open3d.org/docs/latest/python_api/open3d.registration.registration_ransac_based_on_feature_matching.html
    """
    # RANSAC 的对应点距离阈值随尺度放大，粗尺度允许更大的几何误差。
    dist = voxel_size * 1.5
    # Open3D 全局注册：用 FPFH 特征配对，随机采样 3 对点估计粗变换。
    return _reg.registration_ransac_based_on_feature_matching(
        src_down,
        tgt_down,
        src_fpfh,
        tgt_fpfh,
        # mutual_filter 要求匹配是双向最近邻，减少错误特征匹配。
        mutual_filter=True,
        max_correspondence_distance=dist,
        # 粗配准阶段用 point-to-point，不用法向做精约束。
        estimation_method=_reg.TransformationEstimationPointToPoint(False),
        # 每次随机取 3 对对应点求一个候选刚体变换。
        ransac_n=3,
        checkers=[
            # 边长检查保证局部几何形状不要被候选变换拉伸/压缩得太离谱。
            _reg.CorrespondenceCheckerBasedOnEdgeLength(0.9),
            # 距离检查保证变换后的对应点不要超过尺度相关阈值。
            _reg.CorrespondenceCheckerBasedOnDistance(dist),
        ],
        # 高迭代预算提高全局搜索成功率，但也是耗时来源。
        criteria=_reg.RANSACConvergenceCriteria(RANSAC_ITERS, 0.995),
    )


def _gravity_tilt_deg(T: np.ndarray) -> float:
    """Angle (deg) between the transform's z-axis and world z-up."""
    # 将局部 z 轴通过候选旋转投到世界，检查它和世界 z-up 的夹角。
    z_world = T[:3, :3] @ np.array([0.0, 0.0, 1.0])
    # 数值上先 clip 到 [-1, 1]，避免浮点误差让 arccos 出 NaN。
    return float(np.degrees(np.arccos(np.clip(z_world[2], -1.0, 1.0))))


def relocalize(
    global_map: o3d.geometry.PointCloud,
    local_map: o3d.geometry.PointCloud,
) -> tuple[np.ndarray, float]:
    """Estimate the 4x4 transform placing ``local_map`` into ``global_map``.

    Multi-scale x multi-restart FPFH+RANSAC -> gravity-filtered, re-ranked by
    fine-scale inlier ratio (not RANSAC's own fitness) -> fine ICP. The
    rerank catches z-degenerate and wrong-room busts: at FINE_VOXEL a
    5m-off candidate has ~0 inliers while RANSAC reports it as fit.
    """
    # Step 0：把当前 local_map 做一次细尺度下采样，供候选评分和 final ICP 复用。
    # Fine downsample once — used for both candidate scoring and the final ICP.
    src_fine = local_map.voxel_down_sample(FINE_VOXEL)
    # 为 point-to-plane ICP 和 wall-only 分割估计 local 法向。
    src_fine.estimate_normals(
        o3d.geometry.KDTreeSearchParamHybrid(radius=FINE_VOXEL * 2, max_nn=30)
    )
    # 获取 premap 的细尺度缓存；第一次调用会计算，后续同进程复用。
    tgt_fine = _global_fine(global_map, FINE_VOXEL)

    candidates: list[np.ndarray] = []  # 4x4 transforms
    # Step 1：按多个尺度生成全局候选；细尺度更准，粗尺度更容易找到大致位置。
    for vs, n_runs in SCALE_PLAN:
        # 对 local_map 做当前尺度的 downsample/normals/FPFH。
        src_down, src_fpfh = _preprocess(local_map, vs)
        # 对 global_map 做当前尺度预处理；global 侧有缓存，避免重复算 premap 特征。
        tgt_down, tgt_fpfh = _global_preprocess(global_map, vs)
        # 同一尺度跑多次 RANSAC，利用随机性探索不同候选。
        for _ in range(n_runs):
            # Successive calls advance Open3D's RNG state (seeded per-frame in
            # run.py), so each restart explores a different sample sequence.
            # 调用特征匹配 RANSAC，得到一个 map<-world 的粗配准候选。
            result = _ransac(src_down, tgt_down, src_fpfh, tgt_fpfh, vs)
            # 只保留 4x4 变换矩阵；后续统一按矩阵做过滤、翻转和 ICP。
            candidates.append(np.asarray(result.transformation))

    # Step 2：为每个候选补一个 180 度 yaw flip 版本，处理室内朝向歧义。
    # Centroid-aware yaw flip: for every candidate, add the variant where the
    # body cloud is rotated 180° around its OWN xy-centroid (not body origin).
    # A naive `T @ Rz_180` rotates around body origin, which moves the entire
    # cloud across the world when lidar coverage isn't centered on the robot.
    # Rotating around the cloud centroid keeps the flipped cloud in the same
    # approximate world location — the right reading of "same place, opposite
    # heading" for an indoor submap.
    # 取 local 细尺度点云，计算其 xy 质心作为翻转中心。
    src_pts = np.asarray(src_fine.points)
    c_body = np.array([src_pts[:, 0].mean(), src_pts[:, 1].mean(), 0.0])
    # 绕 z 轴转 180 度，相当于 x/y 同时取反。
    rz180 = np.diag([-1.0, -1.0, 1.0])
    t_body_flip = np.eye(4)
    t_body_flip[:3, :3] = rz180
    # 平移项保证旋转围绕 local 点云自身质心，而不是围绕原点。
    t_body_flip[:3, 3] = c_body - rz180 @ c_body  # = (2*Cx, 2*Cy, 0)
    # 候选数量翻倍：原候选 + 质心 yaw flip 候选。
    candidates = candidates + [T @ t_body_flip for T in candidates]

    # Step 3：重力方向过滤。室内 Go2 不应出现大角度翻滚/俯仰的重定位结果。
    # Gravity filter; fall back to all if everything is tilted (degenerate clouds).
    upright = [T for T in candidates if _gravity_tilt_deg(T) <= GRAVITY_TILT_MAX_DEG]
    # 如果所有候选都被判倾斜，说明点云可能退化；保守退回全集，避免无候选崩溃。
    pool = upright if upright else candidates

    # Step 4：构造只含墙面的点云，用墙面来排序和精修候选。
    # Build WALL-ONLY clouds for scoring + polish. Floor/ceiling points have
    # vertical normals; they fit equally well in any yaw rotation (flat planes
    # are rotationally symmetric). Including them in scoring lets a 180°-flipped
    # candidate hide its wall misalignment behind perfect floor alignment. The
    # FULL clouds are still used for the final refinement, so the gravity
    # anchor and inlier density are preserved in the output.
    def _wall_subset(cloud: o3d.geometry.PointCloud) -> o3d.geometry.PointCloud:
        # 读取法向，法向 z 分量小的点更像竖直墙面。
        nrm = np.asarray(cloud.normals)
        mask = np.abs(nrm[:, 2]) < 0.7  # roughly horizontal
        # 墙面点太少时 wall-only 不可靠，退回完整点云。
        if mask.sum() < 100:
            return cloud  # too sparse -> fall back to full cloud
        # 创建一个新点云，只保留墙面点和对应法向。
        sub = o3d.geometry.PointCloud()
        sub.points = o3d.utility.Vector3dVector(np.asarray(cloud.points)[mask])
        sub.normals = o3d.utility.Vector3dVector(nrm[mask])
        return sub

    # 对 local 和 premap 都取 wall-only 子集，后续评分/ICP 用这两个子集。
    src_walls = _wall_subset(src_fine)
    tgt_walls = _wall_subset(tgt_fine)

    # Step 5：用 wall-only fine-scale fitness 给所有候选重排。
    # Stage 1: rank all candidates by WALL-only fine-scale fitness.
    def fine_fitness(T: np.ndarray) -> float:
        # evaluate_registration 不优化，只统计 T 下的 inlier 比例。
        r = _reg.evaluate_registration(src_walls, tgt_walls, RERANK_DIST, T)
        # fitness 越高，说明候选让 local 墙面和 premap 墙面重合越多。
        return float(r.fitness)

    # 只保留前 10 个候选进入 ICP，控制后续精修成本。
    top_k = sorted(pool, key=fine_fitness, reverse=True)[:10]

    # Step 6：对 top-10 候选跑 wall-only point-to-plane ICP，进一步修正 yaw/xy。
    # Stage 2: run a moderate-distance ICP on each top-10 on WALL clouds.
    # Wall correspondences drive yaw and xy; the rerank then picks the
    # candidate whose walls actually align (not the one whose floors agree).
    # TukeyLoss 降低离群对应点权重，减少动态物体/错误墙面点对 ICP 的影响。
    tukey = _reg.TransformationEstimationPointToPlane(_reg.TukeyLoss(k=RERANK_DIST))
    polished: list[tuple[float, np.ndarray]] = []
    for T0 in top_k:
        # 以 RANSAC 候选 T0 为初值，只在墙面子集上做中等迭代数 ICP。
        r = _reg.registration_icp(
            src_walls,
            tgt_walls,
            RERANK_DIST,
            T0,
            tukey,
            _reg.ICPConvergenceCriteria(max_iteration=70),
        )
        # 保存 ICP 后的 fitness 和变换，稍后选出墙面对齐最好的候选。
        polished.append((float(r.fitness), np.asarray(r.transformation)))
    # 选择 wall-only ICP 后 fitness 最高的候选作为 final ICP 初值。
    best_fit, best_T = max(polished, key=lambda fT: fT[0])

    # Step 7：在完整点云上做最后一次 ICP，恢复地面/天花板等非墙面约束。
    # Stage 3: final ICP on full clouds, incl. floor/ceiling
    final = _reg.registration_icp(
        src_fine,
        tgt_fine,
        RERANK_DIST,
        best_T,
        tukey,
        _reg.ICPConvergenceCriteria(max_iteration=50),
    )
    # 返回 final 变换矩阵；fitness 仍用 wall-only best_fit，避免地面掩盖墙面错配。
    return np.asarray(final.transformation), best_fit
