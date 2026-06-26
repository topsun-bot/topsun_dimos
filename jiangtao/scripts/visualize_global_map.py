#!/usr/bin/env python3
"""
可视化全局点云地图, 按高度着色.

功能:
  - 加载 global_map.npy 点云文件
  - 按 Z 轴(高度)映射颜色 (蓝→青→绿→黄→红)
  - 使用 matplotlib 3D 散点图或 Open3D 交互式渲染

用法:
  python jiangtao/scripts/visualize_global_map.py [--input pcd/global_map.npy] [--backend matplotlib]

可选后端:
  - matplotlib: 静态 3D 图 (默认, 无需额外依赖)
  - open3d: 交互式 3D 渲染 (需要 open3d, 可旋转/缩放)
"""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="可视化全局点云地图 (按高度着色)")
    parser.add_argument(
        "--input",
        type=str,
        default="jiangtao/data/20260626-recording_go2/pcd/global_map.npy",
        help="全局点云 .npy 文件路径",
    )
    parser.add_argument(
        "--backend",
        choices=["matplotlib", "open3d"],
        default="open3d",
        help="可视化后端 (默认: matplotlib)",
    )
    parser.add_argument(
        "--downsample",
        type=int,
        default=5,
        help="可视化时的额外降采样倍率 (仅 matplotlib, 默认: 5)",
    )
    parser.add_argument(
        "--point-size",
        type=float,
        default=0.5,
        help="点大小 (matplotlib 默认: 0.5)",
    )
    parser.add_argument(
        "--save",
        type=str,
        default=None,
        help="保存图片路径 (仅 matplotlib, 不指定则弹窗显示)",
    )
    parser.add_argument(
        "--z-min",
        type=float,
        default=None,
        help="过滤掉低于此高度的点 (m), 例如 --z-min 0 去掉地面",
    )
    parser.add_argument(
        "--z-max",
        type=float,
        default=None,
        help="过滤掉高于此高度的点 (m)",
    )
    parser.add_argument(
        "--elevation",
        type=float,
        default=60,
        help="matplotlib 视角仰角 (默认: 60)",
    )
    parser.add_argument(
        "--azimuth",
        type=float,
        default=-90,
        help="matplotlib 视角方位角 (默认: -90, 即俯视偏北)",
    )
    return parser.parse_args()


def height_colormap(z: np.ndarray) -> np.ndarray:
    """将高度值映射到 RGBA 颜色数组 (jet colormap).

    返回 (N, 4) float array, 值范围 [0, 1].
    """
    import matplotlib.cm as cm

    z_min, z_max = z.min(), z.max()
    if z_max - z_min < 1e-6:
        z_norm = np.zeros_like(z)
    else:
        z_norm = (z - z_min) / (z_max - z_min)

    colors = cm.jet(z_norm)
    return colors


def visualize_matplotlib(
    points: np.ndarray,
    downsample: int,
    point_size: float,
    save_path: str | None,
    elevation: float,
    azimuth: float,
) -> None:
    """用 matplotlib 进行 3D 散点图可视化."""
    import matplotlib.pyplot as plt

    # 降采样以加快渲染
    if downsample > 1:
        indices = np.arange(0, len(points), downsample)
        pts = points[indices]
    else:
        pts = points

    print(f"可视化点数: {len(pts):,} (原始 {len(points):,}, 降采样 1/{downsample})")

    colors = height_colormap(pts[:, 2])

    fig = plt.figure(figsize=(16, 12))
    ax = fig.add_subplot(111, projection="3d")
    ax.scatter(
        pts[:, 0],
        pts[:, 1],
        pts[:, 2],
        c=colors,
        s=point_size,
        marker=".",
        alpha=0.8,
    )

    ax.set_xlabel("X (m)")
    ax.set_ylabel("Y (m)")
    ax.set_zlabel("Z (m)")
    ax.set_title(
        f"Global Map - {len(points):,} points\n"
        f"Height range: [{points[:, 2].min():.2f}, {points[:, 2].max():.2f}] m"
    )
    ax.view_init(elev=elevation, azim=azimuth)

    # 等比例坐标轴
    max_range = np.array([
        pts[:, 0].max() - pts[:, 0].min(),
        pts[:, 1].max() - pts[:, 1].min(),
        pts[:, 2].max() - pts[:, 2].min(),
    ]).max() / 2.0
    mid_x = (pts[:, 0].max() + pts[:, 0].min()) * 0.5
    mid_y = (pts[:, 1].max() + pts[:, 1].min()) * 0.5
    mid_z = (pts[:, 2].max() + pts[:, 2].min()) * 0.5
    ax.set_xlim(mid_x - max_range, mid_x + max_range)
    ax.set_ylim(mid_y - max_range, mid_y + max_range)
    ax.set_zlim(mid_z - max_range, mid_z + max_range)

    # 添加颜色条
    sm = plt.cm.ScalarMappable(
        cmap="jet",
        norm=plt.Normalize(vmin=points[:, 2].min(), vmax=points[:, 2].max()),
    )
    sm.set_array([])
    cbar = plt.colorbar(sm, ax=ax, shrink=0.6, pad=0.1)
    cbar.set_label("Height / Z (m)")

    plt.tight_layout()
    if save_path:
        plt.savefig(save_path, dpi=150, bbox_inches="tight")
        print(f"图片已保存到: {save_path}")
    else:
        plt.show()


def visualize_open3d(points: np.ndarray) -> None:
    """用 Open3D 进行交互式 3D 可视化."""
    import open3d as o3d

    print(f"Open3D 可视化, 点数: {len(points):,}")

    # 构建点云
    pcd = o3d.geometry.PointCloud()
    pcd.points = o3d.utility.Vector3dVector(points.astype(np.float64))

    # 按高度着色
    colors = height_colormap(points[:, 2])[:, :3]  # 只取 RGB, 去掉 alpha
    pcd.colors = o3d.utility.Vector3dVector(colors)

    # 渲染
    o3d.visualization.draw_geometries(
        [pcd],
        window_name="Global Map (height-colored)",
        width=1280,
        height=720,
        point_show_normal=False,
    )


def main() -> None:
    args = parse_args()

    input_path = Path(args.input)
    if not input_path.is_absolute():
        input_path = Path(__file__).resolve().parent.parent.parent / input_path

    if not input_path.exists():
        print(f"错误: 找不到点云文件 {input_path}")
        return

    # 加载点云
    print(f"加载点云: {input_path}")
    points = np.load(input_path)
    print(f"点云形状: {points.shape}")
    print(f"X 范围: [{points[:, 0].min():.3f}, {points[:, 0].max():.3f}] m")
    print(f"Y 范围: [{points[:, 1].min():.3f}, {points[:, 1].max():.3f}] m")
    print(f"Z 范围: [{points[:, 2].min():.3f}, {points[:, 2].max():.3f}] m")

    # 高度过滤
    mask = np.ones(len(points), dtype=bool)
    if args.z_min is not None:
        mask &= points[:, 2] >= args.z_min
        print(f"过滤 Z < {args.z_min}m (去除地面)")
    if args.z_max is not None:
        mask &= points[:, 2] <= args.z_max
        print(f"过滤 Z > {args.z_max}m (去除天花板)")
    if not mask.all():
        points = points[mask]
        print(f"过滤后点数: {len(points):,}")
    print()

    if args.backend == "open3d":
        visualize_open3d(points)
    else:
        visualize_matplotlib(
            points,
            downsample=args.downsample,
            point_size=args.point_size,
            save_path=args.save,
            elevation=args.elevation,
            azimuth=args.azimuth,
        )


if __name__ == "__main__":
    main()
