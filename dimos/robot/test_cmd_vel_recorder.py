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
"""``CmdVelRecorder`` 的单元测试.

这里只验证写侧逻辑:
    - ``start()`` 后, 发到 ``cmd_vel`` In 流的 Twist 会按
      ``LegacyPickleStore`` 现有约定逐帧落盘.
    - 没有 ``.ts`` 的 Twist 会被自动打戳, 落盘文件能 round-trip 回读出
      与原始相同 (数值意义上) 的 Twist.
    - ``stop()`` 会取消订阅, 后续 publish 不再落盘.

不联机器人, 不依赖 LCM 多播: 我们直接给 ``cmd_vel`` 输入挂一个内存版
``Transport``, 在测试里手动 ``broadcast`` 出 Twist.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from pathlib import Path
import pickle
import time
from typing import Any

import pytest

from dimos.core.stream import Stream, Transport
from dimos.memory.timeseries.legacy import LegacyPickleStore
from dimos.msgs.geometry_msgs.Twist import Twist
from dimos.msgs.geometry_msgs.Vector3 import Vector3
from dimos.robot.cmd_vel_recorder import CmdVelRecorder, _ensure_ts


class InMemoryTransport(Transport[Twist]):
    """单测专用的最小内存 Transport.

    省去启动 LCM 多播的成本: ``broadcast`` 直接同步调用所有订阅者.
    """

    def __init__(self) -> None:
        self._subscribers: list[Callable[[Twist], Any]] = []
        self._started: bool = False

    def start(self) -> None:
        self._started = True

    def stop(self) -> None:
        self._started = False
        self._subscribers.clear()

    def broadcast(self, _selfstream: Any, value: Twist) -> None:
        for cb in list(self._subscribers):
            cb(value)

    def subscribe(
        self,
        callback: Callable[[Twist], Any],
        selfstream: Stream[Twist] | None = None,
    ) -> Callable[[], None]:
        self._subscribers.append(callback)

        def _unsubscribe() -> None:
            try:
                self._subscribers.remove(callback)
            except ValueError:
                pass

        return _unsubscribe


@pytest.fixture
def recorder(tmp_path: Path) -> Iterator[CmdVelRecorder]:
    """构造一个 ``CmdVelRecorder``, ``recording_name`` 用 ``tmp_path``.

    传绝对路径让 ``LegacyPickleStore`` 直接写到临时目录, 不污染
    ``~/.local/share/dimos/data`` 之类的用户数据目录.
    """
    rec = CmdVelRecorder(recording_name=str(tmp_path / "session"))
    # 给 In 流装一个内存 Transport, 避免依赖 LCM.
    rec.cmd_vel.transport = InMemoryTransport()
    try:
        yield rec
    finally:
        rec._close_module()


def _make_twist(vx: float, wz: float) -> Twist:
    """构造一个不带 ``.ts`` 的 Twist, 模拟上游遥控器的发布行为."""
    return Twist(linear=Vector3(vx, 0.0, 0.0), angular=Vector3(0.0, 0.0, wz))


def test_records_three_frames_to_pickle_dir(recorder: CmdVelRecorder, tmp_path: Path) -> None:
    """发 3 帧 Twist, 落盘目录里应该恰好有 3 个 pickle 文件且能 round-trip."""
    recorder.start()

    frames = [
        _make_twist(0.5, 0.0),
        _make_twist(0.3, 0.1),
        _make_twist(0.0, -0.2),
    ]
    for tw in frames:
        recorder.cmd_vel.transport.broadcast(None, tw)

    # 找回落盘目录. LegacyPickleStore 写到 ``<recording_name>/cmd_vel``.
    cmd_vel_dir = tmp_path / "session" / "cmd_vel"
    assert cmd_vel_dir.is_dir(), f"录制目录未创建: {cmd_vel_dir}"

    pickle_files = sorted(cmd_vel_dir.glob("*.pickle"))
    assert len(pickle_files) == len(frames), (
        f"期望 {len(frames)} 个 pickle 文件, 实际 {len(pickle_files)} 个: "
        f"{[p.name for p in pickle_files]}"
    )

    # Round-trip: 每个文件加载出来应该是 (ts, twist) 元组,
    # 且 twist 的 linear/angular 与原始一致.
    for tw_in, pkl_path in zip(frames, pickle_files, strict=True):
        with open(pkl_path, "rb") as f:
            ts_out, tw_out = pickle.load(f)
        assert isinstance(ts_out, float), f"时间戳类型应为 float, 实际 {type(ts_out)}"
        assert ts_out > 0.0, f"时间戳应为正数墙钟时间, 实际 {ts_out}"
        assert isinstance(tw_out, Twist), f"载荷应为 Twist, 实际 {type(tw_out)}"
        assert tw_out == tw_in, f"round-trip 不一致: in={tw_in!r} out={tw_out!r}"


def test_pickle_dir_compatible_with_legacy_store_read(
    recorder: CmdVelRecorder, tmp_path: Path
) -> None:
    """落盘格式应与 ``LegacyPickleStore`` 读侧 API 完全兼容."""
    recorder.start()
    recorder.cmd_vel.transport.broadcast(None, _make_twist(1.0, 0.0))
    recorder.cmd_vel.transport.broadcast(None, _make_twist(2.0, 0.0))

    # 用同样的 store 路径读回去.
    store: LegacyPickleStore[Twist] = LegacyPickleStore(str(tmp_path / "session" / "cmd_vel"))
    assert len(store) == 2
    items = list(store)
    assert all(isinstance(it, Twist) for it in items)
    assert items[0].linear.x == pytest.approx(1.0)
    assert items[1].linear.x == pytest.approx(2.0)


def test_existing_ts_is_preserved(recorder: CmdVelRecorder, tmp_path: Path) -> None:
    """如果上游已经给 Twist 打了 ``.ts``, 不要覆盖."""
    recorder.start()

    tw = _make_twist(0.7, 0.0)
    fixed_ts = 1_234_567_890.5
    tw.ts = fixed_ts  # type: ignore[attr-defined]
    recorder.cmd_vel.transport.broadcast(None, tw)

    pickle_files = sorted((tmp_path / "session" / "cmd_vel").glob("*.pickle"))
    assert len(pickle_files) == 1
    with open(pickle_files[0], "rb") as f:
        ts_out, _ = pickle.load(f)
    assert ts_out == pytest.approx(fixed_ts), (
        f"上游已打戳的 ts 应被保留, 期望 {fixed_ts}, 实际 {ts_out}"
    )


def test_stop_unsubscribes(recorder: CmdVelRecorder, tmp_path: Path) -> None:
    """``stop()`` 后再 publish 不应继续落盘."""
    recorder.start()
    recorder.cmd_vel.transport.broadcast(None, _make_twist(0.1, 0.0))

    cmd_vel_dir = tmp_path / "session" / "cmd_vel"
    assert len(list(cmd_vel_dir.glob("*.pickle"))) == 1

    recorder.stop()

    # 停了之后再 broadcast 不应该再产生新文件.
    recorder.cmd_vel.transport.broadcast(None, _make_twist(0.2, 0.0))
    assert len(list(cmd_vel_dir.glob("*.pickle"))) == 1, "stop() 后订阅没有取消, 仍然在写文件"


def test_ensure_ts_adds_timestamp_when_missing() -> None:
    """``_ensure_ts`` 应该给没 ``.ts`` 的 Twist 补一个墙钟时间戳."""
    tw = _make_twist(0.0, 0.0)
    assert not hasattr(tw, "ts")

    before = time.time()
    out = _ensure_ts(tw)
    after = time.time()

    assert out is tw, "_ensure_ts 应该原地修改并返回同一对象"
    assert hasattr(tw, "ts")
    assert before <= tw.ts <= after  # type: ignore[attr-defined]


def test_ensure_ts_keeps_existing_timestamp() -> None:
    """``_ensure_ts`` 不应覆盖已经存在的 ``.ts``."""
    tw = _make_twist(0.0, 0.0)
    tw.ts = 42.0  # type: ignore[attr-defined]

    out = _ensure_ts(tw)

    assert out is tw
    assert tw.ts == 42.0  # type: ignore[attr-defined]
