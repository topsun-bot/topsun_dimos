#!/usr/bin/env python3
"""
解析 recording_go2.db 数据库, 将数据导出为可用格式.

功能:
  1. color_image 流 → imgs/ 目录下的 .jpg 文件 (文件名为时间戳)
  2. lidar 流 → pcd/global_map.npy (全局累积点云, world frame)
  3. odom 流 → odom/ 目录下的 .txt 文件 (文件名与对应图像时间戳一致)

用法:
  python jiangtao/scripts/parse_recording_db.py [--db recording_go2.db] [--out jiangtao/data/20260626-recording_go2]

输出结构:
  <out>/
  ├── imgs/           # 彩色图像 (JPEG)
  │   ├── 1779716123.562.jpg
  │   ├── 1779716123.638.jpg
  │   └── ...
  ├── pcd/
  │   └── global_map.npy   # (N, 3) float32 全局累积点云
  └── odom/
      ├── 1779716123.562.txt   # 与图像同名, 内容为最近邻插值的 odom 位姿
      ├── 1779716123.638.txt
      └── ...

odom txt 格式 (每行一个值):
  timestamp x y z qx qy qz qw yaw pitch roll
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="解析 recording_go2.db 到可用数据格式")
    parser.add_argument(
        "--db",
        type=str,
        default="recording_go2.db",
        help="SQLite 数据库路径 (默认: recording_go2.db)",
    )
    parser.add_argument(
        "--out",
        type=str,
        default="jiangtao/data/20260626-recording_go2",
        help="输出根目录 (默认: jiangtao/data/20260626-recording_go2)",
    )
    parser.add_argument(
        "--voxel",
        type=float,
        default=0.05,
        help="全局地图体素降采样大小, 单位 m (默认: 0.05)",
    )
    parser.add_argument(
        "--skip-imgs",
        action="store_true",
        help="跳过图像导出 (已有数据时可用)",
    )
    parser.add_argument(
        "--skip-pcd",
        action="store_true",
        help="跳过点云导出",
    )
    parser.add_argument(
        "--skip-odom",
        action="store_true",
        help="跳过里程计导出",
    )
    return parser.parse_args()


def export_images(store, out_dir: Path) -> list[float]:
    """导出 color_image 流为 JPEG 文件, 返回所有导出帧的时间戳列表."""
    from dimos.msgs.sensor_msgs.Image import Image

    imgs_dir = out_dir / "imgs"
    imgs_dir.mkdir(parents=True, exist_ok=True)

    images = store.stream("color_image", Image)
    total = images.count()
    print(f"[图像] 共 {total} 帧, 导出到 {imgs_dir}/")

    timestamps: list[float] = []
    for i, obs in enumerate(images):
        ts = obs.ts
        timestamps.append(ts)
        filename = f"{ts:.3f}.jpg"
        filepath = imgs_dir / filename

        # Image 对象直接导出 JPEG
        jpeg_bytes = obs.data.to_jpeg_bytes()
        filepath.write_bytes(jpeg_bytes)

        if (i + 1) % 500 == 0 or (i + 1) == total:
            print(f"  [{i+1}/{total}] 已导出")

    print(f"[图像] 完成, 共导出 {len(timestamps)} 张图片")
    return timestamps


def export_global_map(store, out_dir: Path, voxel_size: float) -> None:
    """累积 lidar 流的全部帧为全局点云, 导出为 .npy 文件."""
    from dimos.msgs.sensor_msgs.PointCloud2 import PointCloud2

    pcd_dir = out_dir / "pcd"
    pcd_dir.mkdir(parents=True, exist_ok=True)

    lidar = store.stream("lidar", PointCloud2)
    total = lidar.count()
    print(f"[点云] 共 {total} 帧, 累积全局地图 (voxel={voxel_size}m)...")

    # 收集所有点 (lidar 已经是 world frame, 直接拼接)
    all_points: list[np.ndarray] = []
    for i, obs in enumerate(lidar):
        pts = obs.data.points_f32()  # (N, 3) numpy array
        if len(pts) > 0:
            all_points.append(pts)
        if (i + 1) % 500 == 0 or (i + 1) == total:
            print(f"  [{i+1}/{total}] 已读取")

    if not all_points:
        print("[点云] 警告: 无有效点云数据!")
        return

    # 拼接所有点
    merged = np.concatenate(all_points, axis=0).astype(np.float32)
    print(f"[点云] 合并后总点数: {merged.shape[0]:,}")

    # 体素降采样: numpy unique 方式 (高效处理大规模点云)
    print(f"[点云] 体素降采样 (voxel={voxel_size}m)...")
    grid_indices = np.floor(merged / voxel_size).astype(np.int32)
    # 找到唯一体素, 并为每个点分配体素编号
    _, inverse, counts = np.unique(
        grid_indices, axis=0, return_inverse=True, return_counts=True
    )
    # 按体素编号累加坐标, 再除以计数得均值
    n_voxels = len(counts)
    sums = np.zeros((n_voxels, 3), dtype=np.float64)
    np.add.at(sums, inverse, merged)
    downsampled = (sums / counts[:, None]).astype(np.float32)
    print(f"[点云] 降采样后点数: {downsampled.shape[0]:,}")

    # 保存
    out_path = pcd_dir / "global_map.npy"
    np.save(out_path, downsampled)
    print(f"[点云] 已保存到 {out_path} ({out_path.stat().st_size / 1024 / 1024:.1f} MB)")


def export_odom(store, out_dir: Path, image_timestamps: list[float]) -> None:
    """导出 odom 流为 txt 文件, 每个文件对应一张图像的时间戳.

    对于每个图像时间戳, 找 odom 中最近邻的帧, 输出其位姿.
    """
    from dimos.msgs.geometry_msgs.PoseStamped import PoseStamped

    odom_dir = out_dir / "odom"
    odom_dir.mkdir(parents=True, exist_ok=True)

    # 先加载所有 odom 数据到内存 (数据量小, 0.7MB)
    odom = store.stream("odom", PoseStamped)
    total = odom.count()
    print(f"[里程计] 共 {total} 帧, 加载中...")

    odom_data: list[tuple[float, object]] = []
    for obs in odom:
        odom_data.append((obs.ts, obs.data))

    print(f"[里程计] 已加载 {len(odom_data)} 帧")

    # 用 numpy 做最近邻搜索
    odom_ts = np.array([t for t, _ in odom_data])
    img_ts = np.array(image_timestamps)

    print(f"[里程计] 为 {len(image_timestamps)} 张图像匹配最近邻 odom...")

    # 对每个图像时间戳找最近的 odom
    indices = np.searchsorted(odom_ts, img_ts)
    indices = np.clip(indices, 0, len(odom_ts) - 1)

    # 比较左右邻居选更近的
    for i in range(len(indices)):
        idx = indices[i]
        if idx > 0:
            if abs(odom_ts[idx - 1] - img_ts[i]) < abs(odom_ts[idx] - img_ts[i]):
                indices[i] = idx - 1

    exported = 0
    for i, (ts, odom_idx) in enumerate(zip(image_timestamps, indices)):
        _, pose_data = odom_data[odom_idx]
        filename = f"{ts:.3f}.txt"
        filepath = odom_dir / filename

        # 输出格式: timestamp x y z qx qy qz qw yaw pitch roll
        content = (
            f"{ts:.6f}\n"
            f"{pose_data.x:.6f}\n"
            f"{pose_data.y:.6f}\n"
            f"{pose_data.z:.6f}\n"
            f"{pose_data.orientation.x:.6f}\n"
            f"{pose_data.orientation.y:.6f}\n"
            f"{pose_data.orientation.z:.6f}\n"
            f"{pose_data.orientation.w:.6f}\n"
            f"{pose_data.yaw:.6f}\n"
            f"{pose_data.pitch:.6f}\n"
            f"{pose_data.roll:.6f}\n"
        )
        filepath.write_text(content)
        exported += 1

        if (i + 1) % 2000 == 0 or (i + 1) == len(image_timestamps):
            print(f"  [{i+1}/{len(image_timestamps)}] 已导出")

    print(f"[里程计] 完成, 共导出 {exported} 个 txt 文件")


def main() -> None:
    args = parse_args()

    # 确定工作目录: 如果 db 路径不是绝对路径, 从项目根查找
    db_path = Path(args.db)
    if not db_path.is_absolute():
        # 尝试从当前目录或项目根找
        candidates = [
            Path.cwd() / db_path,
            Path(__file__).resolve().parent.parent.parent / db_path,
        ]
        for c in candidates:
            if c.exists():
                db_path = c
                break

    if not db_path.exists():
        print(f"错误: 找不到数据库 {db_path}")
        sys.exit(1)

    out_dir = Path(args.out)
    if not out_dir.is_absolute():
        out_dir = Path(__file__).resolve().parent.parent.parent / out_dir

    print(f"数据库: {db_path}")
    print(f"输出目录: {out_dir}")
    print(f"体素大小: {args.voxel}m")
    print("=" * 60)

    # 延迟导入 dimos 依赖
    from dimos.memory2.store.sqlite import SqliteStore

    store = SqliteStore(path=str(db_path))
    print(f"已注册流: {store.list_streams()}")
    print()

    # 1. 导出图像
    image_timestamps: list[float] = []
    if not args.skip_imgs:
        image_timestamps = export_images(store, out_dir)
    else:
        # 如果跳过图像导出, 从已有文件读取时间戳
        imgs_dir = out_dir / "imgs"
        if imgs_dir.exists():
            image_timestamps = sorted(
                float(f.stem) for f in imgs_dir.glob("*.jpg")
            )
            print(f"[图像] 跳过, 从已有文件读取 {len(image_timestamps)} 个时间戳")
        else:
            print("[图像] 跳过, 且无已有文件")
    print()

    # 2. 导出全局点云
    if not args.skip_pcd:
        export_global_map(store, out_dir, args.voxel)
    else:
        print("[点云] 跳过")
    print()

    # 3. 导出里程计
    if not args.skip_odom:
        if image_timestamps:
            export_odom(store, out_dir, image_timestamps)
        else:
            print("[里程计] 错误: 无图像时间戳, 无法生成对应的 odom 文件")
    else:
        print("[里程计] 跳过")

    print()
    print("=" * 60)
    print("全部完成!")


if __name__ == "__main__":
    main()
