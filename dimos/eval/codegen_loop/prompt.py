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

"""LLM prompt 模板 - B-紧 路线 (路径复现)。

给 LLM 喂：(起点, 自动抽出来的 waypoint 列表) → 让它写一段
`async def run(robot)` 用 relative_move 序列依次走过这些 waypoint。

调用 @skill 白名单见 ALLOWED_SKILLS — 与 UnitreeSkillContainer 真实暴露的接口对齐
(见 dimos/robot/unitree/unitree_skill_container.py)。
"""

from __future__ import annotations

from dataclasses import dataclass
import math

import numpy as np
from numpy.typing import NDArray

from dimos.eval.codegen_loop.metrics import Divergence

ALLOWED_SKILLS = """
你只能调用以下 @skill (定义在 dimos.robot.unitree.unitree_skill_container.UnitreeSkillContainer)：

  await robot.relative_move(forward: float = 0.0,
                            left: float = 0.0,
                            degrees: float = 0.0) -> str
      在当前机器人朝向坐标系下移动 (forward, left) 米，再旋转 degrees 度。
      forward 正向前、left 正向左、degrees 正向逆时针。

  await robot.wait(seconds: float) -> str
      原地等待 N 秒。

  await robot.current_time() -> str
      返回当前时间字符串。
"""


SYSTEM_PROMPT = """你是一个 dimos Unitree GO2 机器人导航代码生成器。

任务：写一段 Python 异步函数 `async def run(robot):`，让机器人依次走过
一组给定的 waypoint (世界坐标 x, y)。

{allowed_skills}

【硬性约束】
1. 必须 export `async def run(robot):`，不要写其他顶层语句。
2. 只能 import: asyncio, math。
3. 禁止任何文件 / 网络 / subprocess / eval / exec 调用。
4. 机器人启动时朝向已知 (start_yaw_deg)。waypoint 是世界坐标，
   你必须用三角函数把它换成 relative_move 的本体系增量。
5. waypoint 间允许有 ±{tol_m:.1f}m 容差。能用一次 relative_move 直达
   就别拆成多次。每次 relative_move 之后机器人朝向会变，记得维护 current_yaw。
"""


REFINE_PROMPT = """你上一版代码评测失败。

## 上一版代码
```python
{prev_source}
```

## 上一版评测结果
- ATE: {ate_m:.2f} m   (阈值 {ate_thr:.1f})
- Hit Rate: {hit:.1%}   (阈值 {hit_thr:.0%})
{div_block}

请基于诊断**最小修改**上一版代码 —— 不要从头重写。
如果上一版整体方向就错了，再重写。
"""


@dataclass
class ProposeInput:
    task_text: str
    start_xy: tuple[float, float]
    start_yaw_deg: float
    waypoints: NDArray[np.float64]  # (K, 2): only x, y
    tolerance_m: float = 1.5


def build_propose_prompt(inp: ProposeInput) -> tuple[str, str]:
    """返回 (system_prompt, user_prompt)."""
    wp_lines = []
    for i, (x, y) in enumerate(inp.waypoints):
        wp_lines.append(f"  [{i:2d}]  x = {x:+7.2f} m,  y = {y:+7.2f} m")
    user = f"""
任务: {inp.task_text}

起点:  x = {inp.start_xy[0]:+.2f} m,  y = {inp.start_xy[1]:+.2f} m,  yaw = {inp.start_yaw_deg:+.1f}°

需要依次走过的 waypoint (容差 {inp.tolerance_m:.1f} m)：
{chr(10).join(wp_lines)}

请直接给出 `async def run(robot):` 的完整源码，不要任何解释。
""".strip()
    system = SYSTEM_PROMPT.format(
        allowed_skills=ALLOWED_SKILLS.strip(),
        tol_m=inp.tolerance_m,
    )
    return system, user


def build_refine_prompt(
    prev_source: str,
    ate_m: float,
    hit: float,
    ate_thr: float = 1.0,
    hit_thr: float = 0.8,
    first_div: Divergence | None = None,
) -> str:
    if not math.isfinite(ate_m):
        div_block = (
            "- 无可对齐轨迹：先检查仿真进程是否成功、sim DB 是否包含 odom，"
            "以及 GT/sim 是否有可比较的时间范围；不要先归因于 waypoint。"
        )
    elif first_div is None:
        div_block = (
            "- 未观察到首次发散点（轨迹整体匹配但 hit_rate 不达标，可能是 waypoint 遗漏或顺序错）"
        )
    else:
        gx, gy = first_div.gt_xy
        sx, sy = first_div.sim_xy
        div_block = (
            f"- 首次发散点 t={first_div.ts:.1f}s:\n"
            f"    GT  在 ({gx:+.2f}, {gy:+.2f}) yaw={first_div.gt_yaw_deg:+.1f}°\n"
            f"    sim 在 ({sx:+.2f}, {sy:+.2f}) yaw={first_div.sim_yaw_deg:+.1f}°\n"
            f"    距离 {first_div.distance_m:.2f} m\n"
            f"    => 检查上一版代码里这个时间点附近的 relative_move 参数"
        )
    return REFINE_PROMPT.format(
        prev_source=prev_source,
        ate_m=ate_m,
        hit=hit,
        ate_thr=ate_thr,
        hit_thr=hit_thr,
        div_block=div_block,
    )


def _body_frame_move(
    current_xy: tuple[float, float],
    current_yaw_rad: float,
    target_xy: tuple[float, float],
) -> tuple[float, float, float]:
    """Convert a world-frame target into ``relative_move`` arguments."""
    dx = target_xy[0] - current_xy[0]
    dy = target_xy[1] - current_xy[1]
    body_forward = math.cos(current_yaw_rad) * dx + math.sin(current_yaw_rad) * dy
    body_left = -math.sin(current_yaw_rad) * dx + math.cos(current_yaw_rad) * dy
    if math.hypot(dx, dy) < 1e-9:
        return body_forward, body_left, 0.0
    target_yaw = math.atan2(dy, dx)
    delta_yaw = (target_yaw - current_yaw_rad + math.pi) % (2 * math.pi) - math.pi
    return body_forward, body_left, delta_yaw


def hardcoded_baseline(inp: ProposeInput) -> str:
    """
    不调 LLM，直接根据 waypoint 几何关系生成 relative_move 序列。
    用于在 LLM 接通前验证 propose → run_in_sim → reflect 链路。
    """
    lines = [
        "import asyncio, math",
        "",
        "async def run(robot):",
    ]
    if len(inp.waypoints) == 0:
        lines.append("    pass")
    cur_yaw = math.radians(inp.start_yaw_deg)
    cur_x, cur_y = inp.start_xy
    for tx, ty in inp.waypoints:
        body_forward, body_left, dyaw = _body_frame_move(
            (cur_x, cur_y),
            cur_yaw,
            (float(tx), float(ty)),
        )
        deg = math.degrees(dyaw)
        lines.append(
            f"    await robot.relative_move(forward={body_forward:.2f}, "
            f"left={body_left:.2f}, degrees={deg:+.1f})"
        )
        cur_x, cur_y = float(tx), float(ty)
        cur_yaw += dyaw
    return "\n".join(lines)
