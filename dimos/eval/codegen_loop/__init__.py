"""
AI 代码生成 + 轨迹闭环验证 (B-紧 路线)。

四步闭环：propose → run_in_sim → reflect → refine。
详见 ../../../AI_CODEGEN_TRAJECTORY_LOOP.md 和 ../../../CODEGEN_LOOP_SPEC.md。

公开 API:
    from dimos.eval.codegen_loop import (
        load_traj, ate, rpe, hit_rate, first_divergence, evaluate, TrajReport,
        uniform_by_arclength, rdp_simplify,
    )
"""

from dimos.eval.codegen_loop.metrics import (
    Divergence,
    TrajReport,
    align,
    ate,
    evaluate,
    first_divergence,
    hit_rate,
    load_traj,
    rpe,
)
from dimos.eval.codegen_loop.waypoints import (
    cumulative_arclength,
    rdp_simplify,
    uniform_by_arclength,
)

__all__ = [
    "Divergence",
    "TrajReport",
    "align",
    "ate",
    "cumulative_arclength",
    "evaluate",
    "first_divergence",
    "hit_rate",
    "load_traj",
    "rdp_simplify",
    "rpe",
    "uniform_by_arclength",
]
