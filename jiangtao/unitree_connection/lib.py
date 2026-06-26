"""公共工具模块：连接管理、信号处理、数据格式化。

被 `subscribe_demo.py` 和 `save_demo.py` 共用。
"""

from __future__ import annotations

import argparse
from collections.abc import Callable
from dataclasses import dataclass
import os
import signal
import sys
import threading
import time

import numpy as np


# ---------------------------------------------------------------
# 1. 命令行参数解析
# ---------------------------------------------------------------


def parse_common_args(extra: Callable[[argparse.ArgumentParser], None] | None = None) -> argparse.Namespace:
    """解析 demo 共用的命令行参数。

    Args:
        extra: 可选回调，给 ArgumentParser 添加额外参数。
    """
    parser = argparse.ArgumentParser(
        description="直接通过 UnitreeWebRTCConnection 调取 Go2 数据",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--ip",
        default=os.environ.get("ROBOT_IP", "192.168.123.161"),
        help="Go2 IP 地址。也可通过环境变量 ROBOT_IP 设置",
    )
    parser.add_argument(
        "--mode",
        default="ai",
        choices=["ai", "normal"],
        help="机器人运动模式（连接后会切到这个模式）",
    )
    parser.add_argument(
        "--duration",
        type=float,
        default=0,
        help="运行时长（秒）。0 表示一直跑直到 Ctrl+C",
    )
    parser.add_argument(
        "--no-lidar",
        action="store_true",
        help="不订阅雷达点云",
    )
    parser.add_argument(
        "--no-odom",
        action="store_true",
        help="不订阅里程计",
    )
    parser.add_argument(
        "--no-video",
        action="store_true",
        help="不订阅摄像头",
    )
    if extra is not None:
        extra(parser)
    return parser.parse_args()


# ---------------------------------------------------------------
# 2. 优雅退出 —— Ctrl+C 触发 shutdown_event
# ---------------------------------------------------------------


def install_signal_handlers(shutdown_event: threading.Event) -> None:
    """注册 SIGINT / SIGTERM，触发 shutdown_event 让主循环优雅退出。"""

    def _handler(signum: int, _frame: object) -> None:
        sig_name = signal.Signals(signum).name
        print(f"\n收到信号 {sig_name}，准备退出 ...", file=sys.stderr)
        shutdown_event.set()

    signal.signal(signal.SIGINT, _handler)
    signal.signal(signal.SIGTERM, _handler)


# ---------------------------------------------------------------
# 3. 简单的滑窗 fps 统计器（多线程安全）
# ---------------------------------------------------------------


@dataclass
class FpsCounter:
    """统计某个数据流的实时帧率，过去 *window* 秒内的滑动平均。"""

    name: str
    window: float = 2.0
    _lock: threading.Lock = None  # type: ignore[assignment]
    _ts_buf: list[float] = None  # type: ignore[assignment]
    _total: int = 0

    def __post_init__(self) -> None:
        self._lock = threading.Lock()
        self._ts_buf = []

    def tick(self) -> None:
        """每收到一帧调一次。"""
        now = time.monotonic()
        with self._lock:
            self._ts_buf.append(now)
            self._total += 1
            cutoff = now - self.window
            while self._ts_buf and self._ts_buf[0] < cutoff:
                self._ts_buf.pop(0)

    def fps(self) -> float:
        with self._lock:
            n = len(self._ts_buf)
            if n < 2:
                return 0.0
            duration = self._ts_buf[-1] - self._ts_buf[0]
            return (n - 1) / duration if duration > 0 else 0.0

    @property
    def total(self) -> int:
        with self._lock:
            return self._total


# ---------------------------------------------------------------
# 4. 数据 → 字符串摘要（用于打印）
# ---------------------------------------------------------------


def summarize_pointcloud(cloud: object) -> str:
    """雷达点云摘要：点数 + xy 范围。"""
    try:
        points, _ = cloud.as_numpy()  # type: ignore[attr-defined]
        if len(points) == 0:
            return "空点云"
        x_lo, x_hi = float(points[:, 0].min()), float(points[:, 0].max())
        y_lo, y_hi = float(points[:, 1].min()), float(points[:, 1].max())
        z_lo, z_hi = float(points[:, 2].min()), float(points[:, 2].max())
        return (
            f"{len(points):>6d} 个点  "
            f"x[{x_lo:+.2f},{x_hi:+.2f}]m  "
            f"y[{y_lo:+.2f},{y_hi:+.2f}]m  "
            f"z[{z_lo:+.2f},{z_hi:+.2f}]m"
        )
    except Exception as e:
        return f"<解析失败: {e}>"


def summarize_odom(odom: object) -> str:
    """里程计摘要：位置 + yaw。"""
    try:
        pos = odom.position  # type: ignore[attr-defined]
        yaw_rad = odom.orientation.euler[2]  # type: ignore[attr-defined]
        yaw_deg = np.rad2deg(yaw_rad)
        return f"pos({pos.x:+.2f}, {pos.y:+.2f}, {pos.z:+.2f}) yaw={yaw_deg:+6.1f}°"
    except Exception as e:
        return f"<解析失败: {e}>"


def summarize_image(img: object) -> str:
    """图像摘要：分辨率 + 格式。"""
    try:
        return f"{img.shape} {img.format.value}"  # type: ignore[attr-defined]
    except Exception as e:
        return f"<解析失败: {e}>"


# ---------------------------------------------------------------
# 5. 连接 + 订阅 helper
# ---------------------------------------------------------------


def connect(ip: str, mode: str = "ai") -> object:
    """创建 UnitreeWebRTCConnection 实例并返回。

    将 import 延迟到运行时，避免脚本只是 ``--help`` 时也加载重型依赖。
    """
    from dimos.robot.unitree.connection import UnitreeWebRTCConnection

    print(f"[{_now()}] 正在连接 {ip} (mode={mode}) ...", flush=True)
    conn = UnitreeWebRTCConnection(ip=ip, mode=mode)
    print(f"[{_now()}] 已连接", flush=True)
    return conn


def disconnect(conn: object) -> None:
    """安全断开连接，吞掉所有清理时的异常。"""
    print(f"[{_now()}] 正在断开 ...", flush=True)
    try:
        conn.disconnect()  # type: ignore[attr-defined]
    except Exception as e:
        print(f"  disconnect 时异常（可忽略）: {e}", file=sys.stderr)
    print(f"[{_now()}] 已断开", flush=True)


def _now() -> str:
    return time.strftime("%H:%M:%S")
