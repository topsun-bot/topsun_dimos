#!/usr/bin/env python3
"""Demo A —— 实时订阅 Go2 的雷达 / 里程计 / 摄像头并打印统计信息。

不保存数据，纯粹用来验证：
1. WebRTC 是否连得上
2. 三种数据流是不是都在到达
3. 各自的 FPS 大概多少
4. 最新一帧长什么样

按 Ctrl+C 退出。

用法:
    python subscribe_demo.py                       # 默认 IP 192.168.123.161
    python subscribe_demo.py --ip 192.168.123.161
    ROBOT_IP=10.0.0.5 python subscribe_demo.py
    python subscribe_demo.py --duration 30         # 跑 30 秒自动停
    python subscribe_demo.py --no-video            # 关掉视频订阅
"""

from __future__ import annotations

import sys
import threading
import time

# 将本目录加到 sys.path，让 `python subscribe_demo.py` 这种直接调用也能 import lib
sys.path.insert(0, str(__import__("pathlib").Path(__file__).parent))

from lib import (  # noqa: E402
    FpsCounter,
    connect,
    disconnect,
    install_signal_handlers,
    parse_common_args,
    summarize_image,
    summarize_odom,
    summarize_pointcloud,
)


# 最新一帧的引用（线程安全用 lock 保护）
_latest_lidar = None
_latest_odom = None
_latest_video = None
_lock = threading.Lock()


def _make_handlers() -> tuple[FpsCounter, FpsCounter, FpsCounter]:
    """返回三个 FpsCounter，并定义对应的回调函数挂到 Observable 上。

    回调里只更新 latest 引用 + tick 计数，不打印（打印交给主线程定时做，
    避免回调太密集时把控制台刷爆）。
    """
    return (
        FpsCounter("lidar"),
        FpsCounter("odom"),
        FpsCounter("video"),
    )


def main() -> None:
    args = parse_common_args()

    shutdown_event = threading.Event()
    install_signal_handlers(shutdown_event)

    conn = connect(args.ip, mode=args.mode)

    fps_lidar, fps_odom, fps_video = _make_handlers()
    subs: list[object] = []

    # 订阅雷达
    if not args.no_lidar:
        def on_lidar(cloud: object) -> None:
            global _latest_lidar
            with _lock:
                _latest_lidar = cloud
            fps_lidar.tick()

        sub = conn.lidar_stream().subscribe(  # type: ignore[attr-defined]
            on_next=on_lidar,
            on_error=lambda e: print(f"[lidar] 流异常: {e}", file=sys.stderr),
        )
        subs.append(sub)
        print(f"[{_now()}] 已订阅 lidar")

    # 订阅里程计
    if not args.no_odom:
        def on_odom(odom: object) -> None:
            global _latest_odom
            with _lock:
                _latest_odom = odom
            fps_odom.tick()

        sub = conn.odom_stream().subscribe(  # type: ignore[attr-defined]
            on_next=on_odom,
            on_error=lambda e: print(f"[odom] 流异常: {e}", file=sys.stderr),
        )
        subs.append(sub)
        print(f"[{_now()}] 已订阅 odom")

    # 订阅摄像头
    if not args.no_video:
        def on_video(img: object) -> None:
            global _latest_video
            with _lock:
                _latest_video = img
            fps_video.tick()

        sub = conn.video_stream().subscribe(  # type: ignore[attr-defined]
            on_next=on_video,
            on_error=lambda e: print(f"[video] 流异常: {e}", file=sys.stderr),
        )
        subs.append(sub)
        print(f"[{_now()}] 已订阅 video（首帧大约 1-2 秒后到达）")

    # ---------- 主循环：每秒打一行统计 ----------
    print(f"\n开始接收数据。Ctrl+C 退出。\n")
    start = time.monotonic()
    try:
        while not shutdown_event.is_set():
            shutdown_event.wait(timeout=1.0)
            elapsed = time.monotonic() - start
            with _lock:
                lidar = _latest_lidar
                odom = _latest_odom
                video = _latest_video

            print(f"[T={elapsed:5.1f}s]")
            if not args.no_lidar:
                if lidar is not None:
                    print(
                        f"  lidar  fps={fps_lidar.fps():5.1f}  "
                        f"总数={fps_lidar.total:>5d}  最新: {summarize_pointcloud(lidar)}"
                    )
                else:
                    print(f"  lidar  fps={fps_lidar.fps():5.1f}  （还没到第一帧）")
            if not args.no_odom:
                if odom is not None:
                    print(
                        f"  odom   fps={fps_odom.fps():5.1f}  "
                        f"总数={fps_odom.total:>5d}  最新: {summarize_odom(odom)}"
                    )
                else:
                    print(f"  odom   fps={fps_odom.fps():5.1f}  （还没到第一帧）")
            if not args.no_video:
                if video is not None:
                    print(
                        f"  video  fps={fps_video.fps():5.1f}  "
                        f"总数={fps_video.total:>5d}  最新: {summarize_image(video)}"
                    )
                else:
                    print(f"  video  fps={fps_video.fps():5.1f}  （还没到第一帧）")

            if args.duration > 0 and elapsed >= args.duration:
                print(f"\n已运行 {args.duration} 秒，自动退出")
                break
    finally:
        # ---------- 清理：dispose 订阅 + 断开连接 ----------
        for sub in subs:
            try:
                sub.dispose()  # type: ignore[attr-defined]
            except Exception:
                pass
        disconnect(conn)
        print(f"\n累计：lidar={fps_lidar.total} 帧, odom={fps_odom.total} 帧, video={fps_video.total} 帧")


def _now() -> str:
    return time.strftime("%H:%M:%S")


if __name__ == "__main__":
    main()
