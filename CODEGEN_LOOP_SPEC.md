# Codegen ↻ Loop Spec

> 聚焦"**代码生成 → 仿真/回放测试 → 错误反思 → 代码修正**"四步闭环的详细规约。是 `AI_CODEGEN_TRAJECTORY_LOOP.md` 的精炼版，给 harness 写代码时直接照着实现。

## 1. 四步闭环（状态机）

```
              ┌─────────────────────────────────────────────┐
              │                                             │
              │                                             ▼
   ┌─────────────────┐    skill.py    ┌───────────────────────┐
   │ ① 代码生成      │ ───────────▶  │ ② 仿真/回放测试       │
   │   propose()     │                │   run_in_sim()        │
   │   refine()      │                │   → sim_run_<id>.db   │
   └─────────────────┘                └───────────────────────┘
              ▲                                       │
              │                                       │ odom stream
              │                                       ▼
   ┌─────────────────┐                ┌───────────────────────┐
   │ ④ 代码修正       │ ◀────── feedback ────│ ③ 错误反思          │
   │   reflect &     │   JSON           │   compute_metrics()  │
   │   patch         │   prompt         │   summarize_diff()   │
   └─────────────────┘                └───────────────────────┘

   退出条件：metrics.pass()  OR  iter == max_iters
```

每一步都是**纯函数**（输入→输出明确），便于单独测试 + 替换。

---

## 2. 每步契约（实现者只看这一节）

### Step ① — `propose(task, history) → SkillCandidate`

```python
@dataclass
class SkillCandidate:
    source: str          # 一段可 exec 的 Python，必须 export run(robot) async fn
    rationale: str       # LLM 的设计说明（拿来给 reflect 阶段做 anchor）
    allowed_skills: list[str]   # 它声明自己用到的 @skill 名（白名单校验）
```

**输入**：

- `task: str` —— "绕办公楼一圈，从前门开始"
- `start_pose: PoseStamped`
- `history: list[Iteration]` —— 上一轮 (source, metrics, reflection)

**实现要点**：

- system prompt 注入 `UnitreeSkillContainer` 暴露的 `@skill` 签名（用 `agents.annotation` 已有的 introspection）+ `relative_move` 文档（`dimos/robot/unitree/unitree_skill_container.py:210`）。
- 限制 imports 到白名单：`dimos.skills.*`、`asyncio`、`math`。
- 若 `history` 非空，把上一轮 `reflection.diff_summary` 直接拼进 user prompt 头部（"上次失败原因：..."）。

### Step ② — `run_in_sim(scene, start_pose, skill, duration_s) → Path`

```python
def run_in_sim(
    scene: str,                  # e.g. "big_office"，对应 data/.lfs/big_office.ply
    start_pose: PoseStamped,
    skill: SkillCandidate,
    duration_s: float,
) -> Path:                       # 返回 sim_run_<uuid>.db 的绝对路径
    ...
```

**实现要点**：

1. 子进程隔离（**安全**）：用 `subprocess.Popen` 拉一个 `python -m dimos.eval.codegen_loop.sim_worker`，stdin 喂 `skill.source` + meta。worker 内部：
2. `GlobalConfig.update(simulation="mujoco", mujoco_start_pos="x,y", mujoco_room=scene, replay=False)`
3. `ModuleCoordinator.build(unitree_go2_smart)` 启 sim
4. `SqliteStore("sim_run_<uuid>.db").stream("odom").save(...)` 订阅 sim odom
5. `asyncio.create_task( exec_skill(skill.source) )`
6. `asyncio.wait_for(skill_done, timeout=duration_s)`；超时也算正常结束（轨迹截断）。
7. **失败兜底**：sim 崩 / skill 异常 → 写一条 meta 标 `failed=True`，metrics 阶段给最大惩罚。

### Step ③ — `reflect(gt_db, sim_db) → Reflection`

```python
@dataclass
class Reflection:
    metrics: dict[str, float]      # {"ate_m": 1.83, "rpe_m": 0.41, "hit": 0.62}
    verdict: Literal["pass", "fail", "error"]
    diff_summary: str              # 自然语言，喂回 LLM
    first_divergence_ts: float | None
    suggested_focus: list[str]     # ["检查 yaw 方向", "set_goal 第二段偏右"]
    rerun_url: str | None          # 双轨迹 Blueprint 的链接，给人看
```

**关键：`diff_summary` 不能只是 metrics 数字**，否则 LLM 没法定位 bug。要做"**首次发散点定位**"：

```python
def first_divergence(gt, sim, threshold_m=0.5):
    """逐帧对齐 (find_closest)，返回欧氏距 > threshold 的第一个 ts。"""
    for ts, g in gt.iterate_ts():
        s = sim.find_closest(ts, tolerance=0.2)
        if s is None: continue
        if dist((g.x, g.y), (s.x, s.y)) > threshold_m:
            return ts, (g.x, g.y), (s.x, s.y)
    return None
```

然后用 `g.x/y/yaw` 和 `s.x/y/yaw` 算"在第一发散点之前 0.5s，AI 发了什么 cmd_vel"——把这段窗口的 cmd_vel 序列粘到 `diff_summary` 里。LLM 看到"在 t=17.4s，你 cmd_vel=(0.8, 0.0, 1.2)，但 GT 在此处 cmd_vel=(0.5, 0.0, 0.2)"才有能力改代码。

### Step ④ — `refine(prev: SkillCandidate, reflection: Reflection) → SkillCandidate`

跟 ① 同一个 LLM 调用，但 prompt 头加上：

```
你上一版代码评测失败。

## 上一版代码
{prev.source}

## 上一版评测结果
- ATE: {metrics.ate_m:.2f} m  (阈值 1.0)
- Hit Rate: {metrics.hit:.2%}  (阈值 80%)
- 首次发散点：t={first_divergence_ts}s
- 诊断：{diff_summary}
- 重点关注：{', '.join(suggested_focus)}

## 上一版的设计说明（你自己写的）
{prev.rationale}

请基于诊断**最小修改**上一版代码 —— 不要从头重写。如果上一版整体方向错了再重写。
```

"最小修改"这条限制很重要，否则 LLM 喜欢每一轮全部推倒，loop 永远收敛不了。

---

## 3. 一个 ~80 行的端到端 driver

```python
# dimos/eval/codegen_loop/runner.py
from pathlib import Path
import json, uuid
from dimos.memory2.store.sqlite import SqliteStore
from dimos.eval.codegen_loop import propose, refine
from dimos.eval.codegen_loop.sim import run_in_sim
from dimos.eval.codegen_loop.metrics import ate, rpe, hit_rate, first_divergence

PASS_ATE_M = 1.0
PASS_HIT   = 0.80
MAX_ITERS  = 5

def run_loop(gt_db_path: Path) -> dict:
    meta = json.loads(gt_db_path.with_suffix(".meta.json").read_text())
    gt   = SqliteStore(path=str(gt_db_path), must_exist=True)
    gt_odom = gt.stream("odom")

    candidate = None
    history = []

    for it in range(MAX_ITERS):
        # ① 代码生成 / 修正
        if candidate is None:
            candidate = propose(task=meta["task_text"],
                                start_pose=meta["start_pose"],
                                history=history)
        else:
            candidate = refine(prev=candidate, reflection=history[-1]["reflection"])

        # ② 仿真测试
        sim_db_path = run_in_sim(
            scene=meta["scene"],
            start_pose=meta["start_pose"],
            skill=candidate,
            duration_s=meta["duration_s"] * 1.2,
        )
        sim_odom = SqliteStore(path=str(sim_db_path), must_exist=True).stream("odom")

        # ③ 错误反思
        m = {
            "ate_m":  ate(gt_odom, sim_odom),
            "rpe_m":  rpe(gt_odom, sim_odom, delta_s=1.0),
            "hit":    hit_rate(gt_odom, sim_odom, k=10, tolerance_m=1.5),
        }
        div = first_divergence(gt_odom, sim_odom, threshold_m=0.5)
        passed = m["ate_m"] < PASS_ATE_M and m["hit"] > PASS_HIT
        reflection = build_reflection(m, div, passed, candidate, gt_odom, sim_odom)

        history.append({
            "iter": it,
            "candidate": candidate,
            "sim_db": str(sim_db_path),
            "reflection": reflection,
        })

        if passed:
            return {"status": "pass", "iters": it+1, "history": history}

    return {"status": "fail", "iters": MAX_ITERS, "history": history}
```

---

## 4. 三个 anti-pattern（别踩）

| ❌ Anti-pattern | 后果 | ✅ 正确做法 |
|---|---|---|
| 把 `ReplayConnection` 当 sim 用（让 AI 在回放上"跑"） | 永远闭环——AI 命令对 GT odom 无影响 | 用 `MujocoConnection`，回放只做感知/规划只读评测 |
| reflection 只返回 metrics 数字 | LLM 拿不到错误定位，每轮像盲调 | 必须有 `first_divergence_ts` + 该窗口的 cmd_vel/pose 对比 |
| 每轮 `propose()` 不传 history | LLM 不知道上一轮做了什么，反复重走老路 | `refine()` 强制 prompt 里"最小修改" + 贴出上一版完整代码 |

---

## 5. 验收基线（W2 demo 时跑这条）

```bash
# 1. 拉评测数据集
python -c "from dimos.utils.data import get_data; get_data('go2_bigoffice.db')"

# 2. 跑闭环 5 轮
python -m dimos.eval.codegen_loop.runner \
       data/.lfs/extracted/go2_bigoffice.db \
       --max-iters 5

# 3. 看可视化
dimos-viewer ./eval/runs/last/comparison.rrd
# (GT 红线 + sim 绿线 + 误差色带，应在 5 轮内重合到 <1m)
```

成功标准（DoD）：连续 3 个不同 `go2_*.db` 数据集，5 轮迭代内 pass 率 ≥ 60%。
