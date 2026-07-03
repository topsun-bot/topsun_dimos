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

"""
逐步调试 relocalize() — 用录制数据构造 local_map + premap, 打印每一步变量。

纯离线脚本, 不连接真机, 不依赖 WebRTC/LCM 网络/torch。
只依赖: numpy + open3d + relocalize.py + PointCloud2.lcm_decode + SqliteStore(读录制 db)。

用法:
  cd ~/huazhijian/topsun-bot/topsun_dimos
  .venv/bin/python jiangtao/scripts/debug_relocalize.py

数据来源:
  - premap:   jiangtao/data/20260630/recording_go2.pc2.lcm  (PGO 离线地图, 479k 点)
  - local:    jiangtao/data/20260630/recording_go2.db       (录制 lidar 流, 取后半段累积)
"""

from __future__ import annotations

from pathlib import Path
import sys
import time

import numpy as np
import open3d as o3d

# relocalize.py 本身只依赖 numpy + open3d, 无网络/真机依赖
from dimos.mapping.relocalization import relocalize as _reloc_mod

# SqliteStore 只读本地 .db 文件, 不连网络
from dimos.memory2.store.sqlite import SqliteStore

# PointCloud2.lcm_decode 只做二进制反序列化, 不连 LCM 网络
from dimos.msgs.sensor_msgs.PointCloud2 import PointCloud2

# ── 配置 ────────────────────────────────────────────────
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
PREMAP_PATH = PROJECT_ROOT / "jiangtao/data/20260630/recording_go2.pc2.lcm"
DB_PATH = PROJECT_ROOT / "jiangtao/data/20260630/recording_go2.db"
MIN_LOCAL_POINTS = 50_000  # 和 module.py 一致
N_LIDAR_FRAMES = 200  # 累积多少帧 lidar 构造 local_map (后半段, 点数够用)
SEEK_FRAMES = 1500  # 跳过前 1500 帧, 取机器人走动后的数据
ACCUM_VOXEL = 0.05  # 累积 local_map 时的体素大小 (和 VoxelGridMapper 一致)


# ── 打印工具 ────────────────────────────────────────────
def banner(title: str) -> None:
    print(f"\n{'=' * 60}")
    print(f"  {title}")
    print(f"{'=' * 60}")


def step(num: int, title: str) -> None:
    print(f"\n--- Step {num}: {title} ---")


def pcd_info(name: str, pcd: o3d.geometry.PointCloud) -> None:
    pts = np.asarray(pcd.points)
    if len(pts) == 0:
        print(f"  {name}: 0 points (空)")
        return
    print(f"  {name}: {len(pts):,} points")
    print(f"    X: [{pts[:, 0].min():.2f}, {pts[:, 0].max():.2f}]")
    print(f"    Y: [{pts[:, 1].min():.2f}, {pts[:, 1].max():.2f}]")
    print(f"    Z: [{pts[:, 2].min():.2f}, {pts[:, 2].max():.2f}]")


def mat_info(name: str, T: np.ndarray) -> None:
    print(f"  {name} (4x4):")
    for row in T:
        print(f"    [{row[0]:+.4f} {row[1]:+.4f} {row[2]:+.4f} {row[3]:+.4f}]")
    # 提取 yaw (绕 z 轴旋转角度)
    yaw = np.degrees(np.arctan2(T[1, 0], T[0, 0]))
    print(f"    yaw={yaw:.1f}°  translation=({T[0, 3]:.2f}, {T[1, 3]:.2f}, {T[2, 3]:.2f})")


# ── 1. 加载 premap ──────────────────────────────────────
def load_premap() -> o3d.geometry.PointCloud:
    banner("加载 premap (离线 PGO 地图)")
    print(f"  文件: {PREMAP_PATH}")
    print(f"  大小: {PREMAP_PATH.stat().st_size / 1024 / 1024:.2f} MB")
    t0 = time.monotonic()
    pc = PointCloud2.lcm_decode(PREMAP_PATH.read_bytes())
    pcd = pc.pointcloud
    dt = time.monotonic() - t0
    pcd_info("premap", pcd)
    print(f"  加载耗时: {dt:.2f}s")
    return pcd


# ── 2. 从 db 累积 local_map (纯 Open3D, 不依赖 VoxelGrid/torch) ──
def build_local_map() -> o3d.geometry.PointCloud:
    banner("从录制 db 累积 local_map (纯 Open3D 体素去重)")
    print(f"  文件: {DB_PATH}")
    print(f"  跳过前 {SEEK_FRAMES} 帧, 累积 {N_LIDAR_FRAMES} 帧 lidar")
    store = SqliteStore(path=str(DB_PATH), must_exist=True)
    lidar_stream = store.stream("lidar")
    total = lidar_stream.count()
    print(f"  lidar 总帧数: {total}")

    # 用 Open3d VoxelGrid 去重累积, 等效于 VoxelGridMapper(carve=False)。
    # 不引入 dimos.mapping.voxels.VoxelGrid, 避免拉入 torch/memory2.module。
    all_points: list[np.ndarray] = []
    t0 = time.monotonic()
    count = 0
    for i, obs in enumerate(lidar_stream.order_by("ts")):
        if i < SEEK_FRAMES:
            continue
        if count >= N_LIDAR_FRAMES:
            break
        frame: PointCloud2 = obs.data
        if isinstance(frame, PointCloud2):
            pts, _ = frame.as_numpy()
            if len(pts) > 0:
                all_points.append(pts)
                count += 1
    dt = time.monotonic() - t0
    print(f"  读取 {count} 帧, 耗时 {dt:.1f}s")

    # 拼接 + 体素下采样去重 (等效于 VoxelGrid 的累积效果)
    raw = np.concatenate(all_points, axis=0)
    print(f"  拼接后总点数: {len(raw):,}")
    pcd = o3d.geometry.PointCloud()
    pcd.points = o3d.utility.Vector3dVector(raw)
    pcd = pcd.voxel_down_sample(ACCUM_VOXEL)
    pcd_info("local_map (去重后)", pcd)
    return pcd


# ── 3. 逐步调试 relocalize ────────────────────────────
def debug_relocalize(
    global_map: o3d.geometry.PointCloud,
    local_map: o3d.geometry.PointCloud,
) -> None:
    banner("逐步调试 relocalize()")

    _reg = o3d.pipelines.registration
    FINE_VOXEL = _reloc_mod.FINE_VOXEL
    RERANK_DIST = _reloc_mod.RERANK_DIST
    SCALE_PLAN = _reloc_mod.SCALE_PLAN
    RANSAC_ITERS = _reloc_mod.RANSAC_ITERS

    print("  参数:")
    print(f"    SCALE_PLAN = {SCALE_PLAN}")
    print(f"    RANSAC_ITERS = {RANSAC_ITERS:,}")
    print(f"    FINE_VOXEL = {FINE_VOXEL}")
    print(f"    RERANK_DIST = {RERANK_DIST}")
    print(f"    GRAVITY_TILT_MAX_DEG = {_reloc_mod.GRAVITY_TILT_MAX_DEG}")

    # ---- Step 0: 细尺度下采样 ----
    step(0, "细尺度下采样 (FINE_VOXEL=0.1)")
    src_fine = local_map.voxel_down_sample(FINE_VOXEL)
    src_fine.estimate_normals(
        o3d.geometry.KDTreeSearchParamHybrid(radius=FINE_VOXEL * 2, max_nn=30)
    )
    tgt_fine = _reloc_mod._global_fine(global_map, FINE_VOXEL)
    pcd_info("src_fine (local)", src_fine)
    pcd_info("tgt_fine (premap)", tgt_fine)
    print(f"  src_fine 法向示例 (前3个): {np.asarray(src_fine.normals)[:3].round(3).tolist()}")

    # ---- Step 1: 多尺度 RANSAC 生成候选 ----
    step(1, "多尺度 FPFH+RANSAC 生成候选")
    candidates: list[np.ndarray] = []
    for vs, n_runs in SCALE_PLAN:
        print(f"\n  尺度 voxel_size={vs}m, 重启 {n_runs} 次:")
        src_down, src_fpfh = _reloc_mod._preprocess(local_map, vs)
        tgt_down, tgt_fpfh = _reloc_mod._global_preprocess(global_map, vs)
        pcd_info(f"    src_down(vs={vs})", src_down)
        print(f"    src_fpfh 特征维度: {src_fpfh.data.shape}")
        for run_i in range(n_runs):
            t0 = time.monotonic()
            result = _reloc_mod._ransac(src_down, tgt_down, src_fpfh, tgt_fpfh, vs)
            dt = time.monotonic() - t0
            T = np.asarray(result.transformation)
            fit = result.fitness
            inlier = result.inlier_rmse
            yaw = np.degrees(np.arctan2(T[1, 0], T[0, 0]))
            print(
                f"    run {run_i}: fitness={fit:.3f} inlier_rmse={inlier:.3f} "
                f"yaw={yaw:.1f}° t=({T[0, 3]:.1f},{T[1, 3]:.1f},{T[2, 3]:.1f}) [{dt:.1f}s]"
            )
            candidates.append(T)
    print(f"\n  候选总数 (Step 1): {len(candidates)}")

    # ---- Step 2: 质心 yaw flip ----
    step(2, "质心 yaw flip (绕 local 质心转 180°)")
    src_pts = np.asarray(src_fine.points)
    c_body = np.array([src_pts[:, 0].mean(), src_pts[:, 1].mean(), 0.0])
    rz180 = np.diag([-1.0, -1.0, 1.0])
    t_body_flip = np.eye(4)
    t_body_flip[:3, :3] = rz180
    t_body_flip[:3, 3] = c_body - rz180 @ c_body
    print(f"  local 质心 c_body = ({c_body[0]:.2f}, {c_body[1]:.2f}, {c_body[2]:.2f})")
    print(
        f"  t_body_flip 平移项 = ({t_body_flip[0, 3]:.2f}, {t_body_flip[1, 3]:.2f}, {t_body_flip[2, 3]:.2f})"
    )
    print("  (= 2*Cx, 2*Cy, 0 = 2x质心)")
    n_before = len(candidates)
    candidates = candidates + [T @ t_body_flip for T in candidates]
    print(f"  候选数: {n_before} → {len(candidates)} (翻倍)")

    # 看第一个候选和它的 flip 版本
    print("\n  候选[0] vs 候选[0]_flip:")
    yaw0 = np.degrees(np.arctan2(candidates[0][1, 0], candidates[0][0, 0]))
    yaw_f = np.degrees(np.arctan2(candidates[n_before][1, 0], candidates[n_before][0, 0]))
    print(f"    原始:   yaw={yaw0:.1f}°  t=({candidates[0][0, 3]:.1f},{candidates[0][1, 3]:.1f})")
    print(
        f"    flip版: yaw={yaw_f:.1f}°  t=({candidates[n_before][0, 3]:.1f},{candidates[n_before][1, 3]:.1f})"
    )

    # ---- Step 3: 重力过滤 ----
    step(3, "重力方向过滤 (倾斜>10°剔除)")
    upright = [
        T for T in candidates if _reloc_mod._gravity_tilt_deg(T) <= _reloc_mod.GRAVITY_TILT_MAX_DEG
    ]
    pool = upright if upright else candidates
    tilts = [_reloc_mod._gravity_tilt_deg(T) for T in candidates]
    print(
        f"  候选倾斜角分布: min={min(tilts):.1f}° max={max(tilts):.1f}° "
        f"median={np.median(tilts):.1f}°"
    )
    print(f"  通过重力过滤: {len(upright)}/{len(candidates)}")
    if not upright:
        print("  ⚠ 全部被剔除, 退回全集")
    print(f"  pool 大小: {len(pool)}")

    # ---- Step 4: wall-only 子集 ----
    step(4, "构造 wall-only 点云 (|nz| < 0.7)")

    def _wall_subset(cloud: o3d.geometry.PointCloud) -> o3d.geometry.PointCloud:
        nrm = np.asarray(cloud.normals)
        mask = np.abs(nrm[:, 2]) < 0.7
        if mask.sum() < 100:
            return cloud
        sub = o3d.geometry.PointCloud()
        sub.points = o3d.utility.Vector3dVector(np.asarray(cloud.points)[mask])
        sub.normals = o3d.utility.Vector3dVector(nrm[mask])
        return sub

    src_walls = _wall_subset(src_fine)
    tgt_walls = _wall_subset(tgt_fine)
    src_nz = np.abs(np.asarray(src_fine.normals)[:, 2])
    tgt_nz = np.abs(np.asarray(tgt_fine.normals)[:, 2])
    print("  src (local):")
    print(f"    总点数: {len(src_fine.points):,}")
    print(
        f"    墙面点: {len(src_walls.points):,} ({len(src_walls.points) / len(src_fine.points) * 100:.0f}%)"
    )
    print(
        f"    |nz| 分布: min={src_nz.min():.2f} median={np.median(src_nz):.2f} max={src_nz.max():.2f}"
    )
    print("  tgt (premap):")
    print(f"    总点数: {len(tgt_fine.points):,}")
    print(
        f"    墙面点: {len(tgt_walls.points):,} ({len(tgt_walls.points) / len(tgt_fine.points) * 100:.0f}%)"
    )
    print(
        f"    |nz| 分布: min={tgt_nz.min():.2f} median={np.median(tgt_nz):.2f} max={tgt_nz.max():.2f}"
    )

    # ---- Step 5: wall-only fitness 重排 ----
    step(5, "wall-only fitness 重排, 取 top-10")

    def fine_fitness(T: np.ndarray) -> float:
        r = _reg.evaluate_registration(src_walls, tgt_walls, RERANK_DIST, T)
        return float(r.fitness)

    scored = [(fine_fitness(T), i, T) for i, T in enumerate(pool)]
    scored.sort(key=lambda x: x[0], reverse=True)
    print("  pool 中所有候选的 wall fitness:")
    for fit, i, T in scored[:15]:
        yaw = np.degrees(np.arctan2(T[1, 0], T[0, 0]))
        tag = "flip" if i >= n_before else "orig"
        print(
            f"    候选#{i}({tag}): fitness={fit:.4f} yaw={yaw:.1f}° t=({T[0, 3]:.1f},{T[1, 3]:.1f})"
        )
    if len(scored) > 15:
        print(f"    ... 还有 {len(scored) - 15} 个")
    top_k = [T for _, _, T in scored[:10]]
    print("  top-10 选定, 进入 ICP 精修")

    # ---- Step 6: wall-only ICP 精修 ----
    step(6, "wall-only point-to-plane ICP 精修 (top-10)")
    tukey = _reg.TransformationEstimationPointToPlane(_reg.TukeyLoss(k=RERANK_DIST))
    polished: list[tuple[float, np.ndarray]] = []
    for idx, T0 in enumerate(top_k):
        t0 = time.monotonic()
        r = _reg.registration_icp(
            src_walls,
            tgt_walls,
            RERANK_DIST,
            T0,
            tukey,
            _reg.ICPConvergenceCriteria(max_iteration=70),
        )
        dt = time.monotonic() - t0
        yaw = np.degrees(np.arctan2(r.transformation[1, 0], r.transformation[0, 0]))
        print(
            f"    ICP #{idx}: fitness={r.fitness:.4f} inlier_rmse={r.inlier_rmse:.4f} "
            f"yaw={yaw:.1f}° t=({r.transformation[0, 3]:.1f},{r.transformation[1, 3]:.1f}) [{dt:.1f}s]"
        )
        polished.append((float(r.fitness), np.asarray(r.transformation)))
    best_fit, best_T = max(polished, key=lambda fT: fT[0])
    print("\n  最佳候选 (wall ICP fitness 最高):")
    print(f"    fitness = {best_fit:.4f}")
    mat_info("    best_T", best_T)

    # ---- Step 7: final ICP (完整点云) ----
    step(7, "final ICP (完整点云, 恢复地面约束)")
    t0 = time.monotonic()
    final = _reg.registration_icp(
        src_fine,
        tgt_fine,
        RERANK_DIST,
        best_T,
        tukey,
        _reg.ICPConvergenceCriteria(max_iteration=50),
    )
    dt = time.monotonic() - t0
    final_T = np.asarray(final.transformation)
    print(f"  final fitness={final.fitness:.4f} inlier_rmse={final.inlier_rmse:.4f} [{dt:.1f}s]")
    mat_info("  final_T", final_T)

    # ---- 汇总 ----
    banner("结果汇总")
    print(f"  返回的 fitness (wall-only best): {best_fit:.4f}")
    print("  返回的 T (final ICP, map<-world):")
    mat_info("  T", final_T)
    T_inv = np.linalg.inv(final_T)
    yaw_inv = np.degrees(np.arctan2(T_inv[1, 0], T_inv[0, 0]))
    print("  world<-map (TF 发布用):")
    print(f"    yaw={yaw_inv:.1f}°  t=({T_inv[0, 3]:.2f}, {T_inv[1, 3]:.2f}, {T_inv[2, 3]:.2f})")
    print(
        f"\n  判定: fitness={best_fit:.3f} {'≥' if best_fit >= 0.45 else '<'} 0.45 "
        f"→ {'通过 ✓' if best_fit >= 0.45 else '拒绝 ✗'}"
    )


# ── 入口 ────────────────────────────────────────────────
def main() -> None:
    print("relocalize() 逐步调试脚本")
    print(f"Python: {sys.executable}")

    premap = load_premap()
    local_map = build_local_map()

    if len(local_map.points) < MIN_LOCAL_POINTS:
        print(f"\n⚠ local_map 点数不足 ({len(local_map.points)} < {MIN_LOCAL_POINTS})")
        print("  增大 N_LIDAR_FRAMES 或减小 SEEK_FRAMES 后重试")
        sys.exit(1)

    t0 = time.monotonic()
    debug_relocalize(premap, local_map)
    total = time.monotonic() - t0
    print(f"\n总调试耗时: {total:.1f}s")

    # 同时跑一次原版 relocalize 做对比
    banner("对比: 直接调用原版 relocalize()")
    _reloc_mod._GLOBAL_CACHE.clear()  # 清缓存, 和调试流程公平对比
    t0 = time.monotonic()
    T, fitness = _reloc_mod.relocalize(premap, local_map)
    dt = time.monotonic() - t0
    print(f"  fitness={fitness:.4f}  耗时={dt:.1f}s")
    mat_info("  T", T)
    print("\n  (应与上面调试结果一致)")


if __name__ == "__main__":
    main()
