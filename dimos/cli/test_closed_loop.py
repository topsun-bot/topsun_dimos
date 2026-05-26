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
# 同 closed_loop.py：整段中文，关 ruff ambiguous-unicode 警告。
# ruff: noqa: RUF002
"""Unit 8 单测：``dimos closed-loop`` CLI 主循环 + 参数解析。

所有外部 unit（1-7）都 mock，与各 unit 是否 land 完全解耦。
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
import sys
import types
from typing import Any

import numpy as np
import pytest
from typer.testing import CliRunner

from dimos.cli import closed_loop as cl

# ---------------------------------------------------------------------------
# 公共测试夹具：在 sys.modules 里塞假的外部 unit
# ---------------------------------------------------------------------------


@dataclass
class _FakeDecision:
    """模拟 CriticDecision，避免真 import Unit 6。"""

    action: str  # "pass" | "retry" | "abort"
    feedback: str
    metrics: dict[str, float]


class _FakeRunSession:
    """模拟 RunSession，提供 ``sensors_db_path()``。"""

    def __init__(self, dir_path: Path) -> None:
        self._dir = dir_path

    def sensors_db_path(self) -> Path:
        # 单测里我们 monkeypatch _load_truth_traj，这里返回什么都行
        return self._dir / "sensors.db"


# 模块级数据类——必须能被 pickle 序列化（SqliteStore 用 pickle 存 blob）。
class _PoseLike:
    """模拟 Pose：暴露 .position.x/y/z。"""

    def __init__(self, x: float, y: float, z: float) -> None:
        self.position = types.SimpleNamespace(x=x, y=y, z=z)


class _OdomLike:
    """模拟没有 .x/.y/.z 直属性的 Odometry，必须走 pose.position 兜底。"""

    def __init__(self, x: float, y: float, z: float, ts: float) -> None:
        self.pose = _PoseLike(x, y, z)
        self.ts = ts


class _FakeSequence:
    """模拟 SkillSequence，提供 ``.steps`` 和 ``model_dump_json``。"""

    def __init__(self, steps: list[Any] | None = None, task: str = "") -> None:
        self.steps = steps or []
        self.task = task

    def model_dump_json(self) -> str:
        return f'{{"task":"{self.task}","steps":[]}}'


class _FakeStep:
    """模拟 SkillSequence.steps[i]。"""

    def __init__(
        self,
        skill: str,
        args: dict[str, Any] | None = None,
        expect_duration_s: float | None = None,
    ) -> None:
        self.skill = skill
        self.args = args or {}
        self.expect_duration_s = expect_duration_s


class _FakeGenerator:
    """记录调用次数 + 可配置返回 / 抛错。"""

    def __init__(self, model: str, skill_registry: dict[str, Any]) -> None:
        self.model = model
        self.skill_registry = skill_registry
        self.calls: list[tuple[str, dict[str, Any], list[dict[str, Any]]]] = []
        self._next: _FakeSequence | Exception = _FakeSequence()

    def set_next(self, value: _FakeSequence | Exception) -> None:
        self._next = value

    def generate(
        self, task: str, summary: dict[str, Any], feedback: list[dict[str, Any]]
    ) -> _FakeSequence:
        self.calls.append((task, summary, list(feedback)))
        if isinstance(self._next, Exception):
            raise self._next
        return self._next


class _FakeTolerances:
    def __init__(self, ate_m: float, frechet_m: float) -> None:
        self.ate_m = ate_m
        self.frechet_m = frechet_m


class _FakeCritic:
    def __init__(
        self,
        tolerances: _FakeTolerances,
        max_iters: int,
    ) -> None:
        self.tolerances = tolerances
        self.max_iters = max_iters
        self.decisions: list[_FakeDecision] = []
        self.call_count = 0

    def queue(self, *decisions: _FakeDecision) -> None:
        self.decisions = list(decisions)

    def decide(
        self,
        truth: np.ndarray,
        gen: np.ndarray,
        attempt: int,
    ) -> _FakeDecision:
        self.call_count += 1
        if not self.decisions:
            return _FakeDecision("retry", "no-decision-queued", {"ate": 1.0, "frechet": 1.0})
        return self.decisions.pop(0)


@dataclass
class _FakeIterationRecord:
    attempt_idx: int
    generated_traj: np.ndarray
    metrics: dict[str, float]
    feedback: str
    skill_sequence_json: str


@pytest.fixture
def mock_units(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> dict[str, Any]:
    """把 Unit 1-7 全部 mock 成假模块，挂到 sys.modules。

    返回一个字典，测试可以访问到 generator/critic 实例 以及 ``report_calls``
    列表来做断言。
    """

    # --- Unit 5: agents.skill_sequence_generator ---
    generator_instances: list[_FakeGenerator] = []

    def _generator_factory(model: str, skill_registry: dict[str, Any]) -> _FakeGenerator:
        gen = _FakeGenerator(model, skill_registry)
        generator_instances.append(gen)
        return gen

    fake_gen_mod = types.ModuleType("dimos.agents.skill_sequence_generator")
    fake_gen_mod.SkillSequenceGenerator = _generator_factory  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "dimos.agents.skill_sequence_generator", fake_gen_mod)

    # --- Unit 6: agents.closed_loop_critic ---
    critic_instances: list[_FakeCritic] = []

    def _critic_factory(tolerances: _FakeTolerances, max_iters: int) -> _FakeCritic:
        critic = _FakeCritic(tolerances, max_iters)
        critic_instances.append(critic)
        return critic

    fake_critic_mod = types.ModuleType("dimos.agents.closed_loop_critic")
    fake_critic_mod.ClosedLoopCritic = _critic_factory  # type: ignore[attr-defined]
    fake_critic_mod.Tolerances = _FakeTolerances  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "dimos.agents.closed_loop_critic", fake_critic_mod)

    # --- Unit 2: core.run_session ---
    def _load_session(path: Path) -> _FakeRunSession:
        return _FakeRunSession(Path(path))

    fake_session_mod = types.ModuleType("dimos.core.run_session")
    fake_session_mod.RunSession = types.SimpleNamespace(load=_load_session)  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "dimos.core.run_session", fake_session_mod)

    # --- Unit 4: simulation.dimsim.replay_harness ---
    replay_calls: list[Any] = []

    def _replay_cmd_vel(
        cmd_vel_iter: Iterable[tuple[float, Any]],
        **kwargs: Any,
    ) -> tuple[np.ndarray, np.ndarray]:
        materialised = list(cmd_vel_iter)
        replay_calls.append(materialised)
        # 返回一段假轨迹——和真值差距由 critic 决定，这里只是占位
        traj = np.array(
            [[0.0, 0.0, 0.0], [0.1, 0.0, 0.0], [0.2, 0.0, 0.0]],
            dtype=float,
        )
        ts = np.array([0.0, 0.05, 0.1], dtype=float)
        return traj, ts

    fake_harness_mod = types.ModuleType("dimos.simulation.dimsim.replay_harness")
    fake_harness_mod.replay_cmd_vel_in_dimsim = _replay_cmd_vel  # type: ignore[attr-defined]
    # 确保父包存在
    for name in (
        "dimos.simulation",
        "dimos.simulation.dimsim",
    ):
        if name not in sys.modules:
            monkeypatch.setitem(sys.modules, name, types.ModuleType(name))
    monkeypatch.setitem(sys.modules, "dimos.simulation.dimsim.replay_harness", fake_harness_mod)

    # --- Unit 7: utils.trajectory_report ---
    report_calls: list[dict[str, Any]] = []

    def _generate_html(
        out_dir: Path,
        *,
        task: str,
        truth_traj: np.ndarray,
        iterations: list[Any],
        final_action: str,
    ) -> Path:
        report_calls.append(
            {
                "out_dir": Path(out_dir),
                "task": task,
                "truth_traj": truth_traj,
                "iterations": list(iterations),
                "final_action": final_action,
            }
        )
        path = Path(out_dir) / "report.html"
        path.write_text("<html><body>fake report</body></html>", encoding="utf-8")
        return path

    rrd_calls: list[Path] = []

    def _write_rrd(path: Path, truth: np.ndarray, gen: np.ndarray) -> None:
        rrd_calls.append(Path(path))
        Path(path).write_bytes(b"fake-rrd")

    fake_report_mod = types.ModuleType("dimos.utils.trajectory_report")
    fake_report_mod.generate_html_report = _generate_html  # type: ignore[attr-defined]
    fake_report_mod.IterationRecord = _FakeIterationRecord  # type: ignore[attr-defined]
    fake_report_mod.write_comparison_rrd = _write_rrd  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "dimos.utils.trajectory_report", fake_report_mod)

    # --- _load_truth_traj 桩：避免真去读 SqliteStore ---
    def _fake_load_truth(_session: Any) -> np.ndarray:
        return np.array(
            [[0.0, 0.0, 0.0], [1.0, 0.0, 0.0], [2.0, 0.0, 0.0]],
            dtype=float,
        )

    monkeypatch.setattr(cl, "_load_truth_traj", _fake_load_truth)

    return {
        "generator_instances": generator_instances,
        "critic_instances": critic_instances,
        "replay_calls": replay_calls,
        "report_calls": report_calls,
        "rrd_calls": rrd_calls,
    }


# ---------------------------------------------------------------------------
# 主循环测试
# ---------------------------------------------------------------------------


def _make_args(truth_dir: Path, out_dir: Path, **overrides: Any) -> cl.ClosedLoopArgs:
    base = {
        "truth": truth_dir,
        "task": "向前走 2 米",
        "max_iters": 3,
        "tolerance_frechet": 0.5,
        "tolerance_ate": 0.3,
        "simulator": "dimsim",
        "robot": "unitree-go2-agentic",
        "out": out_dir,
    }
    base.update(overrides)
    return cl.ClosedLoopArgs(**base)  # type: ignore[arg-type]


def test_pass_on_first_iter(
    mock_units: dict[str, Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """显式让 critic 在第一轮返回 pass。"""
    truth_dir = tmp_path / "truth"
    truth_dir.mkdir()
    out_dir = tmp_path / "out"

    # 重写 _critic_factory：返回的 critic 默认 decide 都给 pass
    original_factory = sys.modules["dimos.agents.closed_loop_critic"].ClosedLoopCritic

    def _passing_factory(tolerances: Any, max_iters: int) -> _FakeCritic:
        c: _FakeCritic = original_factory(tolerances, max_iters)
        c.queue(
            _FakeDecision("pass", "ok", {"ate": 0.05, "frechet": 0.1}),
        )
        return c

    monkeypatch.setattr(
        sys.modules["dimos.agents.closed_loop_critic"],
        "ClosedLoopCritic",
        _passing_factory,
    )

    args = _make_args(truth_dir, out_dir, max_iters=5)
    code = cl.run_closed_loop(args)

    assert code == cl.EXIT_PASS
    report = mock_units["report_calls"][0]
    assert report["final_action"] == "pass"
    assert len(report["iterations"]) == 1
    assert (out_dir / "report.html").exists()


def test_three_retries_then_abort(
    mock_units: dict[str, Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """3 次 retry 后达 max_iters → abort，退出码 2，iterations 长度 == max_iters。"""
    truth_dir = tmp_path / "truth"
    truth_dir.mkdir()
    out_dir = tmp_path / "out"

    original_factory = sys.modules["dimos.agents.closed_loop_critic"].ClosedLoopCritic

    def _retry_factory(tolerances: Any, max_iters: int) -> _FakeCritic:
        c: _FakeCritic = original_factory(tolerances, max_iters)
        c.queue(
            *(
                _FakeDecision("retry", f"too far {i}", {"ate": 1.0, "frechet": 1.5})
                for i in range(max_iters)
            )
        )
        return c

    monkeypatch.setattr(
        sys.modules["dimos.agents.closed_loop_critic"],
        "ClosedLoopCritic",
        _retry_factory,
    )

    args = _make_args(truth_dir, out_dir, max_iters=4)
    code = cl.run_closed_loop(args)

    assert code == cl.EXIT_ABORT
    report = mock_units["report_calls"][0]
    assert report["final_action"] == "abort"
    assert len(report["iterations"]) == 4
    # 所有轮的 feedback 都拼入了 history → 第 N 轮 generator 应当看到 N-1 条 feedback
    gen = mock_units["generator_instances"][0]
    assert len(gen.calls) == 4
    assert len(gen.calls[-1][2]) == 3  # 第 4 轮 generator 看到 3 条历史


def test_generator_exception_aborts(
    mock_units: dict[str, Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """generator 抛 schema 异常 → abort + 报告仍生成 + 非零退出码。"""
    truth_dir = tmp_path / "truth"
    truth_dir.mkdir()
    out_dir = tmp_path / "out"

    original_gen_factory = sys.modules[
        "dimos.agents.skill_sequence_generator"
    ].SkillSequenceGenerator

    def _broken_factory(model: str, skill_registry: dict[str, Any]) -> _FakeGenerator:
        g: _FakeGenerator = original_gen_factory(model, skill_registry)
        g.set_next(ValueError("schema validation failed"))
        return g

    monkeypatch.setattr(
        sys.modules["dimos.agents.skill_sequence_generator"],
        "SkillSequenceGenerator",
        _broken_factory,
    )

    args = _make_args(truth_dir, out_dir)
    code = cl.run_closed_loop(args)
    assert code == cl.EXIT_ABORT
    # 报告应该仍然生成（即便 iterations 为空）
    report = mock_units["report_calls"][0]
    assert report["final_action"] == "abort"
    assert report["iterations"] == []


def test_replay_harness_failure_aborts(
    mock_units: dict[str, Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """dimsim 抛错也走 abort 分支，不能未捕获崩溃。"""
    truth_dir = tmp_path / "truth"
    truth_dir.mkdir()
    out_dir = tmp_path / "out"

    def _bad_replay(_cmd_iter: Any, **_kwargs: Any) -> Any:
        raise RuntimeError("dimsim crash")

    monkeypatch.setattr(
        sys.modules["dimos.simulation.dimsim.replay_harness"],
        "replay_cmd_vel_in_dimsim",
        _bad_replay,
    )

    args = _make_args(truth_dir, out_dir)
    code = cl.run_closed_loop(args)
    assert code == cl.EXIT_ABORT
    report = mock_units["report_calls"][0]
    assert report["final_action"] == "abort"


def test_default_out_dir_used_when_none(
    mock_units: dict[str, Any],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``out=None`` → 使用 default_out_dir()，目录是 closed_loop_reports/<ts>。"""
    truth_dir = tmp_path / "truth"
    truth_dir.mkdir()
    fake_default = tmp_path / "default_out"

    monkeypatch.setattr(cl, "default_out_dir", lambda: fake_default)

    args = _make_args(truth_dir, out_dir=None)  # type: ignore[arg-type]
    code = cl.run_closed_loop(args)
    assert code in (cl.EXIT_PASS, cl.EXIT_ABORT)
    assert fake_default.exists()
    assert (fake_default / "report.html").exists()


def test_comparison_rrd_failure_is_non_fatal(
    mock_units: dict[str, Any],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """rrd 写失败不能让 CLI 整体 crash——只是 warn。"""
    truth_dir = tmp_path / "truth"
    truth_dir.mkdir()
    out_dir = tmp_path / "out"

    def _bad_rrd(_path: Path, _truth: Any, _gen: Any) -> None:
        raise OSError("rrd write failed")

    monkeypatch.setattr(
        sys.modules["dimos.utils.trajectory_report"],
        "write_comparison_rrd",
        _bad_rrd,
    )

    args = _make_args(truth_dir, out_dir)
    code = cl.run_closed_loop(args)
    assert code == cl.EXIT_ABORT
    # 报告仍生成
    assert (out_dir / "report.html").exists()


# ---------------------------------------------------------------------------
# 辅助函数测试
# ---------------------------------------------------------------------------


def test_summarize_traj_normal() -> None:
    traj = np.array(
        [[0.0, 0.0, 0.0], [3.0, 4.0, 0.0], [3.0, 4.0, 0.0]],
        dtype=float,
    )
    s = cl._summarize_traj(traj)
    assert s["n_points"] == 3
    assert s["start"] == [0.0, 0.0, 0.0]
    assert s["end"] == [3.0, 4.0, 0.0]
    assert s["path_length"] == pytest.approx(5.0)


def test_summarize_traj_empty() -> None:
    s = cl._summarize_traj(np.empty((0, 3)))
    assert s["n_points"] == 0
    assert s["path_length"] == 0.0


def test_seq_to_cmd_vel_gotow() -> None:
    """GoToW(2, 0) 在 1s 内 → 应当合成约 20 个 vx=2 的 Twist。"""
    seq = _FakeSequence(
        steps=[
            _FakeStep("GoToW", {"x": 2.0, "y": 0.0}, expect_duration_s=1.0),
        ]
    )
    cmds = list(cl._seq_to_cmd_vel(seq))
    assert len(cmds) == 20
    first_ts, first_twist = cmds[0]
    assert first_ts == pytest.approx(0.0)
    assert first_twist.linear.x == pytest.approx(2.0)
    assert first_twist.linear.y == pytest.approx(0.0)
    # 时间戳单调
    times = [t for t, _ in cmds]
    assert all(times[i] <= times[i + 1] for i in range(len(times) - 1))


def test_seq_to_cmd_vel_rotate() -> None:
    """rotate(180 度) 在 1s 内 → wz ≈ π。"""
    import math

    seq = _FakeSequence(
        steps=[
            _FakeStep("rotate", {"degrees": 180.0}, expect_duration_s=1.0),
        ]
    )
    cmds = list(cl._seq_to_cmd_vel(seq))
    assert len(cmds) == 20
    _, twist = cmds[0]
    assert twist.angular.z == pytest.approx(math.pi, rel=1e-3)
    assert twist.linear.x == pytest.approx(0.0)


def test_seq_to_cmd_vel_unknown_skill_skipped() -> None:
    """未识别 skill：跳过且不崩；时间线仍前推。"""
    seq = _FakeSequence(
        steps=[
            _FakeStep("dance", {}, expect_duration_s=1.0),
            _FakeStep("GoToW", {"x": 1.0, "y": 0.0}, expect_duration_s=1.0),
        ]
    )
    cmds = list(cl._seq_to_cmd_vel(seq))
    # dance 跳过；GoToW 产出 20 帧
    assert len(cmds) == 20
    # 但第一个 cmd 的 ts 应该 >= 1.0（被 dance 占掉的时长）
    assert cmds[0][0] >= 1.0


def test_seq_to_cmd_vel_default_duration() -> None:
    """expect_duration_s 缺失/None/负 → 退回 1.0s。"""
    seq = _FakeSequence(
        steps=[
            _FakeStep("GoToW", {"x": 1.0, "y": 0.0}, expect_duration_s=None),
            _FakeStep("GoToW", {"x": 1.0, "y": 0.0}, expect_duration_s=-0.5),
        ]
    )
    cmds = list(cl._seq_to_cmd_vel(seq))
    # 两步各 20 帧
    assert len(cmds) == 40


def test_load_truth_traj_reads_real_sqlite(tmp_path: Path) -> None:
    """``_load_truth_traj`` 真去读一个临时 SQLite db，验证集成路径而非纯 mock。"""
    import pickle

    from dimos.memory.timeseries.sqlite import SqliteStore

    db_path = tmp_path / "sensors.db"

    # 写 3 条假 odom 数据；用 SimpleNamespace 模拟 Odometry，暴露 x/y/z
    fake_records = [
        types.SimpleNamespace(x=0.0, y=0.0, z=0.0, ts=1.0),
        types.SimpleNamespace(x=1.0, y=2.0, z=0.0, ts=1.1),
        types.SimpleNamespace(x=3.0, y=4.0, z=0.0, ts=1.2),
    ]
    store: SqliteStore[Any] = SqliteStore(db_path, table="odom")
    for rec in fake_records:
        store.save(rec)

    # 用真实路径包成 session
    session = _FakeRunSession(tmp_path)
    traj = cl._load_truth_traj(session)
    assert traj.shape == (3, 3)
    assert traj[2].tolist() == [3.0, 4.0, 0.0]

    # pickle round-trip 校验
    assert pickle.dumps(traj) is not None


def test_load_truth_traj_empty_db_returns_zero_rows(tmp_path: Path) -> None:
    """空 DB → 返回 ``(0, 3)`` 数组而不是崩。"""
    from dimos.memory.timeseries.sqlite import SqliteStore

    db_path = tmp_path / "sensors.db"
    # 创建空表
    SqliteStore(db_path, table="odom")._get_conn()

    session = _FakeRunSession(tmp_path)
    traj = cl._load_truth_traj(session)
    assert traj.shape == (0, 3)


def test_load_truth_traj_pose_fallback(tmp_path: Path) -> None:
    """odom 没有 .x/.y/.z 直接属性 → 走 .pose.position 兜底。"""
    from dimos.memory.timeseries.sqlite import SqliteStore

    db_path = tmp_path / "sensors.db"

    store: SqliteStore[Any] = SqliteStore(db_path, table="odom")
    # 用模块级类（_PoseLike / _OdomLike）以便 pickle 能序列化
    store.save(_OdomLike(1.0, 2.0, 3.0, 1.0))
    store.save(_OdomLike(4.0, 5.0, 6.0, 2.0))

    session = _FakeRunSession(tmp_path)
    traj = cl._load_truth_traj(session)
    assert traj.shape == (2, 3)
    assert traj[1].tolist() == [4.0, 5.0, 6.0]


def test_default_out_dir_format() -> None:
    """默认输出目录路径形如 .../closed_loop_reports/<ts>。"""
    p = cl.default_out_dir()
    # 不一定存在，但应该满足结构
    assert p.parent.name == "closed_loop_reports"
    assert p.name and "_" in p.name  # YYYYMMDD_HHMMSS


# ---------------------------------------------------------------------------
# CLI 层（typer）测试
# ---------------------------------------------------------------------------


@pytest.fixture
def cli_runner() -> CliRunner:
    return CliRunner()


@pytest.fixture
def typer_app(mock_units: dict[str, Any]) -> Any:
    """构造一个临时的 typer app，仅注册 closed-loop，避免拖入主 CLI。

    注意：typer 在只有一个命令时会进入「单命令模式」，把 ``closed-loop`` 当成
    位置参数解析。我们加一个 no-op ``_noop`` 子命令强制保持「多命令模式」，
    这样测试调用 ``["closed-loop", ...]`` 才会被正确路由到 closed-loop 子命令。
    """
    import typer

    app = typer.Typer()
    cl.register(app)

    @app.command("_noop")
    def _noop() -> None:
        """no-op，仅用于强制 typer 进入多命令模式（不要在生产 CLI 注册）。"""

    return app


def test_cli_missing_required_args(cli_runner: CliRunner, typer_app: Any) -> None:
    """缺 --truth / --task → 非零退出码，stderr 含 Missing。"""
    result = cli_runner.invoke(typer_app, [])
    assert result.exit_code != 0


def test_cli_invalid_truth_dir(cli_runner: CliRunner, typer_app: Any, tmp_path: Path) -> None:
    """--truth 不是目录 → 退出码 1。"""
    bogus = tmp_path / "does_not_exist"
    result = cli_runner.invoke(
        typer_app,
        ["closed-loop", "--truth", str(bogus), "--task", "noop"],
    )
    assert result.exit_code == cl.EXIT_ERROR


def test_cli_invalid_max_iters(cli_runner: CliRunner, typer_app: Any, tmp_path: Path) -> None:
    truth_dir = tmp_path / "t"
    truth_dir.mkdir()
    result = cli_runner.invoke(
        typer_app,
        [
            "closed-loop",
            "--truth",
            str(truth_dir),
            "--task",
            "noop",
            "--max-iters",
            "0",
        ],
    )
    assert result.exit_code == cl.EXIT_ERROR


def test_cli_defaults_propagate(
    cli_runner: CliRunner,
    typer_app: Any,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """不传可选参数 → ClosedLoopArgs 使用 plan 定义的默认值。"""
    truth_dir = tmp_path / "t"
    truth_dir.mkdir()
    out_dir = tmp_path / "out"

    captured: dict[str, cl.ClosedLoopArgs] = {}

    def _spy(a: cl.ClosedLoopArgs) -> int:
        captured["args"] = a
        return cl.EXIT_PASS

    monkeypatch.setattr(cl, "run_closed_loop", _spy)
    result = cli_runner.invoke(
        typer_app,
        [
            "closed-loop",
            "--truth",
            str(truth_dir),
            "--task",
            "向前走 2 米",
            "--out",
            str(out_dir),
        ],
    )
    assert result.exit_code == cl.EXIT_PASS, result.output
    a = captured["args"]
    assert a.task == "向前走 2 米"
    assert a.max_iters == 5
    assert a.tolerance_frechet == 0.5
    assert a.tolerance_ate == 0.3
    assert a.simulator == "dimsim"
    assert a.robot == "unitree-go2-agentic"
    assert a.out == out_dir
    assert a.truth == truth_dir


def test_cli_overrides_all_options(
    cli_runner: CliRunner,
    typer_app: Any,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    truth_dir = tmp_path / "t"
    truth_dir.mkdir()
    out_dir = tmp_path / "out"

    captured: dict[str, cl.ClosedLoopArgs] = {}

    def _spy(a: cl.ClosedLoopArgs) -> int:
        captured["args"] = a
        return cl.EXIT_PASS

    monkeypatch.setattr(cl, "run_closed_loop", _spy)
    result = cli_runner.invoke(
        typer_app,
        [
            "closed-loop",
            "--truth",
            str(truth_dir),
            "--task",
            "spin",
            "--max-iters",
            "7",
            "--tolerance-frechet",
            "0.42",
            "--tolerance-ate",
            "0.21",
            "--simulator",
            "dimsim",
            "--robot",
            "unitree-g1-agentic",
            "--out",
            str(out_dir),
        ],
    )
    assert result.exit_code == cl.EXIT_PASS, result.output
    a = captured["args"]
    assert a.max_iters == 7
    assert a.tolerance_frechet == pytest.approx(0.42)
    assert a.tolerance_ate == pytest.approx(0.21)
    assert a.robot == "unitree-g1-agentic"


def test_cli_internal_exception_returns_error(
    cli_runner: CliRunner,
    typer_app: Any,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """run_closed_loop 抛未捕获异常 → 退出码 1。"""
    truth_dir = tmp_path / "t"
    truth_dir.mkdir()

    def _explode(_a: cl.ClosedLoopArgs) -> int:
        raise RuntimeError("unexpected")

    monkeypatch.setattr(cl, "run_closed_loop", _explode)
    result = cli_runner.invoke(
        typer_app,
        ["closed-loop", "--truth", str(truth_dir), "--task", "noop"],
    )
    assert result.exit_code == cl.EXIT_ERROR


def test_cli_pass_propagates_exit_zero(
    cli_runner: CliRunner,
    typer_app: Any,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    truth_dir = tmp_path / "t"
    truth_dir.mkdir()
    monkeypatch.setattr(cl, "run_closed_loop", lambda _a: cl.EXIT_PASS)
    result = cli_runner.invoke(
        typer_app,
        ["closed-loop", "--truth", str(truth_dir), "--task", "noop"],
    )
    assert result.exit_code == cl.EXIT_PASS


def test_cli_abort_propagates_exit_two(
    cli_runner: CliRunner,
    typer_app: Any,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    truth_dir = tmp_path / "t"
    truth_dir.mkdir()
    monkeypatch.setattr(cl, "run_closed_loop", lambda _a: cl.EXIT_ABORT)
    result = cli_runner.invoke(
        typer_app,
        ["closed-loop", "--truth", str(truth_dir), "--task", "noop"],
    )
    assert result.exit_code == cl.EXIT_ABORT
