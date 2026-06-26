#!/usr/bin/env python3
"""Demo B —— 录制 Go2 数据到磁盘。

每个数据流写到独立文件 / 子目录：

    recordings/<YYYYMMDD-HHMMSS>/
    ├── lidar/
    │   ├── 000000_<ts>.npy        # (N, 3) float32 点云
    │   └── ...
    ├── images/
    │   ├── 000000_<ts>.jpg        # 摄像头帧（jpeg 压缩）
    │   └── ...
    ├── odom.csv                   # 时间序列：ts, x, y, z, qx, qy, qz, qw
    └── meta.json                  # 录制元信息（IP / 起止时间 / 总帧数等）

用法:
    python save_demo.py --duration 10                    # 录 10 秒
    python save_demo.py --duration 30 --out /tmp/go2_rec
    python save_demo.py --no-video --duration 60         # 只录 lidar 和 odom
    python save_demo.py --jpeg-quality 85                # 调 jpeg 压缩质量
"""

from __future__ import annotations

import csv
import json
from pathlib import Path
import sys
import threading
import time

sys.path.insert(0, str(Path(__file__).parent))

from lib import (  # noqa: E402
    FpsCounter,
    connect,
    disconnect,
    install_signal_handlers,
    parse_common_args,
)


def _add_extra_args(parser) -> None:  # type: ignore[no-untyped-def]
    """添加 save_demo 专用参数。"""
    parser.add_argument(
        "--out",
        default="./recordings",
        help="录制根目录；脚本会在下面再建一个时间戳子目录",
    )
    parser.add_argument(
        "--jpeg-quality",
        type=int,
        default=90,
        help="JPEG 压缩质量 (1-100)，越大文件越大画质越好",
    )


def _ensure_dirs(root: Path, args) -> tuple[Path, Path | None, Path | None]:  # type: ignore[no-untyped-def]
    """根据 --no-* 参数创建对应子目录，返回每个数据流的目标路径。"""
    root.mkdir(parents=True, exist_ok=True)
    lidar_dir = (root / "lidar") if not args.no_lidar else None
    image_dir = (root / "images") if not args.no_video else None
    if lidar_dir is not None:
        lidar_dir.mkdir(exist_ok=True)
    if image_dir is not None:
        image_dir.mkdir(exist_ok=True)
    return root, lidar_dir, image_dir


def main() -> None:
    args = parse_common_args(extra=_add_extra_args)

    if args.duration <= 0:
        print("提示：--duration <= 0 时本脚本会一直录到 Ctrl+C", file=sys.stderr)

    # 录制目录用启动时间命名
    rec_root = Path(args.out) / time.strftime("%Y%m%d-%H%M%S")
    rec_root, lidar_dir, image_dir = _ensure_dirs(rec_root, args)
    print(f"[{_now()}] 录制目录: {rec_root}")

    shutdown_event = threading.Event()
    install_signal_handlers(shutdown_event)

    conn = connect(args.ip, mode=args.mode)
    started_at = time.time()

    fps_lidar = FpsCounter("lidar")
    fps_odom = FpsCounter("odom")
    fps_video = FpsCounter("video")
    subs: list[object] = []

    # 用一个 lock 保护文件写入；reactivex 回调可能在多个线程
    write_lock = threading.Lock()

    # ---------- 雷达：每帧存成一个 .npy ----------
    if not args.no_lidar:
        assert lidar_dir is not None

        def on_lidar(cloud: object) -> None:
            try:
                import numpy as np

                points, _ = cloud.as_numpy()  # type: ignore[attr-defined]
                idx = fps_lidar.total
                ts = getattr(cloud, "ts", None) or time.time()
                fname = f"{idx:06d}_{ts:.3f}.npy"
                with write_lock:
                    np.save(lidar_dir / fname, points.astype("float32"))
                fps_lidar.tick()
            except Exception as e:
                print(f"[lidar] 写盘失败: {e}", file=sys.stderr)

        sub = conn.lidar_stream().subscribe(  # type: ignore[attr-defined]
            on_next=on_lidar,
            on_error=lambda e: print(f"[lidar] 流异常: {e}", file=sys.stderr),
        )
        subs.append(sub)
        print(f"[{_now()}] 已订阅 lidar -> {lidar_dir}/")

    # ---------- 里程计：append 到一个 CSV ----------
    odom_csv = rec_root / "odom.csv"
    odom_writer: csv.writer | None = None  # type: ignore[name-defined]
    odom_file = None

    if not args.no_odom:
        odom_file = open(odom_csv, "w", newline="", encoding="utf-8")
        odom_writer = csv.writer(odom_file)
        odom_writer.writerow(["ts", "frame_id", "x", "y", "z", "qx", "qy", "qz", "qw"])

        def on_odom(odom: object) -> None:
            try:
                ts = getattr(odom, "ts", None) or time.time()
                pos = odom.position  # type: ignore[attr-defined]
                rot = odom.orientation  # type: ignore[attr-defined]
                fid = getattr(odom, "frame_id", "")
                with write_lock:
                    odom_writer.writerow(  # type: ignore[union-attr]
                        [
                            f"{ts:.6f}",
                            fid,
                            f"{pos.x:.6f}",
                            f"{pos.y:.6f}",
                            f"{pos.z:.6f}",
                            f"{rot.x:.6f}",
                            f"{rot.y:.6f}",
                            f"{rot.z:.6f}",
                            f"{rot.w:.6f}",
                        ]
                    )
                    odom_file.flush()  # type: ignore[union-attr]
                fps_odom.tick()
            except Exception as e:
                print(f"[odom] 写盘失败: {e}", file=sys.stderr)

        sub = conn.odom_stream().subscribe(  # type: ignore[attr-defined]
            on_next=on_odom,
            on_error=lambda e: print(f"[odom] 流异常: {e}", file=sys.stderr),
        )
        subs.append(sub)
        print(f"[{_now()}] 已订阅 odom  -> {odom_csv}")

    # ---------- 摄像头：每帧存成一个 jpeg ----------
    if not args.no_video:
        assert image_dir is not None

        def on_video(img: object) -> None:
            try:
                # 用 Pillow 直接编码，避免依赖 OpenCV
                from PIL import Image as PILImage

                arr = img.as_numpy()  # type: ignore[attr-defined] # (H, W, 3) RGB uint8
                idx = fps_video.total
                ts = getattr(img, "ts", None) or time.time()
                fname = f"{idx:06d}_{ts:.3f}.jpg"
                with write_lock:
                    PILImage.fromarray(arr).save(
                        image_dir / fname,
                        format="JPEG",
                        quality=args.jpeg_quality,
                    )
                fps_video.tick()
            except Exception as e:
                print(f"[video] 写盘失败: {e}", file=sys.stderr)

        sub = conn.video_stream().subscribe(  # type: ignore[attr-defined]
            on_next=on_video,
            on_error=lambda e: print(f"[video] 流异常: {e}", file=sys.stderr),
        )
        subs.append(sub)
        print(f"[{_now()}] 已订阅 video -> {image_dir}/")

    # ---------- 主循环：每秒打一行进度 ----------
    print(f"\n开始录制。Ctrl+C 退出。\n")
    try:
        while not shutdown_event.is_set():
            shutdown_event.wait(timeout=1.0)
            elapsed = time.monotonic() - 0  # 进度按 fps_*.total 累加更直观
            t = time.time() - started_at
            print(
                f"[T={t:5.1f}s] "
                f"lidar={fps_lidar.total:>5d}({fps_lidar.fps():4.1f}fps) "
                f"odom={fps_odom.total:>5d}({fps_odom.fps():4.1f}fps) "
                f"video={fps_video.total:>5d}({fps_video.fps():4.1f}fps)"
            )
            if args.duration > 0 and t >= args.duration:
                print(f"\n已录满 {args.duration} 秒")
                break
    finally:
        # ---------- 清理：先 dispose 订阅，再关 CSV，再断 WebRTC ----------
        for sub in subs:
            try:
                sub.dispose()  # type: ignore[attr-defined]
            except Exception:
                pass
        if odom_file is not None:
            odom_file.close()
        disconnect(conn)

        # ---------- 写元信息 ----------
        meta = {
            "ip": args.ip,
            "mode": args.mode,
            "started_at": started_at,
            "stopped_at": time.time(),
            "duration_sec": time.time() - started_at,
            "frames": {
                "lidar": fps_lidar.total,
                "odom": fps_odom.total,
                "video": fps_video.total,
            },
            "settings": {
                "no_lidar": args.no_lidar,
                "no_odom": args.no_odom,
                "no_video": args.no_video,
                "jpeg_quality": args.jpeg_quality,
            },
        }
        (rec_root / "meta.json").write_text(json.dumps(meta, indent=2, ensure_ascii=False))

        print(f"\n录制完成: {rec_root}")
        print(f"  lidar={fps_lidar.total} 帧, odom={fps_odom.total} 行, video={fps_video.total} 帧")


def _now() -> str:
    return time.strftime("%H:%M:%S")


if __name__ == "__main__":
    main()
