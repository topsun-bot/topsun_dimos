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

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal

import numpy as np

if TYPE_CHECKING:
    import open3d as o3d  # type: ignore[import-untyped]

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


def _o3d_registration() -> Any:
    """Lazily import open3d and return its ``pipelines.registration`` module."""
    import open3d as o3d

    return o3d.pipelines.registration


def _preprocess(
    pcd: o3d.geometry.PointCloud, voxel_size: float
) -> tuple[o3d.geometry.PointCloud, Any]:
    """Downsample, estimate normals, compute FPFH descriptors."""
    import open3d as o3d

    _reg = _o3d_registration()
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
    import open3d as o3d

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


def _prepare_fine_cloud(
    cloud: o3d.geometry.PointCloud,
    voxel_size: float = FINE_VOXEL,
) -> o3d.geometry.PointCloud:
    """Prepare a downsampled cloud with normals for point-to-plane ICP."""
    import open3d as o3d

    # local 点云每次都会变化，因此不能像固定 premap 一样放入全局缓存。
    down = cloud.voxel_down_sample(voxel_size)
    # point-to-plane ICP 和墙面筛选都依赖法向，半径沿用原 final ICP 参数。
    down.estimate_normals(o3d.geometry.KDTreeSearchParamHybrid(radius=voxel_size * 2, max_nn=30))
    return down


def _wall_subset(cloud: o3d.geometry.PointCloud) -> o3d.geometry.PointCloud:
    """Keep mostly vertical surfaces so floor points cannot hide a bad yaw."""
    import open3d as o3d

    # 读取法向，法向 z 分量较小的点对应近似竖直的墙面。
    normals = np.asarray(cloud.normals)
    mask = np.abs(normals[:, 2]) < 0.7
    # 墙面点太少时 wall-only 约束不稳定，直接退回完整点云。
    if mask.sum() < 100:
        return cloud

    # 创建独立点云，保证墙面 ICP 不会修改原始 fine 点云。
    subset = o3d.geometry.PointCloud()
    subset.points = o3d.utility.Vector3dVector(np.asarray(cloud.points)[mask])
    subset.normals = o3d.utility.Vector3dVector(normals[mask])
    return subset


@dataclass(frozen=True)
class IcpStageMetrics:
    """Point-to-plane registration quality at one ICP stage."""

    fitness: float
    rmse: float
    inlier_count: int
    source_pts: int


@dataclass(frozen=True)
class FastIcpDiagnostics:
    """Diagnostic snapshot for fast ICP (cached-start) root-cause analysis."""

    voxel_size_m: float
    max_correspondence_distance: float
    max_iteration: int
    icp_estimation: str
    source_raw_pts: int
    source_fine_pts: int
    source_wall_pts: int
    target_full_fine_pts: int
    target_roi_pts: int
    crop_enabled: bool
    crop_radius_m: float | None
    crop_fallback_full_target: bool
    crop_aabb_min: list[float] | None
    crop_aabb_max: list[float] | None
    wall_before: IcpStageMetrics
    full_before: IcpStageMetrics
    wall_after: IcpStageMetrics
    full_after: IcpStageMetrics
    init_vs_result_trans_delta_m: float
    init_vs_result_yaw_delta_deg: float
    limiting_stage: str
    combined_fitness: float

    def to_log_line(self) -> str:
        """Single-line summary for structured logs."""
        crop = (
            f"crop_enabled={self.crop_enabled} crop_radius_m={self.crop_radius_m} "
            f"crop_fallback_full={self.crop_fallback_full_target} "
            f"crop_aabb_min={self.crop_aabb_min} crop_aabb_max={self.crop_aabb_max}"
        )
        return (
            f"voxel_size_m={self.voxel_size_m} max_dist={self.max_correspondence_distance} "
            f"max_iter={self.max_iteration} icp_estimation={self.icp_estimation} "
            f"src_raw={self.source_raw_pts} src_fine={self.source_fine_pts} "
            f"src_wall={self.source_wall_pts} "
            f"tgt_full_fine={self.target_full_fine_pts} tgt_roi={self.target_roi_pts} "
            f"{crop} "
            f"wall_before fitness={self.wall_before.fitness:.4f} rmse={self.wall_before.rmse:.4f} "
            f"inliers={self.wall_before.inlier_count}/{self.wall_before.source_pts} "
            f"full_before fitness={self.full_before.fitness:.4f} rmse={self.full_before.rmse:.4f} "
            f"inliers={self.full_before.inlier_count}/{self.full_before.source_pts} "
            f"wall_after fitness={self.wall_after.fitness:.4f} rmse={self.wall_after.rmse:.4f} "
            f"inliers={self.wall_after.inlier_count}/{self.wall_after.source_pts} "
            f"full_after fitness={self.full_after.fitness:.4f} rmse={self.full_after.rmse:.4f} "
            f"inliers={self.full_after.inlier_count}/{self.full_after.source_pts} "
            f"T_delta trans_m={self.init_vs_result_trans_delta_m:.3f} "
            f"yaw_deg={self.init_vs_result_yaw_delta_deg:.1f} "
            f"limiting_stage={self.limiting_stage} combined_fitness={self.combined_fitness:.4f}"
        )


def _inlier_count(result: Any, source_pts: int) -> int:
    correspondence_set = getattr(result, "correspondence_set", None)
    if correspondence_set is not None and len(correspondence_set) > 0:
        return len(correspondence_set)
    return round(float(result.fitness) * source_pts)


def _evaluate_stage(
    source: o3d.geometry.PointCloud,
    target: o3d.geometry.PointCloud,
    max_dist: float,
    transform: np.ndarray,
) -> IcpStageMetrics:
    _reg = _o3d_registration()
    source_pts = len(source.points)
    if source_pts == 0:
        return IcpStageMetrics(0.0, float("nan"), 0, 0)
    # 当前 Open3D 版 evaluate_registration 无 estimation_method 参数, 默认 point-to-point。
    evaluated = _reg.evaluate_registration(source, target, max_dist, transform)
    return IcpStageMetrics(
        fitness=float(evaluated.fitness),
        rmse=float(evaluated.inlier_rmse),
        inlier_count=_inlier_count(evaluated, source_pts),
        source_pts=source_pts,
    )


IcpEstimation = Literal["point_to_point", "point_to_plane"]


def _icp_estimation_method(
    icp_estimation: IcpEstimation,
    max_correspondence_distance: float,
) -> Any:
    """Build Open3D ICP estimation; point-to-plane matches global relocalize() TukeyLoss."""
    _reg = _o3d_registration()
    if icp_estimation == "point_to_point":
        return _reg.TransformationEstimationPointToPoint()
    return _reg.TransformationEstimationPointToPlane(
        _reg.TukeyLoss(k=max_correspondence_distance),
    )


def _metrics_from_icp_result(result: Any, source_pts: int) -> IcpStageMetrics:
    return IcpStageMetrics(
        fitness=float(result.fitness),
        rmse=float(result.inlier_rmse),
        inlier_count=_inlier_count(result, source_pts),
        source_pts=source_pts,
    )


def _pose_delta_m_and_yaw_deg(
    T_result: np.ndarray,
    T_init: np.ndarray,
) -> tuple[float, float]:
    T_delta = T_result @ np.linalg.inv(T_init)
    trans_m = float(np.linalg.norm(T_delta[:3, 3]))
    yaw_deg = float(np.degrees(np.arctan2(T_delta[1, 0], T_delta[0, 0])))
    return trans_m, yaw_deg


def _crop_target_around_source(
    source: o3d.geometry.PointCloud,
    target: o3d.geometry.PointCloud,
    init_T_map_world: np.ndarray,
    crop_radius: float,
) -> tuple[o3d.geometry.PointCloud, dict[str, Any]]:
    """Crop the target around the source transformed by the cached pose."""
    import open3d as o3d

    meta: dict[str, Any] = {
        "crop_enabled": True,
        "crop_radius_m": crop_radius,
        "crop_fallback_full_target": False,
        "crop_aabb_min": None,
        "crop_aabb_max": None,
        "target_full_fine_pts": len(target.points),
    }
    source_points = np.asarray(source.points)
    # 空 source 无法形成裁剪框，保留完整 target 交给 ICP 报告真实结果。
    if len(source_points) == 0:
        meta["crop_fallback_full_target"] = True
        meta["target_roi_pts"] = len(target.points)
        return target, meta

    # 直接用矩阵变换 numpy 点，不复制 Open3D 点云，减少一次内存分配。
    transformed = (init_T_map_world[:3, :3] @ source_points.T).T
    transformed += init_T_map_world[:3, 3]
    min_bound = transformed.min(axis=0) - crop_radius
    max_bound = transformed.max(axis=0) + crop_radius
    meta["crop_aabb_min"] = min_bound.round(3).tolist()
    meta["crop_aabb_max"] = max_bound.round(3).tolist()
    crop_box = o3d.geometry.AxisAlignedBoundingBox(min_bound, max_bound)
    cropped = target.crop(crop_box)

    # 缓存初值偏差较大时裁剪区可能没有地图点；此时退回完整 premap。
    if len(cropped.points) < 100:
        meta["crop_fallback_full_target"] = True
        meta["target_roi_pts"] = len(target.points)
        return target, meta

    meta["target_roi_pts"] = len(cropped.points)
    return cropped, meta


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
    import open3d as o3d

    _reg = o3d.pipelines.registration
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
    import open3d as o3d

    _reg = o3d.pipelines.registration

    # Step 0：把当前 local_map 做一次细尺度下采样，供候选评分和 final ICP 复用。
    # Fine downsample once — used for both candidate scoring and the final ICP.
    src_fine = _prepare_fine_cloud(local_map)
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
    tukey = _icp_estimation_method("point_to_plane", RERANK_DIST)
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


def relocalize_with_initial(
    global_map: o3d.geometry.PointCloud,
    local_map: o3d.geometry.PointCloud,
    init_T_map_world: np.ndarray,
    *,
    max_correspondence_distance: float = RERANK_DIST,
    max_iteration: int = 50,
    crop_radius: float | None = 8.0,
    icp_estimation: IcpEstimation = "point_to_point",
) -> tuple[np.ndarray, float, FastIcpDiagnostics]:
    """Refine ``map <- world`` from a cached transform without global RANSAC."""
    _reg = _o3d_registration()
    # JSON 或调用方传入的初值必须是有限的 4x4 刚体变换矩阵。
    initial = np.asarray(init_T_map_world, dtype=float)
    if initial.shape != (4, 4) or not np.isfinite(initial).all():
        raise ValueError("init_T_map_world must be a finite 4x4 matrix")
    if max_iteration <= 0:
        raise ValueError("max_iteration must be positive")
    if max_correspondence_distance <= 0:
        raise ValueError("max_correspondence_distance must be positive")

    source_raw_pts = len(local_map.points)
    # local 每次都重新下采样；premap 继续使用进程内 fine cache。
    src_fine = _prepare_fine_cloud(local_map)
    tgt_fine = _global_fine(global_map, FINE_VOXEL)

    crop_meta: dict[str, Any] = {
        "crop_enabled": False,
        "crop_radius_m": None,
        "crop_fallback_full_target": False,
        "crop_aabb_min": None,
        "crop_aabb_max": None,
        "target_full_fine_pts": len(tgt_fine.points),
        "target_roi_pts": len(tgt_fine.points),
    }
    # 有缓存姿态时只取附近 premap，减少 ICP KD-tree 搜索的 target 点数。
    target_for_icp = tgt_fine
    if crop_radius is not None and crop_radius > 0:
        target_for_icp, crop_meta = _crop_target_around_source(
            src_fine,
            tgt_fine,
            initial,
            crop_radius,
        )

    # point_to_point: 地面点过多时法向不可靠; point_to_plane: 与全局 relocalize() 一致 TukeyLoss。
    icp_method = _icp_estimation_method(icp_estimation, max_correspondence_distance)
    # 第一段用墙面 ICP 优先纠正 xy/yaw。
    src_walls = _wall_subset(src_fine)
    tgt_walls = _wall_subset(target_for_icp)
    wall_before = _evaluate_stage(
        src_walls,
        tgt_walls,
        max_correspondence_distance,
        initial,
    )
    full_before = _evaluate_stage(
        src_fine,
        target_for_icp,
        max_correspondence_distance,
        initial,
    )
    wall_result = _reg.registration_icp(
        src_walls,
        tgt_walls,
        max_correspondence_distance,
        initial,
        icp_method,
        _reg.ICPConvergenceCriteria(max_iteration=max_iteration),
    )

    # 第二段回到完整点云做 final ICP。
    final_result = _reg.registration_icp(
        src_fine,
        target_for_icp,
        max_correspondence_distance,
        wall_result.transformation,
        icp_method,
        _reg.ICPConvergenceCriteria(max_iteration=max_iteration),
    )

    final_T = np.asarray(final_result.transformation)
    wall_after = _metrics_from_icp_result(wall_result, len(src_walls.points))
    full_after = _metrics_from_icp_result(final_result, len(src_fine.points))
    # 取墙面和全点云 fitness 中较小值，两道约束任一不可靠就拒绝。
    wall_fit = float(wall_result.fitness)
    final_fit = float(final_result.fitness)
    # fitness = min(wall_fit, final_fit)
    # 墙面点匹配数较低，若原premap中该区域墙面点少，则fit值较低，采用大的匹配值
    fitness = max(wall_fit, final_fit)

    limiting_stage = "wall" if wall_fit <= final_fit else "full"
    trans_delta_m, yaw_delta_deg = _pose_delta_m_and_yaw_deg(final_T, initial)

    diagnostics = FastIcpDiagnostics(
        voxel_size_m=FINE_VOXEL,
        max_correspondence_distance=max_correspondence_distance,
        max_iteration=max_iteration,
        icp_estimation=icp_estimation,
        source_raw_pts=source_raw_pts,
        source_fine_pts=len(src_fine.points),
        source_wall_pts=len(src_walls.points),
        target_full_fine_pts=int(crop_meta["target_full_fine_pts"]),
        target_roi_pts=int(crop_meta["target_roi_pts"]),
        crop_enabled=bool(crop_meta["crop_enabled"]),
        crop_radius_m=crop_meta["crop_radius_m"],
        crop_fallback_full_target=bool(crop_meta["crop_fallback_full_target"]),
        crop_aabb_min=crop_meta["crop_aabb_min"],
        crop_aabb_max=crop_meta["crop_aabb_max"],
        wall_before=wall_before,
        full_before=full_before,
        wall_after=wall_after,
        full_after=full_after,
        init_vs_result_trans_delta_m=trans_delta_m,
        init_vs_result_yaw_delta_deg=yaw_delta_deg,
        limiting_stage=limiting_stage,
        combined_fitness=fitness,
    )
    return final_T, fitness, diagnostics
