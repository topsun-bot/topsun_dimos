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
"""cmd_vel 录制 Sink.

数据闭环系统 Unit 1: 把订阅到的 cmd_vel Twist 流持续落盘到
``LegacyPickleStore``, 与 ``GO2Connection.record()`` 录制 lidar/odom/video
的方式保持一致; 为后续的 AI 真值回放与轨迹对比提供输入.
"""

from __future__ import annotations

from pathlib import Path
import time
from typing import TYPE_CHECKING

from reactivex import operators as ops

from dimos.core.core import rpc
from dimos.core.module import Module, ModuleConfig
from dimos.core.stream import In
from dimos.memory.timeseries.legacy import LegacyPickleStore
from dimos.msgs.geometry_msgs.Twist import Twist
from dimos.utils.logging_config import setup_logger

if TYPE_CHECKING:
    from reactivex.observable import Observable


logger = setup_logger()


class CmdVelRecorderConfig(ModuleConfig):
    """``CmdVelRecorder`` 的配置项.

    Attributes:
        recording_name: 录制目录名或绝对路径.
            ``LegacyPickleStore`` 会把 ``cmd_vel`` 子目录拼到该路径之后;
            相对路径会落到 ``get_data_dir`` 解析出的数据目录里,
            绝对路径则直接使用 (单测用 ``tmp_path`` 时推荐传绝对路径).
    """

    recording_name: str


class CmdVelRecorder(Module):
    """订阅 ``cmd_vel`` Twist 流并落盘的 Module.

    用法:
        把本 Module 加入 blueprint, 与发布 ``cmd_vel`` 的 Module (例如
        ``KeyboardTeleop`` 或上层导航控制器) autoconnect 即可. ``start()``
        阶段会创建 ``LegacyPickleStore(<recording_name>/cmd_vel)`` 并把流
        持续写入 pickle 目录; ``stop()`` 时由 ``register_disposable`` 自动
        取消订阅.

    写入格式遵循 ``LegacyPickleStore`` 的现有约定: 每一帧 Twist 写一个
    ``NNN.pickle`` 文件, 内容是 ``(timestamp, twist)`` 元组.
    """

    config: CmdVelRecorderConfig
    cmd_vel: In[Twist]

    _store: LegacyPickleStore[Twist] | None = None

    @rpc
    def start(self) -> None:
        """启动录制.

        逻辑:
            1. 调用基类 ``start()`` 走完正常的模块启动流程.
            2. 用 ``recording_name`` 构造 ``LegacyPickleStore``, 目标目录为
               ``<recording_name>/cmd_vel``, 与 GO2Connection 中
               ``lidar``/``odom``/``video`` 的命名约定一致.
            3. 订阅 ``cmd_vel.pure_observable()``: 每帧若没有 ``.ts``
               属性, 就用 ``time.time()`` 现场打戳, 然后交给
               ``LegacyPickleStore.consume_stream`` 落盘.
            4. 把 ``consume_stream`` 返回的 ``DisposableBase`` 通过
               ``register_disposable`` 注册到模块的资源管理器,
               ``stop()`` 时会自动取消订阅.
        """
        super().start()

        # 用 ``Path`` 拼接, 避免 trailing slash / Windows 分隔符之类的边角问题.
        # ``LegacyPickleStore`` 内部对绝对路径会直接落到该目录, 相对路径会走
        # ``get_data_dir`` 解析; 这里只关心末尾追加 ``cmd_vel`` 子目录.
        store_path = Path(self.config.recording_name) / "cmd_vel"
        store: LegacyPickleStore[Twist] = LegacyPickleStore(str(store_path))
        self._store = store

        # cmd_vel 上游一般是遥控器或导航控制器, 发出的 Twist 不带 ``.ts``.
        # LegacyPickleStore.consume_stream 依赖 ``data.ts`` 做时间戳,
        # 所以在订阅链路里补一道墙钟时间戳.
        timestamped: Observable[Twist] = self.cmd_vel.pure_observable().pipe(ops.map(_ensure_ts))

        disposable = store.consume_stream(timestamped)
        self.register_disposable(disposable)

        logger.info(f"CmdVelRecorder started: recording_name={self.config.recording_name!r}")


def _ensure_ts(twist: Twist) -> Twist:
    """如果 ``twist`` 没有 ``.ts``, 用墙钟时间补上.

    ``LegacyPickleStore.consume_stream`` 依赖 ``data.ts`` 做时间戳; 上游的
    cmd_vel 发布者一般不打戳, 因此在录制侧统一补一道时间戳, 避免
    ``AttributeError`` 把 RxPY 订阅链断掉.

    Args:
        twist: 上游来的 cmd_vel 帧.

    Returns:
        同一个 ``Twist`` 对象 (原地补 ``.ts`` 后返回), 便于下游沿用.
    """
    if not hasattr(twist, "ts"):
        # 直接挂到实例上即可, ``Twist`` 没有 ``__slots__``.
        twist.ts = time.time()  # type: ignore[attr-defined]
    return twist


__all__ = ["CmdVelRecorder", "CmdVelRecorderConfig"]
