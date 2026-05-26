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

"""dimsim ReplayHarness: replay a cmd_vel time series in dimsim and collect odom.

本模块是数据闭环系统 (Unit 4) 的核心: 把 AI 生成的 cmd_vel 序列丢进 dimsim
轻量 2D 仿真后端, 按真实时间戳回放, 同时订阅 dimsim 输出的 odom, 最终返
回一段 (N, 3) 的轨迹和对应时间戳. 下游单元可用此轨迹与"真值"做 ATE /
Frechet / DTW 比对.

主要 API: :func:`replay_cmd_vel_in_dimsim`.

依赖:
    - :mod:`dimos.simulation.dimsim.dimsim_process`: 启动 dimsim 子进程;
    - :mod:`dimos.simulation.dimsim.scene_client`: teleport 机器人到初始位姿;
    - :mod:`dimos.utils.testing.replay`: 复用 ``timed_playback`` 做实时调度;
    - LCM ``/cmd_vel`` topic: dimsim 自己订阅这里来驱动机器人;
    - LCM ``/odom`` topic: dimsim 发布机器人位姿, 我们订阅它拿轨迹.
"""

from __future__ import annotations

from collections.abc import Iterable
import threading
import time

import numpy as np

from dimos.core.global_config import GlobalConfig
from dimos.core.transport import LCMTransport
from dimos.msgs.geometry_msgs.PoseStamped import PoseStamped
from dimos.msgs.geometry_msgs.Twist import Twist
from dimos.simulation.dimsim.dimsim_process import DimSimProcess
from dimos.simulation.dimsim.scene_client import SceneClient
from dimos.utils.logging_config import setup_logger
from dimos.utils.testing.replay import timed_playback

logger = setup_logger()

# dimsim 输出 odom 的 LCM topic 名 (与 ``DimSimConnection`` 中保持一致).
_ODOM_TOPIC = "/odom"
# dimsim 订阅 cmd_vel 的 LCM topic 名 (与 ``DirectCmdVelExplorer`` 中保持一致).
_CMD_VEL_TOPIC = "/cmd_vel"

# 当 cmd_vel 流耗尽后, 再等多久让最后一帧 odom 抵达, 避免轨迹尾巴被截.
_TAIL_DRAIN_SECONDS = 1.0

# 收到第一帧 odom 的等待上限 (用于在采集开始前确认 dimsim 已就绪).
_ODOM_READY_TIMEOUT = 30.0

# teleport 完成到机器人真正稳定在新位姿之间, dimsim 物理需要一点时间收敛
# (服务器侧 kinematic body 更新 -> 浏览器侧渲染 -> 下一帧 odom 抵达).
_POST_TELEPORT_SETTLE_SECONDS = 0.5


def _stop_twist() -> Twist:
    """构造一个零速 Twist, 用于回放结束时给机器人发刹车命令."""
    return Twist()


class _OdomCollector:
    """订阅 /odom 并把每一帧 PoseStamped 累积为轨迹的小工具.

    线程安全: LCM 回调在独立线程触发, ``snapshot`` 由主线程读取, 列表的追加
    与拷贝都用 ``self._lock`` 保护.
    """

    def __init__(self) -> None:
        self._positions: list[tuple[float, float, float]] = []
        self._timestamps: list[float] = []
        self._lock = threading.Lock()
        self._first_event = threading.Event()

    def on_pose(self, pose: PoseStamped) -> None:
        with self._lock:
            self._positions.append((float(pose.x), float(pose.y), float(pose.z)))
            self._timestamps.append(float(pose.ts))
        self._first_event.set()

    def wait_for_first(self, timeout: float) -> bool:
        """阻塞直到收到第一帧 odom, 返回是否成功."""
        return self._first_event.wait(timeout=timeout)

    def snapshot(self) -> tuple[np.ndarray, np.ndarray]:
        """拷出当前累积的轨迹. 返回 (positions (N,3), timestamps (N,))."""
        with self._lock:
            if not self._positions:
                return (
                    np.empty((0, 3), dtype=np.float64),
                    np.empty((0,), dtype=np.float64),
                )
            positions = np.asarray(self._positions, dtype=np.float64)
            timestamps = np.asarray(self._timestamps, dtype=np.float64)
        return positions, timestamps

    def reset(self) -> None:
        """清空已累积的轨迹 (保留"已收到首帧"状态不变).

        用于丢弃 teleport 等异步事件期间的暂态 odom, 让上层只看到 cmd_vel
        生效后的真实轨迹.
        """
        with self._lock:
            self._positions.clear()
            self._timestamps.clear()


def replay_cmd_vel_in_dimsim(
    cmd_vel_iter: Iterable[tuple[float, Twist]],
    *,
    initial_pose: tuple[float, float] = (0.0, 0.0),
    timeout_s: float = 300.0,
    speed: float = 1.0,
) -> tuple[np.ndarray, np.ndarray]:
    """在 dimsim 中回放一段 cmd_vel 序列, 返回机器人轨迹.

    实现概览:

    1. 启动 dimsim 子进程 (:class:`DimSimProcess`) 并建立 :class:`SceneClient`
       连接; 通过 ``set_agent_position`` 把机器人 teleport 到 ``initial_pose``;
    2. 通过 :class:`LCMTransport` 订阅 ``/odom``, 把每帧 :class:`PoseStamped`
       的 ``(x, y, z)`` 累积进 :class:`_OdomCollector`;
    3. 用 :func:`timed_playback` 把 cmd_vel 迭代器变成"按真实时间戳发射"的
       Observable, 每发一帧就 LCM-publish 到 ``/cmd_vel``;
    4. 当 cmd_vel 流自然结束或达到 ``timeout_s`` 后, 发一帧零速 Twist 让机器人
       停下, 再短暂等待最后几帧 odom 落袋;
    5. 最后退订并停止所有资源, 无论中途是否抛异常都保证 dimsim 子进程被回收.

    Args:
        cmd_vel_iter: ``(ts, Twist)`` 元组的可迭代对象, ``ts`` 为秒级时间戳
            (单调递增; 首帧时间戳被 :func:`timed_playback` 作为锚点对齐到
            当前墙钟).
        initial_pose: 机器人在 dimos 世界系下的初始 ``(x, y)``, 米. z 自动取
            :class:`SceneClient` 中的默认值 0.52 (机器人离地高度).
        timeout_s: 回放硬超时, 单位秒. 即使 cmd_vel 还未发完, 超时后也会
            停止并返回截止当前的轨迹.
        speed: 回放速度因子. ``1.0`` 为实时; ``2.0`` 表示按 2 倍速回放 (cmd_vel
            发射间隔减半, 仿真也跑得"更快" -- 但 dimsim 物理仍按真实墙钟推进,
            所以加速回放可能让控制行为偏离原始数据, 仅供测试调试用).

    Returns:
        ``(positions, timestamps)``:

        - ``positions``: 形状 ``(N, 3)`` 的 ``np.ndarray[np.float64]``, 每行
          ``(x, y, z)``, 单位米;
        - ``timestamps``: 形状 ``(N,)`` 的 ``np.ndarray[np.float64]``, 对应
          每条 odom 帧的秒级时间戳 (来自 :class:`PoseStamped` 的 ``ts``).

    Raises:
        TimeoutError: 在 ``_ODOM_READY_TIMEOUT`` 秒内未收到任何 odom 时抛出
            (dimsim 没起来或网络/LCM 配置问题).
    """
    if speed <= 0:
        raise ValueError(f"speed must be positive, got {speed}")
    if timeout_s <= 0:
        raise ValueError(f"timeout_s must be positive, got {timeout_s}")

    global_config = GlobalConfig()
    dimsim_process = DimSimProcess(global_config)
    scene_client: SceneClient | None = None
    odom_transport: LCMTransport[PoseStamped] = LCMTransport(_ODOM_TOPIC, PoseStamped)
    cmd_vel_transport: LCMTransport[Twist] = LCMTransport(_CMD_VEL_TOPIC, Twist)
    unsubscribe_odom = None
    collector = _OdomCollector()
    done_event = threading.Event()
    error_box: list[BaseException] = []
    playback_disposable = None

    try:
        # 1) 启动 dimsim 子进程与 LCM 通道.
        dimsim_process.start()
        odom_transport.start()
        unsubscribe_odom = odom_transport.subscribe(collector.on_pose)
        cmd_vel_transport.start()

        # 2) 建立 SceneClient 并把机器人放到初始位姿.
        # SceneClient.set_agent_position 入参顺序是 (x, y, z) (Three.js 世界系, Y-up);
        # DimSimClient 包装层把 dimos 的 (x, y) 映射成 (y, z, x). 我们直接用
        # SceneClient 做完整的坐标映射, 保持与 e2e_tests/dim_sim_client.py 一致.
        scene_client = SceneClient(host="localhost", port=global_config.dimsim_port)
        # 给 dimsim 一点时间起好 WebSocket 服务; SceneClient 内部连不上会立刻报错.
        scene_client_started = False
        deadline = time.time() + _ODOM_READY_TIMEOUT
        last_err: BaseException | None = None
        while time.time() < deadline and not scene_client_started:
            try:
                scene_client.start()
                scene_client_started = True
            except Exception as exc:
                # 连接失败时短暂重试, 不让单次失败拖死整个流程.
                last_err = exc
                time.sleep(0.5)
        if not scene_client_started:
            raise TimeoutError(
                f"failed to connect to dimsim WebSocket within {_ODOM_READY_TIMEOUT}s: {last_err!r}"
            )
        # 与 DimSimClient.set_agent_position 一致的轴序映射: (x, y) -> (y, z=0.52, x).
        scene_client.set_agent_position(initial_pose[1], 0.52, initial_pose[0])

        # 3) 等第一帧 odom, 证明 dimsim 数据流通了.
        if not collector.wait_for_first(timeout=_ODOM_READY_TIMEOUT):
            raise TimeoutError(
                f"no /odom received within {_ODOM_READY_TIMEOUT}s after dimsim start"
            )
        # teleport 是异步消息, 需要等几帧 odom 到位才能确认机器人真的在初始
        # 位姿; 否则首批 cmd_vel 会在旧位姿上生效, 导致轨迹起点错位.
        # 在 settle 窗口内做一次轮询; 如果机器人长时间没接近 ``initial_pose``,
        # 周期性重发 teleport (dimsim 偶尔会丢首次 teleport, 例如刚启动时
        # WebSocket 还在 handshake).
        settle_deadline = time.time() + _POST_TELEPORT_SETTLE_SECONDS + 2.0
        next_retry_at = time.time() + 0.5
        max_retries = 3
        retries = 0
        while time.time() < settle_deadline:
            positions, _ = collector.snapshot()
            if positions.shape[0] > 0:
                last_xy = positions[-1, :2]
                err = float(np.hypot(last_xy[0] - initial_pose[0], last_xy[1] - initial_pose[1]))
                if err < 0.3:
                    break
            if time.time() >= next_retry_at and retries < max_retries:
                try:
                    scene_client.set_agent_position(initial_pose[1], 0.52, initial_pose[0])
                except Exception as exc:
                    logger.warning(f"teleport retry failed: {exc!r}")
                retries += 1
                next_retry_at = time.time() + 0.5
            time.sleep(0.1)
        # 丢弃 settle 期间累积的 odom, 只保留从 cmd_vel 开始之后的轨迹.
        collector.reset()

        # 4) 用 timed_playback 把 cmd_vel 迭代器变成实时 Observable, 回放到 /cmd_vel.
        def on_next(twist: Twist) -> None:
            try:
                cmd_vel_transport.broadcast(None, twist)
            except BaseException as exc:
                # 任何下游 LCM 错误都吃掉并结束本次回放.
                error_box.append(exc)
                done_event.set()

        def on_error(exc: Exception) -> None:
            error_box.append(exc)
            done_event.set()

        def on_completed() -> None:
            done_event.set()

        # ``timed_playback`` 期望一个 factory (每次订阅调一次得新迭代器);
        # 这里只订阅一次, 直接 ``iter(cmd_vel_iter)`` 即可.
        playback = timed_playback(
            lambda: iter(cmd_vel_iter),
            speed=speed,
            detect_loop=False,
        )
        playback_disposable = playback.subscribe(
            on_next=on_next,
            on_error=on_error,
            on_completed=on_completed,
        )

        # 5) 等 cmd_vel 流自然结束, 或硬超时.
        done_event.wait(timeout=timeout_s)
        if error_box:
            raise error_box[0]

        # 6) 拉手刹: cmd_vel 流结束 (或超时) 后发零速, 避免机器人因最后一帧
        # cmd_vel 还残留惯性继续漂移; 随后等 _TAIL_DRAIN_SECONDS 让最后几帧
        # odom 抵达.
        try:
            cmd_vel_transport.broadcast(None, _stop_twist())
        except Exception as exc:
            logger.warning(f"failed to send stop cmd_vel: {exc!r}")
        time.sleep(_TAIL_DRAIN_SECONDS)

        return collector.snapshot()

    finally:
        # 清理顺序: 先 dispose Observable (停止再调 on_next), 再退订 LCM,
        # 最后 stop transports / scene client / dimsim 子进程.
        if playback_disposable is not None:
            try:
                playback_disposable.dispose()
            except Exception as exc:
                logger.warning(f"failed to dispose playback: {exc!r}")
        if unsubscribe_odom is not None:
            try:
                unsubscribe_odom()
            except Exception as exc:
                logger.warning(f"failed to unsubscribe /odom: {exc!r}")
        try:
            odom_transport.stop()
        except Exception as exc:
            logger.warning(f"failed to stop odom_transport: {exc!r}")
        try:
            cmd_vel_transport.stop()
        except Exception as exc:
            logger.warning(f"failed to stop cmd_vel_transport: {exc!r}")
        if scene_client is not None:
            try:
                scene_client.stop()
            except Exception as exc:
                logger.warning(f"failed to stop SceneClient: {exc!r}")
        try:
            dimsim_process.stop()
        except Exception as exc:
            logger.warning(f"failed to stop dimsim subprocess: {exc!r}")
        # ``DimSimProcess`` 启动了两个 daemon=True 的 stdout/stderr reader
        # 线程, 子进程被 terminate 后流关闭, reader 循环 ``for raw in stream``
        # 会自然结束. 但 OS 关闭管道与 Python GIL 调度之间有微小延迟,
        # pytest 的线程泄漏检测可能在 reader 线程实际退出前抢跑. 这里 join
        # 当前进程内除自身外仍存活的 reader daemon 线程, 给它们一个收尾窗口.
        join_deadline = time.time() + 2.0
        current = threading.current_thread()
        for t in threading.enumerate():
            if t is current or not t.is_alive() or not t.daemon:
                continue
            remaining = join_deadline - time.time()
            if remaining <= 0:
                break
            target = getattr(t, "_target", None)
            target_name = getattr(target, "__name__", "")
            if target_name == "_reader":
                t.join(timeout=min(remaining, 0.5))


__all__ = ["replay_cmd_vel_in_dimsim"]
