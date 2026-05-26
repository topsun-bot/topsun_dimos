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
# 整段中文文档/注释/CLI help 文本里的全角标点是项目协作规范要求，关掉
# ruff 的 ambiguous-unicode 警告族。
# ruff: noqa: RUF001, RUF002
"""``dimos closed-loop`` CLI——数据闭环编排器（Unit 8 集成单元）。

把 Unit 1-7 串成端到端流程：

    真值 session 目录  →  SkillSequenceGenerator（LLM）
                       →  dimsim ReplayHarness（合成 cmd_vel 回放）
                       →  ClosedLoopCritic（ATE/Fréchet/DTW 门控）
                       →  收敛 / 失败 → 生成 HTML 报告 → 退出码

退出码语义：

    0   pass   —— critic 判定真值与生成轨迹一致
    2   abort  —— critic 主动放弃（达到 max_iters 或 schema 失败）
    1   error  —— 未捕获的内部异常 / 无效参数

本模块**不重新实现** Unit 1-7 的逻辑：所有 import 都按 plan 文件里的接口签名
直接写。单元测试会 monkeypatch 这些外部依赖，因此即便其它 unit 还没 merge，
本 PR 的 CI 也能独立通过。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import logging
import math
from pathlib import Path
from typing import TYPE_CHECKING, Any

import typer

if TYPE_CHECKING:
    from collections.abc import Iterable

    import numpy as np

    from dimos.msgs.geometry_msgs.Twist import Twist


# 注意：模块级 logger，不在 import 时配置 handler——交给 dimos 主 CLI 统一配置。
logger = logging.getLogger(__name__)


# 仅与 CLI 解析相关的常量；阈值默认值同时出现在 plan E 节，禁止改动。
_DEFAULT_MAX_ITERS = 5
_DEFAULT_TOLERANCE_FRECHET = 0.5
_DEFAULT_TOLERANCE_ATE = 0.3
_DEFAULT_SIMULATOR = "dimsim"
_DEFAULT_ROBOT = "unitree-go2-agentic"

# 退出码
EXIT_PASS = 0
EXIT_ERROR = 1
EXIT_ABORT = 2


# ---------------------------------------------------------------------------
# 参数对象
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ClosedLoopArgs:
    """``run_closed_loop`` 的参数对象。

    用 dataclass 而不是直接传 Typer 的 ``ctx`` 是为了让单测能脱离 Typer 单独构造，
    也便于将来程序化调用。
    """

    truth: Path
    task: str
    max_iters: int = _DEFAULT_MAX_ITERS
    tolerance_frechet: float = _DEFAULT_TOLERANCE_FRECHET
    tolerance_ate: float = _DEFAULT_TOLERANCE_ATE
    simulator: str = _DEFAULT_SIMULATOR
    robot: str = _DEFAULT_ROBOT
    out: Path | None = None


# ---------------------------------------------------------------------------
# 输出目录
# ---------------------------------------------------------------------------


def default_out_dir() -> Path:
    """返回默认报告输出目录。

    按 plan E 节：``~/.local/share/dimos/closed_loop_reports/<timestamp>/``。
    时间戳格式 ``YYYYMMDD_HHMMSS``，与 ``RunSession`` 命名风格保持一致。
    """
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    return Path.home() / ".local" / "share" / "dimos" / "closed_loop_reports" / ts


# ---------------------------------------------------------------------------
# 真值轨迹加载与摘要（辅助函数，CLI 内部用）
# ---------------------------------------------------------------------------


def _load_truth_traj(session: Any) -> np.ndarray:
    """从 ``RunSession`` 读取 odom 流，提取 ``(x, y, z)`` 轨迹张量。

    Args:
        session: ``dimos.core.run_session.RunSession`` 实例。要求暴露
            ``sensors_db_path()`` 方法返回 ``Path``。

    Returns:
        形状 ``(N, 3)`` 的 numpy 数组，单位米。空 session 返回 ``(0, 3)``
        的空数组。

    Notes:
        - 表名假设为 ``odom``——与 Unit 2 写 ``RunSession`` 时的约定一致。
        - 这里把 SqliteStore import 推迟到函数体内，避免 ``dimos --help`` 启动
          时被拖入。
        - 传给 SqliteStore 的路径必须是绝对路径；否则会触发 LFS 自动下载逻辑，
          把 session-local db 误判成数据集名称。
    """
    import numpy as np

    from dimos.memory.timeseries.sqlite import SqliteStore

    db_path = Path(session.sensors_db_path()).resolve()
    store: SqliteStore[Any] = SqliteStore(db_path, table="odom")

    points: list[tuple[float, float, float]] = []
    # 用公开 API ``iterate_items()``（基类提供，返回 (ts, data)）；
    # 不直接调 ``_iter_items``，免得未来 backend 重构破坏我们。
    for _ts, odom in store.iterate_items():
        # Odometry 暴露 x/y/z 属性；如果上游写入的是别的对象，尝试用 .pose.position
        try:
            points.append((float(odom.x), float(odom.y), float(odom.z)))
        except AttributeError:
            pos = odom.pose.position
            points.append((float(pos.x), float(pos.y), float(pos.z)))

    if not points:
        return np.empty((0, 3), dtype=float)
    return np.asarray(points, dtype=float)


def _summarize_traj(traj: np.ndarray) -> dict[str, Any]:
    """生成真值轨迹摘要，喂给 SkillSequenceGenerator 作为上下文。

    Args:
        traj: 形状 ``(N, 3)`` 的轨迹张量。

    Returns:
        含 ``start`` / ``end`` / ``path_length`` / ``duration_s`` / ``n_points``
        的字典；``duration_s`` 这里用点数近似，真实时间戳需 caller 单独传。
        空轨迹返回全零摘要。
    """
    import numpy as np

    if traj.size == 0 or traj.shape[0] < 1:
        return {
            "start": [0.0, 0.0, 0.0],
            "end": [0.0, 0.0, 0.0],
            "path_length": 0.0,
            "duration_s": 0.0,
            "n_points": 0,
        }

    start = traj[0].tolist()
    end = traj[-1].tolist()
    if traj.shape[0] >= 2:
        segs = np.linalg.norm(np.diff(traj, axis=0), axis=1)
        path_length = float(segs.sum())
    else:
        path_length = 0.0

    return {
        "start": [float(v) for v in start],
        "end": [float(v) for v in end],
        "path_length": path_length,
        # duration_s 在仅有位置序列时无法精确推断；交给 caller / 报告里补充
        "duration_s": float(traj.shape[0]),
        "n_points": int(traj.shape[0]),
    }


# ---------------------------------------------------------------------------
# Skill 序列 → cmd_vel 时间序列（简化合成器）
# ---------------------------------------------------------------------------


def _seq_to_cmd_vel(seq: Any) -> Iterable[tuple[float, Twist]]:
    """把 ``SkillSequence`` 展开成 ``(timestamp, Twist)`` 时间序列。

    **这是一个简化合成器**——本 CLI 不真去 dimsim 里调用 ``@skill`` 方法，
    而是按 skill 语义直接合成等效 cmd_vel。理由：

      * 闭环 CLI 是 "AI 编程评估" 工具，关心 skill 序列 → 轨迹的可重现映射，
        不关心 skill 在仿真器里被调度的细节。
      * dimsim ReplayHarness（Unit 4）输入就是 ``(ts, Twist)`` 时间序列；
        我们只要把 skill 序列翻译成这个序列即可。

    支持的 skill：

      * ``GoToW(x, y, yaw_deg=0)``：相对当前位姿移动 ``(x, y)``，
        以 ``expect_duration_s`` 时长匀速推算 ``vx``/``vy``。
      * ``rotate(degrees)``：以 ``expect_duration_s`` 时长匀速旋转 ``wz``。
      * 其他 skill：跳过并 warning。

    Args:
        seq: ``dimos.agents.skill_sequence_schema.SkillSequence`` 实例。
            要求暴露 ``.steps`` 列表，每个 step 有 ``.skill`` (str)、
            ``.args`` (dict)、``.expect_duration_s`` (float | None)。

    Returns:
        ``(absolute_ts_seconds, Twist)`` 的可迭代序列；时间戳从 0 起算，
        每个 step 以 ``cmd_freq_hz=20`` 分片发出 Twist。
    """
    # 延迟 import：Twist 依赖 dimos_lcm，重；测试里也会被 mock 掉。
    from dimos.msgs.geometry_msgs.Twist import Twist

    cmd_freq_hz = 20.0
    dt = 1.0 / cmd_freq_hz

    cmd_seq: list[tuple[float, Twist]] = []
    t = 0.0

    for step in getattr(seq, "steps", []):
        skill_name = (getattr(step, "skill", "") or "").strip()
        args = getattr(step, "args", {}) or {}
        duration = float(getattr(step, "expect_duration_s", None) or 1.0)
        if duration <= 0:
            duration = 1.0

        if skill_name == "GoToW":
            dx = float(args.get("x", 0.0))
            dy = float(args.get("y", 0.0))
            vx = dx / duration
            vy = dy / duration
            wz = 0.0
        elif skill_name == "rotate":
            degrees = float(args.get("degrees", 0.0))
            wz = math.radians(degrees) / duration
            vx = 0.0
            vy = 0.0
        else:
            logger.warning("skipping unsupported skill in cmd_vel synthesis: %s", skill_name)
            t += duration  # 保持时间线连续
            continue

        n_steps = max(1, round(duration * cmd_freq_hz))
        for _ in range(n_steps):
            cmd_seq.append((t, Twist(linear=[vx, vy, 0.0], angular=[0.0, 0.0, wz])))
            t += dt

    return cmd_seq


# ---------------------------------------------------------------------------
# 主循环
# ---------------------------------------------------------------------------


def run_closed_loop(args: ClosedLoopArgs) -> int:
    """运行闭环主循环并写报告，返回退出码。

    把 Unit 1-7 串成完整流程，每一轮调用 generator → harness → critic，直到 critic
    判 pass / abort 或达到 ``max_iters``。

    Args:
        args: 闭环参数对象。

    Returns:
        进程退出码（见模块顶部 EXIT_* 常量）。
    """
    # 所有外部 unit 都在函数体内 import，方便单测 monkeypatch。
    # 每个 import 后面挂的 mypy 抑制标注覆盖两种情况：单元尚未 land；以及
    # 已 land 但还没有 py.typed 标记。单元 land 并打上类型信息后这些
    # 抑制标注会变成 unused-ignore，届时清理。
    from dimos.agents.closed_loop_critic import (  # type: ignore[import-untyped]
        ClosedLoopCritic,
        Tolerances,
    )
    from dimos.agents.skill_sequence_generator import (  # type: ignore[import-untyped]
        SkillSequenceGenerator,
    )
    from dimos.core.run_session import RunSession  # type: ignore[import-untyped]
    from dimos.simulation.dimsim.replay_harness import (  # type: ignore[import-untyped]
        replay_cmd_vel_in_dimsim,
    )
    from dimos.utils.trajectory_report import (  # type: ignore[import-untyped]
        IterationRecord,
        generate_html_report,
        write_comparison_rrd,
    )

    print(f"[closed-loop] 加载真值 session: {args.truth}")
    truth_session = RunSession.load(args.truth)
    truth_traj = _load_truth_traj(truth_session)
    truth_summary = _summarize_traj(truth_traj)
    print(
        f"[closed-loop] 真值轨迹: {truth_summary['n_points']} 点, "
        f"长度 {truth_summary['path_length']:.2f} m"
    )

    generator = SkillSequenceGenerator(model=args.robot, skill_registry={})
    critic = ClosedLoopCritic(
        tolerances=Tolerances(ate_m=args.tolerance_ate, frechet_m=args.tolerance_frechet),
        max_iters=args.max_iters,
    )

    iterations: list[Any] = []
    feedback_history: list[dict[str, Any]] = []
    final_action = "abort"

    for attempt in range(args.max_iters):
        print(f"\n[closed-loop] === 第 {attempt + 1}/{args.max_iters} 轮 ===")

        # 1) generator
        try:
            seq = generator.generate(args.task, truth_summary, feedback_history)
        except Exception as exc:
            logger.exception("SkillSequenceGenerator 失败")
            print(f"[closed-loop] 生成 skill 序列失败: {exc}", flush=True)
            final_action = "abort"
            break

        # 2) skill → cmd_vel
        cmd_vel_iter = _seq_to_cmd_vel(seq)

        # 3) dimsim 回放
        try:
            gen_traj, _ts = replay_cmd_vel_in_dimsim(cmd_vel_iter)
        except Exception as exc:
            logger.exception("dimsim ReplayHarness 失败")
            print(f"[closed-loop] 仿真回放失败: {exc}", flush=True)
            final_action = "abort"
            break

        # 4) critic
        decision = critic.decide(truth_traj, gen_traj, attempt)

        # 5) 记录本轮
        try:
            seq_json = seq.model_dump_json()
        except AttributeError:
            seq_json = str(seq)

        iterations.append(
            IterationRecord(
                attempt_idx=attempt,
                generated_traj=gen_traj,
                metrics=decision.metrics,
                feedback=decision.feedback,
                skill_sequence_json=seq_json,
            )
        )
        feedback_history.append(
            {
                "attempt": attempt,
                "feedback": decision.feedback,
                "metrics": decision.metrics,
            }
        )

        print(f"[closed-loop] 本轮 critic: action={decision.action}")
        print(f"[closed-loop] 指标: {decision.metrics}")

        if decision.action != "retry":
            final_action = decision.action
            break
    else:
        # for-else: 循环正常结束（没 break），说明 retry 用尽
        final_action = "abort"

    # 6) 生成 HTML 报告
    out_dir = Path(args.out) if args.out else default_out_dir()
    out_dir.mkdir(parents=True, exist_ok=True)
    print(f"\n[closed-loop] 写报告到: {out_dir}")
    report_path = generate_html_report(
        out_dir,
        task=args.task,
        truth_traj=truth_traj,
        iterations=iterations,
        final_action=final_action,
    )
    print(f"[closed-loop] Report: {report_path}")

    # 7) 可选 comparison rrd——失败不致命
    if iterations:
        try:
            last_gen = iterations[-1].generated_traj
            write_comparison_rrd(out_dir / "comparison.rrd", truth_traj, last_gen)
        except Exception as exc:  # 报告已写成功，rrd 失败仅 warn
            logger.warning("write_comparison_rrd 失败（非致命）: %s", exc)

    print(f"[closed-loop] 最终状态: {final_action}")
    return EXIT_PASS if final_action == "pass" else EXIT_ABORT


# ---------------------------------------------------------------------------
# Typer 子命令注册
# ---------------------------------------------------------------------------


def register(main: typer.Typer) -> None:
    """把 ``closed-loop`` 子命令挂到主 typer app 上。

    在 ``dimos.robot.cli.dimos`` 模块加载时调用一次。
    """

    @main.command("closed-loop")
    def closed_loop_cmd(
        truth: Path = typer.Option(
            ...,
            "--truth",
            help="真值 RunSession 目录路径（必填）。",
        ),
        task: str = typer.Option(
            ...,
            "--task",
            help='要让 AI 完成的自然语言任务描述，例如 "围绕办公楼跑一圈"。',
        ),
        max_iters: int = typer.Option(
            _DEFAULT_MAX_ITERS,
            "--max-iters",
            help="闭环最大轮数；超出仍未 pass 则 abort，退出码 2。",
        ),
        tolerance_frechet: float = typer.Option(
            _DEFAULT_TOLERANCE_FRECHET,
            "--tolerance-frechet",
            help="Fréchet 距离容差（米），高于此值 critic 会让 LLM retry。",
        ),
        tolerance_ate: float = typer.Option(
            _DEFAULT_TOLERANCE_ATE,
            "--tolerance-ate",
            help="ATE（绝对轨迹误差）容差（米），高于此值 critic 会让 LLM retry。",
        ),
        simulator: str = typer.Option(
            _DEFAULT_SIMULATOR,
            "--simulator",
            help="仿真后端名称；当前仅支持 dimsim。",
        ),
        robot: str = typer.Option(
            _DEFAULT_ROBOT,
            "--robot",
            help="目标机器人 blueprint 名称，会传给 SkillSequenceGenerator 选择 prompt。",
        ),
        out: Path | None = typer.Option(
            None,
            "--out",
            help=(
                "报告输出目录；不填则默认 ~/.local/share/dimos/closed_loop_reports/<timestamp>/。"
            ),
        ),
    ) -> None:
        """运行 AI 闭环：真值 → AI 生成 → 仿真回放 → 自纠错。

        端到端流程将串联 RunSession 加载、SkillSequenceGenerator (LLM)、dimsim
        ReplayHarness、ClosedLoopCritic 与 trajectory_report，最终在 ``--out``
        指定目录下生成一份 HTML 报告。

        退出码：

          * 0 —— critic 判 pass
          * 1 —— 参数错误或未捕获异常
          * 2 —— critic 主动 abort（含达到 max_iters）

        前置条件：必须先用 ``CmdVelRecorder`` + ``RunSession`` 录制一段真值数据，
        参考 README "数据闭环系统" 章节。
        """
        args_obj = ClosedLoopArgs(
            truth=truth,
            task=task,
            max_iters=max_iters,
            tolerance_frechet=tolerance_frechet,
            tolerance_ate=tolerance_ate,
            simulator=simulator,
            robot=robot,
            out=out,
        )

        # 参数校验：truth 必须是已存在目录
        if not args_obj.truth.exists() or not args_obj.truth.is_dir():
            print(
                f"[closed-loop] 错误：--truth 必须是已存在的目录，got {args_obj.truth}",
                flush=True,
            )
            raise typer.Exit(EXIT_ERROR)

        if args_obj.max_iters < 1:
            print(f"[closed-loop] 错误：--max-iters 必须 ≥ 1，got {args_obj.max_iters}")
            raise typer.Exit(EXIT_ERROR)

        try:
            code = run_closed_loop(args_obj)
        except typer.Exit:
            raise
        except Exception as exc:
            logger.exception("closed-loop 主循环未捕获异常")
            print(f"[closed-loop] 内部错误: {exc}", flush=True)
            raise typer.Exit(EXIT_ERROR)

        raise typer.Exit(code)


__all__ = [
    "EXIT_ABORT",
    "EXIT_ERROR",
    "EXIT_PASS",
    "ClosedLoopArgs",
    "default_out_dir",
    "register",
    "run_closed_loop",
]
