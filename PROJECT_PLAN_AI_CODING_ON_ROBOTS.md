# 项目方案：让 AI 生成的机器人代码达到"可重构"级别

**仓库**：https://github.com/topsun-bot/topsun_dimos.git
**起草日期**：2026-05-25
**关联文档**：[AI_CODEGEN_TRAJECTORY_LOOP.md](./AI_CODEGEN_TRAJECTORY_LOOP.md)（架构 + 数据闭环详解）、[CODEGEN_LOOP_SPEC.md](./CODEGEN_LOOP_SPEC.md)（四步循环实现规约）

---

## TL;DR

要让 AI 写的机器人代码能直接上线运行（而不只是 demo），缺的不是更强的模型，而是一套**带物理约束的自动验收闭环**。dimos 已经具备闭环所需的全部底座（Rerun 可视化、`.db` 时序录制、Mujoco 仿真、`@skill` 抽象），我们只需要做两件新东西：(1) 把"遥控录制的真值"和"AI 仿真跑出的轨迹"做 ATE/RPE 对比；(2) 把首次发散点的细粒度诊断喂回 LLM 做最小修改迭代。两周内可跑通端到端 demo，目标是连续 3 个办公楼场景，5 轮内 pass 率 ≥ 60%。

---

## 1. 我们要解决什么问题

> **北极星**：让 AI 生成的机器人代码质量达到能直接重构 / 上线的级别。

今天 LLM 写机器人控制代码的主要失败模式不是语法错，而是**语义错**——参数符号搞反、坐标系搞错、转弯顺序错。这些错在静态 review 里看不出来，必须让代码跑起来、产生实际轨迹，才能暴露。

行业里成熟的对照是自动驾驶的"**数据闭环**"：每条路测产生的数据回灌成回归测试集，新版算法必须在所有历史场景上跑出可比的指标才能发版。我们要的是同一套思路，只是把"算法版本"换成"AI 生成的某一版代码"。

## 2. 我们已有什么（不用造）

经过对 `topsun_dimos` 仓库的代码梳理，闭环所需的基础设施 **已经全部存在**：

| 能力 | 实现位置 | 状态 |
|---|---|---|
| 长驻可视化 + 录制 .rrd | `dimos/visualization/rerun/bridge.py`（`RerunBridgeModule`，含 `save_to_disk`） | ✅ 可用 |
| 真值时序数据 (.db) | `dimos/memory2/store/sqlite.py` + `dimos/utils/testing/replay.py` | ✅ 可用 |
| 已录制的办公楼数据集 | `data/.lfs/go2_{bigoffice,china_office,hongkong_office,short,slamabuse{1,2}}.db` | ✅ 6 条现成 |
| 遥控（Quest / 手机 / 键盘） | `dimos/teleop/`、`dimos/robot/unitree/keyboard_teleop.py` | ✅ 可用 |
| 数据回灌（让下游模块以为在跑真车） | `dimos/robot/unitree/go2/connection.py::ReplayConnection` + `make_connection` 自动切换 | ✅ 可用 |
| Mujoco 仿真（同一 `Go2ConnectionProtocol`） | `dimos/robot/unitree/mujoco_connection.py` | ✅ 可用 |
| AI 可调的技能集 | `dimos/robot/unitree/unitree_skill_container.py`（`@skill relative_move` 等） | ✅ 可用 |
| LLM agent 框架 | `dimos/agents/demo_agent.py`、MCP server | ✅ 可用 |

**这是一个特别有利的起点**——我们不需要选型任何新框架。

## 3. 一个关键的架构判断（别走错路）

最直觉的做法是"让 AI 在回放数据里运行并对比轨迹"。**这条路在物理上不闭环**，必须澄清：

- `ReplayConnection.move(twist)` 是 `return True` 的空操作。AI 发的速度指令**不会改变回放里的 odom**。
- 拿同一条回放 odom 去对比"AI 跑出来的轨迹"，对比的是同一份数据，metrics 永远 0。

**正确做法**：

- **真值通道**：遥控 → 真车 → 录到 `<run>.db` （ground truth，只读）
- **评测通道**：把 GT 的起点 + 场景灌进 Mujoco → 让 AI 代码在 sim 里实际驱动 → sim 输出落到 `sim_run_<id>.db`
- 然后 GT.db ↔ sim.db 做轨迹对比

回放仍有用，但只适合验证"感知 / 路径规划只读"类代码，**不适合验证运动控制类代码**。

## 4. 方案总览（四步闭环）

```
┌────────────────────────────────────────────────────────────────────┐
│                                                                    │
│   ┌──────────────┐   skill.py   ┌──────────────────┐              │
│   │ ① 代码生成   │ ───────────▶ │ ② 仿真测试       │              │
│   │  (LLM)       │              │  Mujoco + .db    │              │
│   └──────────────┘              └──────────────────┘              │
│         ▲                                │                         │
│         │ 反馈 JSON                       │ sim odom               │
│         │                                ▼                         │
│   ┌──────────────┐              ┌──────────────────┐              │
│   │ ④ 代码修正   │ ◀────────── │ ③ 错误反思       │              │
│   │  refine()    │   diff       │  GT vs sim       │              │
│   └──────────────┘   summary    │  ATE/RPE/Hit     │              │
│                                  └──────────────────┘              │
│                                                                    │
│   退出：metrics pass  或  iter ≥ MAX                                │
└────────────────────────────────────────────────────────────────────┘
```

四步契约、JSON 反馈结构、anti-pattern 列表见 [`CODEGEN_LOOP_SPEC.md`](./CODEGEN_LOOP_SPEC.md)。三个关键设计取舍：

1. **第 ③ 步必须返回"首次发散点"而不是只返回数字**。给 LLM 一个 `(t=17.4s, gt_pose, sim_pose, last_cmd_vel_window)`，它才知道往哪改。只给 metrics 是盲调。
2. **第 ④ 步强制"最小修改"**。否则 LLM 每轮全部推倒重写，永不收敛。prompt 里贴出上一版完整 source，明示"只改有问题的部分"。
3. **AI 代码白名单 import + 子进程沙箱**。`exec` LLM 代码必须隔离进程，避免污染评测机。

## 5. 数据约定（这是后面所有人的契约）

每条遥控录制产出两个产物 + 一份 sidecar：

| 产物 | 路径 | 用途 |
|---|---|---|
| 真值 | `data/runs/<run_name>.db` | AI 评测的 ground-truth（结构化时序，可 SQL） |
| 可视化 | `~/.local/share/dimos/rrd/dimos_<ts>.rrd` | 复盘 / demo / 调 Blueprint |
| 元数据 | `data/runs/<run_name>.meta.json` | `{scene, start_pose, task_text, duration_s, teleop_user, map_ref}` |

`.db` 必须含以下 stream：`odom`（**轨迹真值，必需**）、`cmd_vel`、`lidar`、`color_image`、`tf`、可选 `goal_request`。

## 6. 里程碑（两周到 demo）

| 周次 | 里程碑 | 验收 |
|---|---|---|
| W1 D1–D2 | 团队遥控产出 3 条 `office_lap_*.db` + meta.json | 能用 `TimedSensorReplay` 回放并在 Rerun 看到 |
| W1 D3–D4 | 写完 `dimos/eval/codegen_loop/{runner,sim,metrics}.py`，手写一段 skill 跑通对比 | Mujoco 跑出 sim_run.db，ATE/RPE 有数 |
| W1 D5 | 接通 LLM 产 skill，单轮成功率 ≥ 30% | propose + refine prompt 模板冻结 |
| W2 D1–D2 | 闭环迭代：失败 → 喂 metrics → 再生成，跑出"3 次内 pass"的 case | ≥1 个 office_lap pass |
| W2 D3 | Rerun 双轨迹叠加 Blueprint + 误差色带 | demo 可视化通过 |
| W2 D4–D5 | CI + 文档 | `pytest tests/eval/test_ai_codegen.py` 全绿 |

**DoD（Definition of Done）**：连续 3 个不同 `go2_*.db` 数据集，5 轮内 pass 率 ≥ 60%。

## 7. 风险与缓解

| 风险 | 影响 | 缓解 |
|---|---|---|
| Sim2real gap：Mujoco 轨迹与真车有差 | 严格 ATE 偶发不可达 | 用相对指标（RPE） + 覆盖率代替严格 ATE；阈值按场景标定 |
| memory2 写路径目前还在 pickle | 录制工程化弱 | 路线图 P0 #1：把 `SensorStorage` 写路径切到 `SqliteStore`（read-side 已迁，write-side TODO） |
| LLM 产生不安全代码 | 评测机污染 | exec 走子进程 + 白名单 import + 网络隔离 |
| 大场景下 Mujoco 慢 → 单轮迭代 > 5 分钟 | 闭环节奏被拖死 | mesh 简化、`mujoco_steps_per_frame` 调大、按起点 BBOX 裁场景 |
| `.rrd` 体积膨胀 | 磁盘满 | `memory_limit` 控 in-mem ring + logrotate 清 `~/.local/share/dimos/rrd/` |

## 8. 资源需求

| 项 | 估算 |
|---|---|
| 工程人力 | 1 个全栈 + 1 个 SLAM/sim 兼职，2 周 |
| 算力 | 1 台带 GPU 的评测机（Mujoco 不强求 GPU，但 CLIP / VLM 需要） |
| 数据采集 | 团队累计 ~4h 遥控录制（3 个办公楼场景 × 2 圈 × 30 分钟） |
| LLM 成本 | 每轮 ~10K token，10 个数据集 × 5 轮 = 500K token，可忽略 |

## 9. 为什么这条路值得做（一句话）

- **对外**：这是机器人行业版的"自动驾驶数据闭环"——把每条遥控数据变成可回归测试集，这是后续所有"AI codegen for robotics"产品的基础设施。
- **对内**：一旦闭环跑通，团队任何人都能在不写代码的情况下让狗完成新任务（"绕楼一圈检查门是否关着"），AI 自己迭代到通过为止。这是真正意义上的"vibecode your robot"。

---

## 附录 A：决策检查清单（给评审者）

- [ ] 同意"AI 评测必须走 Mujoco 而非纯回放"这一架构判断？（§3）
- [ ] 同意"首次发散点 + cmd_vel 窗口"作为反馈核心而非只给 metrics？（§4 取舍 1）
- [ ] 同意"refine 强制最小修改"而非每轮重写？（§4 取舍 2）
- [ ] 同意 W2 DoD：5 轮内 pass 率 ≥ 60%？（§6）
- [ ] 同意 P0 优先 `memory2` 写路径迁移？（§7）

## 附录 B：技术细节索引

- 架构梳理 + 代码定位 + 完整路线图 → [AI_CODEGEN_TRAJECTORY_LOOP.md](./AI_CODEGEN_TRAJECTORY_LOOP.md)
- 四步循环每步契约 + driver 代码骨架 + anti-pattern → [CODEGEN_LOOP_SPEC.md](./CODEGEN_LOOP_SPEC.md)
- 录制 / 回放 API 官方文档 → `docs/usage/data_streams/storage_replay.md`
- 可视化栈官方文档 → `docs/usage/visualization.md`
- memory2 设计 → `dimos/memory2/intro.md` + `dimos/memory2/architecture.md`
