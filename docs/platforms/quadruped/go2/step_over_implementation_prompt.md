# GO2 室内步越障碍 — 实现 Prompt（DimOS）

本文档为 Go2 EDU 室内自动步越功能的完整实现说明，可直接交给开发 Agent 或工程师使用。

---

## 角色与目标

你是 DimOS 仓库中的资深机器人软件工程师。在 **不破坏现有导航主链路** 的前提下，为 **Unitree Go2 ** 实现 **室内自动步越障碍** 能力，并接入 **`unitree-go2-agentic`** 蓝图（或在其上扩展的等价 agentic 栈）。

**禁止**：

- 引入 `FrontJump` / `FrontPounce` 等跳跃类 sport 命令作为步越手段
- 实现下文「明确排除」的条目（惩罚分、步越状态机迟滞等）

**目标**：

机器人在自主导航过程中，对前方障碍做 **2.5D 几何 + 腹下净空** 判定；可步越则继续通过；不可步越则 **优先重规划绕障**；绕不开则 **停车并语音播报**。

---

## 仓库与架构上下文

### 技术栈

- DimOS：Module + Blueprint（`autoconnect`），LCM 流，forkserver worker
- Go2 连接：`dimos/robot/unitree/go2/connection.py`（WebRTC，**5 cm 预处理体素点云**，非原始 LiDAR）
- 导航蓝图链：`unitree_go2_basic` → `VoxelGridMapper` → `CostMapper`（默认 `height_cost`）→ `ReplanningAStarPlanner`
- Agentic：`unitree_go2_agentic` = `unitree_go2_spatial` + `McpServer` + `McpClient` + `_common_agentic`（含 `SpeakSkill`）

### 已有能力（复用，勿重复造轮子）

| 组件 | 路径 / 说明 |
|------|-------------|
| 体素地图 | `dimos/mapping/voxels.py` — 5 cm 体素，column carving |
| 高度代价图 | `dimos/mapping/pointclouds/occupancy.py` — `height_cost_occupancy`，`can_climb≈0.15`、`can_pass_under≈0.6`（**仅坡度绕障，≠步越**） |
| 全局规划 | `dimos/navigation/replanning_a_star/global_planner.py` — **距目标 >1.5 m 用 voronoi，≤1.5 m 用 gradient**（硬切换，需按本 spec 改迟滞） |
| 局部跟踪 | `dimos/navigation/replanning_a_star/local_planner.py` + `PathClearance` |
| 运动 / 语音 | `UnitreeSkillContainer`、`SpeakSkill`；**无**官方「步越」API |
| 机身参考 | `dimos/robot/unitree/go2/go2.urdf` 包络约 **0.7×0.31×0.4 m**；`GlobalConfig.robot_width≈0.3`，`robot_rotation_diameter≈0.6` |

### 文档

- 导航管线：`docs/capabilities/navigation/native/index.md`

---

## 产品需求（必须满足）

### 运动与判定

| 项 | 要求 |
|----|------|
| 动作类型 | **步越**（正常步态过棱），**禁止**跳跃类 `execute_sport_command` |
| 踏面高度 | 相对 **当前脚下地面** 的障碍顶高度 **≤ 0.10 m** → 允许步越 |
| 宽度（默认，可配置） | 沿路径 **台面深度 ≤ 0.20 m**；**孤立块**挡路宽度 **0.10–0.50 m**；两障碍 **缝宽 ≥ 0.40 m** |
| 连续路沿 | 横向整段挡路时 **不限制** 横向宽度，仅看高度 + 台面深度 + 腹下净空 |
| 触发 | **导航过程中自动**检测与决策，**不要**做成仅 Agent 手动调用的 `@skill`（除非另有内部 RPC） |
| 场景 | **室内真机**，机型 **Go2 EDU** |
| 失败策略 | **能绕就绕**（触发重规划）；**绕不开则停** + **`speak("前方障碍无法通过")`**（文案固定） |
| 运行蓝图 | **`unitree-go2-agentic`**（需 `SpeakSkill` + 完整导航栈） |

### 动态障碍

- **人 / 明显移动物体**：不执行步越，走绕障或停车逻辑（保守默认）。

### 默认距离（可写入 `StepOverConfig`，真机可调）

- 约 **1.0 m** 开始分析前方 ROI
- 约 **0.4 m** 前执行步越相关行为（减速 / 允许沿路径继续等，见「执行层」）

---

## 工程约束（必须实现）

### 约束 1 — 腹下净空（防托底）

**问题**：仅看「棱高 ≤10 cm」会导致 **肚子蹭台沿 / 托底**。

**要求**：

- 在机器人前方路径 **条带**（建议沿 `base_link` 前向 **0.25–0.55 m**，宽度约 **robot_width + 余量**）内做 **2.5D 分析**：
  - 估计局部地面 \(z_{ground}\)
  - 估计障碍顶 \(z_{top}\) 与台面几何（深度、挡路宽度）
  - 计算机身包络在该段上的 **腹下净空** \(g\)（机身下沿相对地面 / 障碍的有效最小间隙）
- **允许步越** 需同时满足：
  - 踏面高度 \(h \le 0.10\,\text{m}\)
  - 宽度规则满足（见上表）
  - **腹下净空** \(g \ge g_{min}\)，初值 **`g_min = 0.12 m`**（12 cm，可配置，真机标定）
- \(h\) 满足但 \(g\) 不足 → 视为 **不可越**（绕或停 + speak）

### 约束 2 — 1.5 m 规划策略迟滞（防路径左右横跳）

**问题**：`GlobalPlanner._find_wide_path` 在 `distance(goal) > 1.5` 使用 **voronoi**，否则使用 **gradient**，硬切换导致重规划路径在边界附近 **左右甩**。

**要求**（修改 `global_planner.py` 或等价逻辑）：

```text
进入 near（gradient）：  dist(goal) < 1.3 m
回到 far（voronoi）：    dist(goal) > 1.7 m
区间 [1.3, 1.7] m：      保持上一档地图类型，不切换
```

- 状态需在 planner 实例内 **记忆**（例如 `_using_near_map: bool`）
- **可选增强**：若新路径与当前路径在机器人附近 **横向偏差 > 0.3 m** 且 **航向差 > 25°**，且距上次采纳新路径 **< 3 s**，则 **抑制采纳** 或延迟 replan（实现方式自定，须在 PR / 注释中说明）

### 约束 3 — 点云噪点抑制

| 手段 | 规格 |
|------|------|
| 时间一致性 | ROI 内「可步越」条件 **连续 ≥ 5 帧**（约 0.5 s @10 Hz）才触发步越执行 |
| 空间滤波 | 对 ROI 内 5 cm 体素 / 高度图：**开运算 1 次** 或 **3×3 中值**，去除孤立高刺 |
| 高度死区 | 低于 **`ignore_noise = 0.05 m`** 的高度起伏忽略；**> 0.10 m** 直接判不可越 |
| 窄刺剔除 | 估计挡路宽度 **< 0.10 m** 的连通域 **不计为障碍** |

---

## 明确排除（不要实现）

1. **跨越惩罚分** `J_cross`、绕路 vs 步越的代价比较优化
2. **步越状态机迟滞**（如 9 cm 进入 / 11 cm 退出、COOLDOWN 2 s 等）
3. **`FrontJump` / `FrontPounce`** 等 sport 命令作为步越手段
4. 将本功能做成 **LLM `@skill`** 暴露给 Agent（自动链路即可）

---

## 执行层（Go2 无专用步越 API）

**现实**：Unitree WebRTC / Sport API **没有**「StepOver」指令。

**第一版可接受行为**（须在代码与文档注释中写清）：

1. **判定链**：体素 / 全局图 → ROI 2.5D → 噪点滤波（约束 3）→ 几何 + 腹下（约束 1）→ 布尔「可越 / 不可越」
2. **可越**：通知导航 **允许** 沿当前 / 重规划路径 **低速通过**（若连接层有 `body_height` 等接口可选用；**无则仅降速**，勿伪造 API）
3. **不可越**：调用 planner **replan**；若 replan 失败或 `cancel_goal` → **cmd_vel 零** + **SpeakSkill** 固定话术
4. **不要**假设步越成功率；日志中输出 \(h, d, g, \text{width}\) 便于 Foxglove / Rerun 调试

### 集成点建议（实现者自选一种并论证）

- **方案 A**：新 Module `StepOverModule`，订阅 `global_map` 或体素点云 + `odom` + planner state，发布步越许可 / 禁止或触发 replan RPC
- **方案 B**：在 `LocalPlanner` / `PathClearance` 前插入障碍条带检测（侵入面更小但耦合高）

无论方案，须接入 **`unitree-go2-agentic`**（或等价 agentic 蓝图），并按仓库规范更新 blueprint 注册（`pytest dimos/robot/test_all_blueprints_generation.py`）。

---

## 配置建议（`StepOverConfig` / `ModuleConfig`）

```yaml
# 几何
max_step_height_m: 0.10
min_belly_clearance_m: 0.12
max_ledge_depth_m: 0.20
isolated_obstacle_width_min_m: 0.10
isolated_obstacle_width_max_m: 0.50
min_gap_width_m: 0.40
ignore_noise_m: 0.05

# 距离
analyze_distance_m: 1.0
execute_distance_m: 0.4

# 噪点（约束 3）
stable_frames_required: 5
morph_open_iterations: 1

# 规划迟滞（约束 2）
planner_near_enter_m: 1.3
planner_far_exit_m: 1.7

# 语音
blocked_speech_text: "前方障碍无法通过"
```

---

## 测试与验收

### 环境

- 室内真机 Go2 EDU：`dimos run unitree-go2-agentic --robot-ip <IP>`
- 测试物：约 **8 cm**、**12 cm** 路沿 / 木条（宽 ≥ 30 cm）

### 用例

| # | 场景 | 期望 |
|---|------|------|
| T1 | 8 cm 路沿，正向接近 | 连续 5 帧满足后 **通过**（或低速通过），无托底 |
| T2 | 12 cm 棱 | **不步越**；**绕路**或 **停 + speak** |
| T3 | 8 cm 但台面深 > 20 cm | 不可越（平台化） |
| T4 | 孤立 5 cm 宽噪点刺 | 被滤除，不误触发 |
| T5 | 目标在 1.4 m 附近来回 | 地图策略 **不频繁 voronoi↔gradient 翻转**（日志可见切换次数明显减少） |

### 日志 / 可视化

- 每次判定输出：`h`, `d`, `g`, `width`, `passable`, `stable_frame_count`
- 可选：向 Rerun 发布 ROI 条带与阈值线（若项目已有 bridge 模式则对齐）

---

## 代码质量与仓库规范

- 遵循 `AGENTS.md`：类型注解、mypy、ruff、文件头 license、无 inline import（除循环依赖）
- 新模块需 `start` / `stop` RPC；**不需要** `@skill`（本功能非 Agent 工具）
- 不改手写 `dimos/robot/all_blueprints.py` — 跑 `pytest dimos/robot/test_all_blueprints_generation.py`
- 单元测试：至少覆盖 **几何判定纯函数**（给定高度图 → passable）与 **1.5 m 迟滞状态机**（给定距离序列 → 地图类型序列）
- PR 描述须含：为何不用 `height_cost` 代替、Go2 无步越 API 的第一版行为、真机标定项

---

## 交付清单

1. `StepOverModule`（或等价）+ `StepOverConfig`
2. `GlobalPlanner` 1.3 / 1.7 m 迟滞（+ 可选 replan 防抖）
3. 接入 `unitree-go2-agentic`（及必要的 spec / remap）
4. 测试：`test_step_over_*.py`、`test_global_planner_map_hysteresis.py`（名称可调整）
5. 用户文档：`docs/platforms/quadruped/go2/step_over.md`（行为、阈值、限制、真机调试步骤）

---

## 一句话摘要

> 在室内 Go2 自动导航中，用前方 ROI 的 2.5D 几何（≤10 cm 高、宽度规则、≥12 cm 腹下净空）+ 5 帧稳定与体素去噪，决定低速过棱或绕障 / 停播；同时把全局规划 1.5 m 处的 voronoi / gradient 硬切改为 1.3 / 1.7 m 迟滞，避免路径横跳；不做法术跳跃、惩罚分或 9 / 11 cm 状态迟滞。

---
