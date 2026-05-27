# 空间记忆盲区自动探索设计文档

## 1. 背景

当前空间记忆模块可以在机器人运行时记录图像帧、位姿和语义向量，并支持按文本或位置查询历史记忆。这个能力解决了“机器人在哪里看过什么”的问题，但还缺少一个巡检场景中很关键的能力：

机器人无法主动判断“哪些可通行区域还没有空间记忆覆盖”，也无法在发现记忆盲区后主动前往补采样。

在真实巡检任务中，仅有地图覆盖并不等于记忆覆盖。机器人可能已经知道一片区域可通行，但摄像头从未看过那里，或者上一次记录已经很久以前。这样的区域会导致后续语义查询、异常检测、环境回忆都缺少可靠依据。

## 2. 目标

新增“空间记忆盲区自动探索”能力：

机器人在巡检过程中检查附近可通行区域的空间记忆覆盖情况。如果发现某个区域没有记忆，或记忆已经过期，则选择一个安全、可达、距离较近的盲区点作为目标，调用导航前往该区域补采样。

这个方向可以概括为：

> 在空间记忆中做自动探索，让探索目标由“记忆覆盖缺口”产生。

它不是让机器人为了扩大地图而探索，而是让机器人为了补齐视觉/语义记忆而探索。也就是说，地图上已经可通行的地方，如果空间记忆没有看过、没有存过、或者太久没看过，就会被系统识别为需要补采样的区域。

一句话目标：

> 机器人不仅能记住走过的地方，还能发现自己“没有好好记住”的地方，并主动过去补记忆。

建议功能名：

```text
explore_memory_blindspot
```

也可以作为后续产品化命名：

```text
refresh_spatial_memory
```

MVP 推荐使用 `explore_memory_blindspot`，因为它更明确表达“发现记忆盲区并前往补采样”的行为。

第一版目标就是支持长时间探索。在 one-shot 能力之上增加一个长时间连续探索模式：

```text
patrol_memory_blindspots
```

它会持续调用盲区选择逻辑，根据空间记忆覆盖缺口寻找下一个探索目标。探索可以持续较长时间，例如 15 到 60 分钟，但必须支持人工停止、单目标超时、连续失败停止、低电量/急停等安全退出条件。

## 3. 非目标

本次改动不做以下内容：

- 不替代现有 frontier exploration 或 coverage patrol。
- 不改变空间记忆的图像 embedding 模型。
- 不改动底层导航控制逻辑。
- 不做无约束、不可中断的无限探索；长时间探索必须可被 `stop_all_motion` / `stop_navigation` / `dimos stop` 中断。
- 不直接命令机器人驶入未知或障碍栅格；如果目标是未探索区域，应选择未知边界附近的安全可达 free cell 作为观察点。

## 4. 用户场景

### 4.1 现实演示场景

1. 启动 Go2 agentic blueprint。
2. 让机器人只巡视办公室或实验室的一部分区域。
3. 空间记忆记录已巡视区域的图像和位姿。
4. 机器人停在中间位置，周围保留一些空间记忆未覆盖的区域，包括已知可通行区域和未知边界附近区域。
5. 用户发出指令：

```text
检查附近有没有空间记忆盲区，有的话过去探索一下。
```

6. 机器人查询 costmap、odom 和空间记忆覆盖情况。
7. 系统发现一个附近可通行但没有记忆覆盖的点。
8. 机器人调用导航前往该点。
9. 到达附近后空间记忆继续采集图像帧。
10. 如果开启长时间探索模式，系统继续寻找下一个安全盲区或记忆未探索边界，直到用户停止、持续时间结束、连续失败过多、无可达目标或触发安全退出条件。
11. 系统返回：

```text
完成空间记忆驱动探索：运行 28 分钟，访问 12 个记忆盲区/未覆盖边界，新增 86 帧空间记忆。
```

### 4.2 产品价值

- 巡检任务中主动补齐记忆覆盖。
- 降低语义查询结果遗漏的概率。
- 让机器人从“被动记录”升级为“主动维护空间记忆质量”。
- 可以作为长期自主巡检、异常检测、语义导航的前置能力。
- 在 `semantic-nav-robust-loop` 的基础上，补强语义导航依赖的记忆质量，而不是继续堆叠新的找物体路径。

### 4.3 与普通探索的区别

| 普通探索 | 记忆驱动探索 |
|---|---|
| 目标是扩大地图或发现未知区域 | 目标是补齐空间记忆覆盖 |
| 依据 costmap frontier / coverage router | 依据 spatial memory frame locations + costmap 可达性 |
| 关注哪里没建图 | 关注哪里没被空间记忆看过、没记住、记忆过期 |
| 更偏导航层能力 | 是空间记忆 + 导航结合能力 |
| 可能进入未知边界 | 选择未知边界附近的安全观察点，为未覆盖区域补视觉记忆 |

本功能可以复用导航系统完成移动，但“要去哪里”的决策由空间记忆覆盖质量决定。

## 5. 核心概念

### 5.1 空间记忆覆盖

空间记忆覆盖指：某个世界坐标点附近是否存在有效的历史记忆帧。

有效记忆帧需要满足：

- 位置距离目标点不超过 `coverage_radius_m`。
- 记忆时间未超过 `stale_after_sec`，除非用户允许使用旧记忆。

### 5.2 空间记忆盲区

空间记忆盲区指：

- costmap 中可通行但缺少空间记忆覆盖的区域；
- 或者未知边界附近、可从安全 free cell 观察到的未探索区域；
- 距离机器人不太远；
- 与障碍物保持安全距离；
- 在 `coverage_radius_m` 范围内没有有效空间记忆帧。

### 5.3 过期记忆区域

如果某区域曾经有空间记忆，但最近一帧太旧，则该区域不是完全盲区，而是“记忆过期区域”。

MVP 可以把过期区域也当成盲区候选，但返回文案中区分：

- `missing`: 从未记录过。
- `stale`: 有记忆，但已经过期。

### 5.4 记忆驱动探索

记忆驱动探索指：系统先从空间记忆判断哪里缺少视觉/语义记录，再把该位置转换为导航目标。

完整判断链路：

1. 地图告诉系统：这片区域能不能走。
2. 空间记忆告诉系统：这片区域有没有被看过。
3. 时间戳告诉系统：这片区域的记忆是否过期。
4. 导航系统负责：前往选中的补采样目标。

因此，本功能的核心不是“探索未知地图”，而是“主动维护空间记忆质量”。

第一版可以把目标分为两类：

| 类型 | 说明 | 导航目标 |
|---|---|---|
| `memory_gap` | 已知可通行区域缺少空间记忆覆盖 | 区域内部的 safe free cell |
| `memory_frontier` | 地图/视野边界附近缺少空间记忆，适合继续观察 | 未知边界旁的 safe free cell |

机器人不会直接把未知栅格作为导航目标，而是去未知/未覆盖区域旁边的安全观察点，靠相机继续补充空间记忆。

## 6. 功能设计

### 6.1 新增空间记忆查询能力

在 `SpatialMemory` 增加 RPC：

```python skip
def get_memory_locations(self) -> list[dict[str, float | str]]:
    """Return stored spatial memory frame locations and timestamps."""
```

返回字段建议：

| 字段 | 类型 | 说明 |
|---|---|---|
| `frame_id` | `str` | 记忆帧 ID |
| `pos_x` | `float` | 世界坐标 x |
| `pos_y` | `float` | 世界坐标 y |
| `pos_z` | `float` | 世界坐标 z |
| `timestamp` | `float` | 记录时间 |

同时修复或兼容已有位置查询字段：

当前空间记忆写入 metadata 时使用 `pos_x`、`pos_y`、`pos_z`，但部分查询代码读取的是 `x`、`y`。本次应统一使用 `pos_x`、`pos_y`，并兼容旧字段 `x`、`y`。

### 6.2 新增盲区查找能力

在导航 skill 或单独 helper 中实现：

```python skip
def find_nearest_memory_blindspot(
    robot_pose: PoseStamped,
    costmap: OccupancyGrid,
    memory_locations: list[dict[str, Any]],
    search_radius_m: float = 5.0,
    coverage_radius_m: float = 1.0,
    stale_after_sec: float = 600.0,
) -> PoseStamped | None:
    ...
```

候选点生成策略：

1. 从 robot pose 周围的 costmap 网格中采样候选点，包括普通 free cells 和未知边界附近的 free cells。
2. 过滤不可通行点和障碍物附近点。
3. 计算候选点到最近有效记忆帧的距离。
4. 如果距离大于 `coverage_radius_m`，则认为是盲区。
5. 对未知边界附近候选点增加 `memory_frontier` 权重，让机器人优先走向未被空间记忆覆盖的新区域。
6. 按距离机器人近、远离障碍物、记忆缺失程度、是否能带来新记忆覆盖排序。
7. 返回排名最高的目标点。

MVP 可以先使用规则排序：

```text
score = robot_distance + obstacle_penalty - blindspot_bonus - frontier_bonus
```

### 6.3 新增 One-shot Agent Skill

在 `NavigationSkillContainer` 增加 one-shot skill，作为连续模式的基础能力：

```python skip
@skill
def explore_memory_blindspot(
    self,
    search_radius_m: float = 5.0,
    coverage_radius_m: float = 1.0,
    stale_after_sec: float = 600.0,
) -> str:
    """Find a nearby reachable area with missing or stale spatial memory and navigate there."""
```

行为：

1. 检查 skill 是否启动。
2. 检查 odom 是否可用。
3. 获取当前 costmap。
4. 获取空间记忆位置列表。
5. 查找最近盲区。
6. 如果没有盲区，返回“附近空间记忆覆盖良好”。
7. 如果找到盲区，调用 `_navigation.set_goal(goal_pose)`。
8. 返回目标位置和原因。

返回示例：

```text
Found a memory blind spot 2.4m away at (3.2, -1.7). Started navigating there to collect spatial memory.
```

如果没有找到：

```text
No reachable memory blind spot found within 5.0m. Nearby spatial memory coverage looks healthy.
```

### 6.4 新增长时间记忆驱动探索 Skill

第一版需要机器人基于空间记忆持续探索未覆盖区域，新增长时间探索 skill：

```python skip
@skill
def patrol_memory_blindspots(
    self,
    search_radius_m: float = 8.0,
    coverage_radius_m: float = 1.0,
    stale_after_sec: float = 600.0,
    max_goals: int = 0,
    max_duration_sec: float = 120.0,
    goal_timeout_sec: float = 90.0,
    stuck_timeout_sec: float = 15.0,
    progress_epsilon_m: float = 0.25,
    cooldown_sec: float = 2.0,
    recognize_on_arrival: bool = True,
    include_object_summary: bool = True,
) -> str:
    """Explore spatial-memory-uncovered areas for an extended period."""
```

实现时建议把 docstring 写得更明确，帮助 agent 将自然语言指令映射到该 skill：

```python skip
"""Explore areas that are not yet covered by spatial memory.

Use this when the user asks the robot to explore unexplored areas,
inspect unvisited places, fill spatial-memory gaps, refresh memory coverage,
or build spatial memory over time. The robot navigates to safe observation
points near spatial-memory-uncovered areas, records new spatial memory, and
can recognize objects on arrival.
"""
```

行为：

1. 记录开始时间。
2. 循环查找下一个安全的空间记忆未覆盖目标，包括 `memory_gap` 和 `memory_frontier`。
3. 每次最多发送一个导航目标，并等待到达、超时或被取消。
4. 到达或超时后短暂冷却，再重新计算记忆覆盖缺口。
5. 默认按 `max_duration_sec=120.0` 运行；真机长时间测试可显式传入 `max_duration_sec=1800.0` 或更长时间。如果传入 `max_goals > 0`，也可按目标数停止。
6. 超过 `max_duration_sec`、达到 `max_goals`、找不到目标、连续失败过多或收到停止命令时结束。
7. 如果 `include_object_summary=True`，汇总本轮新探索区域识别到的物品。
8. 返回探索摘要和新物品清单。

返回示例：

```text
Memory-driven exploration finished: visited 2 goal(s), timed out 1, stuck 0, elapsed 120s.
Stopped because max_duration_sec reached.
New objects found in explored areas: chair x3, desk x1, monitor x2, backpack x1.
```

如果中途没有可用目标：

```text
Memory-driven exploration finished: visited 8 goal(s), elapsed 1220s.
Stopped because no reachable memory-uncovered target remained within 8.0m.
```

### 6.5 MVP 功能链路

第一版保持小而完整：

1. 从空间记忆取所有历史帧位置。
2. 从当前 costmap 取机器人附近可通行点。
3. 找到一个附近没有记忆覆盖，或记忆已经过期的可通行点。
4. 选择最近且安全的目标点。
5. 调用导航前往该点。
6. 到达后由现有空间记忆模块继续自然记录新帧。
7. 如果是 one-shot 模式，返回“已前往补采样”的解释信息。
8. 如果是长时间探索模式，重新计算下一个记忆未覆盖目标，持续探索直到触发停止条件。

MVP 应包含连续探索模式。默认 `max_duration_sec=120.0`，用于保持一次 agent/RPC 调用在默认超时范围内；真机长时间测试可以显式传入 15 到 60 分钟的 `max_duration_sec`。`max_goals` 是可选保护参数，`0` 表示不按目标数量停止。每个目标仍然必须有 `goal_timeout_sec`，并且实际等待会被剩余 `max_duration_sec` 截断，避免单个不可达目标让巡逻超出总时长。

### 6.6 与当前分支的关系

当前 `feat/semantic-nav-robust-loop` 已经把自然语言导航做成多阶段 fallback，并引入 room / landmark / VLM memory / CLIP map 等记忆能力。`explore_memory_blindspot` 不直接改这些 fallback 的决策顺序，而是提升它们依赖的数据质量：

- `navigate_with_text` 需要可靠的空间记忆候选。
- `room_sweep` 和 `vlm_memory` 需要足够多、足够新的房间参考图。
- `find_room_visually` 需要多视角 room image refs。
- 记忆盲区探索可以主动补齐这些数据，让后续语义导航更稳。

因此，这个功能是对 robust semantic navigation loop 的记忆质量优化，而不是新的独立导航策略。

### 6.7 探索过程中的内容识别与保存

当前分支已经支持 LLM/VLM 识别图像内容并保存到 landmark memory，但不是每一帧都会自动识别。已有触发点包括：

- `tag_location` / `tag_room`: 保存房间参考图，并异步识别房间内物体。
- `detect_objects_in_view`: 手动识别当前画面物体，并保存为 object landmark。
- `navigate_with_text`: fallback 中会使用当前图像或历史图像做 VLM 查询，但不一定每次都保存新 landmark。

为了让长时间记忆驱动探索形成完整闭环，本功能建议在探索过程中增加低频内容识别：

```python skip
recognize_on_arrival: bool = True
recognize_interval_sec: float = 30.0
```

行为：

1. 机器人到达一个 `memory_gap` 或 `memory_frontier` 目标附近。
2. 等待空间记忆模块记录新图像帧。
3. 如果 `recognize_on_arrival=True`，调用现有 VLM 识别当前画面内容。
4. 将识别出的物体保存到 `SpatialLandmarkMemory`。
5. 为本轮新保存的 landmark 追加探索来源 metadata，例如 `source=memory_blindspot_explorer`、`exploration_run_id`、`target_id`、`target_type`、`target_pose`。
6. 在内存中记录本轮每个探索目标新增的物品名、数量和位置。
7. 空间记忆继续保存原始图像、位姿和 embedding。

完整链路：

```text
发现记忆未覆盖区域
→ 导航到安全观察点
→ 空间记忆保存图像 + 位姿 + embedding
→ VLM/LLM 识别图像内容
→ 保存 room/object landmark
→ 汇总本轮新探索区域识别到的物品
→ 下一轮继续寻找记忆未覆盖区域
```

第一版可以只在到达每个目标后触发一次识别，避免长时间探索时频繁调用 VLM 造成延迟和费用不可控。

探索结束时，skill 返回值应包含本轮新探索区域的物品清单，方便真机演示时直接看到结果。例如：

```text
完成记忆驱动探索：运行 18 分钟，访问 7 个未覆盖区域，新增 42 帧空间记忆。
新探索区域识别到的物品：
- 区域 1 office_frontier: chair, desk, monitor
- 区域 2 hallway_gap: trash can, fire extinguisher
- 区域 3 meeting_room_frontier: table, whiteboard, chair
```

如果用户在探索结束后继续询问：

```bash
dimos tell '列出刚才新探索区域的物品'
```

agent 应优先查询最近一次 `exploration_run_id` 对应的 landmarks，而不是列出全部历史 landmarks。这样可以区分“以前记住的物品”和“这次新探索发现的物品”。

## 7. 安全策略

为了保证现实演示稳定，MVP 必须有以下限制：

| 策略 | 说明 |
|---|---|
| 长时间但可中断 | `patrol_memory_blindspots` 可以长时间运行，但必须支持 `stop_all_motion`、`stop_navigation`、`dimos stop` |
| 运行时间上限 | 默认使用 `max_duration_sec` 作为主停止条件，避免无人看管无限运行 |
| 可选目标数上限 | `max_goals` 可为空；如果设置则达到目标数后停止 |
| 每轮只发一个 goal | 即使连续模式，也必须等待当前目标结束后再选下一个 |
| 目标落在安全 free cell | 可以面向未知/未覆盖区域探索，但导航 goal 必须是安全可达的 free cell |
| 目标离障碍物保持安全距离 | 使用 inflation 或邻域检查 |
| 搜索半径限制 | 默认 8m，可根据现场扩大，但不能无界全图随机跑 |
| 单目标超时限制 | `goal_timeout_sec` 到期后取消当前目标并进入下一轮或结束 |
| 支持 `stop_all_motion` 取消 | 沿用当前分支的停止/恢复技能 |
| 无 odom/costmap 时不移动 | 只返回错误信息 |
| 连续失败上限 | 连续找不到目标或导航失败时自动停止 |

## 8. 影响范围

| 模块 | 影响 |
|---|---|
| `dimos/agents_deprecated/memory/spatial_vector_db.py` | 修复位置字段兼容，新增记忆位置列表查询 |
| `dimos/perception/spatial_perception.py` | 新增空间记忆覆盖查询 RPC |
| `dimos/perception/spatial_memory_spec.py` | 增加新 RPC 协议定义 |
| `dimos/agents/skills/navigation.py` | 新增 `explore_memory_blindspot` 和 `patrol_memory_blindspots` skill |
| `SpatialLandmarkMemory` 写入路径 | 为探索过程中新增的 landmark 追加 `exploration_run_id` 等 metadata，便于列出本轮新发现物品 |
| `dimos/agents/skills/test_navigation.py` | 增加 skill 行为测试 |
| 可选 `dimos/memory2/vis/space` | 后续可视化盲区目标，不作为 MVP 必须项 |

不影响：

- 现有 `query_by_text` 语义查询。
- 现有 `navigate_with_text` 行为。
- 机器人底层运动控制。
- ChromaDB collection schema 的主流程。

## 9. 实现约束和开发顺序

### 9.1 实现约束

实现时不要重写导航系统，不要新建一套 exploration 框架。第一版应优先复用当前分支已有能力：

- `NavigationSkillContainer`
- `SpatialMemory`
- `SpatialLandmarkMemory`
- `navigate_with_text`
- `detect_objects_in_view`
- `stop_all_motion`
- 现有 odom / costmap / navigation `set_goal` 链路

新增逻辑只负责：

1. 读取空间记忆覆盖。
2. 找到空间记忆未覆盖区域或记忆未探索边界。
3. 选择安全可达的 free cell 作为观察点。
4. 调用现有导航能力前往该观察点。
5. 到达后复用现有空间记忆和 VLM/LLM 识别能力保存内容。

### 9.2 第一版开发顺序

建议按以下顺序实现，避免一次性改动过大：

1. 在 `SpatialMemory` / `SpatialVectorDB` 增加 `get_memory_locations()`，返回历史记忆帧位置和时间戳。
2. 修复/兼容位置 metadata 字段：同时支持 `pos_x` / `pos_y` 和旧字段 `x` / `y`。
3. 抽出盲区候选选择 helper，例如 `find_nearest_memory_blindspot(...)`。
4. 支持两类目标：`memory_gap` 和 `memory_frontier`。
5. 在 `NavigationSkillContainer` 增加 one-shot skill：`explore_memory_blindspot(...)`。
6. 在 `NavigationSkillContainer` 增加长时间探索 skill：`patrol_memory_blindspots(...)`。
7. 增加 `recognize_on_arrival`，到达目标后低频调用现有 VLM/LLM 识别并保存 landmark。
8. 补单元测试：记忆位置查询、盲区选择、one-shot skill、长时间探索停止条件。
9. 按 `13. 当前分支使用方法` 做真机或仿真链路验证。

## 10. 测试计划

### 10.1 单元测试

新增测试覆盖：

- `query_by_location` 支持 `pos_x`、`pos_y` metadata。
- `get_memory_locations` 返回所有已存记忆帧位置。
- 无记忆时，附近 free cell 被识别为盲区。
- 有有效记忆覆盖时，不返回盲区。
- 只有过期记忆时，返回 stale blindspot。
- 无 odom/costmap 时 skill 返回错误且不调用导航。
- 找到盲区时 skill 调用 `set_goal()`。
- 长时间探索达到 `max_duration_sec` 后停止。
- 设置 `max_goals` 时，达到 `max_goals` 后停止。
- 单个目标超过 `goal_timeout_sec` 后取消当前目标。
- 连续模式每轮最多发送一个新 goal，不并发发送多个目标。
- 能选择 `memory_frontier` 类型目标：未知/未覆盖区域旁边的 safe free cell。
- 开启 `recognize_on_arrival` 时，到达目标后会低频触发 VLM 内容识别并保存 landmark。

### 10.2 命令验证

建议验证命令：

```bash
uv run pytest dimos/agents/skills/test_navigation.py -v
uv run pytest dimos/perception/test_spatial_memory.py -k location -v
uv run pytest dimos/navigation/frontier_exploration/test_wavefront_frontier_goal_selector.py -k goal -v
uv run ruff check dimos/agents/skills dimos/perception dimos/agents_deprecated/memory
uv run mypy dimos/agents/skills dimos/perception dimos/agents_deprecated/memory
```

### 10.3 现实演示验证

演示前准备：

- 确认 Go2 能正常发布 odom、costmap、camera。
- 确认空间记忆模块正在记录帧。
- 设置较小搜索半径，例如 3m。
- 设置较小覆盖半径，例如 0.8m。
- 保证盲区目标区域没有障碍物。

演示指令：

```text
检查附近有没有空间记忆盲区，有的话过去探索一下。
```

预期结果：

- 系统返回盲区目标位置。
- 机器人向目标移动。
- 到达附近后空间记忆新增帧。
- 如果调用 `explore_memory_blindspot`，完成一个目标后停止。
- 如果调用 `patrol_memory_blindspots`，会继续选择下一个盲区，直到达到 `max_goals`、超时、无盲区或收到停止命令。

### 10.4 真机测试方法

真机测试分三步进行：先验证数据链路，再验证 one-shot 移动，最后验证长时间 memory-driven exploration。不要第一次就直接跑 30 分钟，先用短时间参数确认行为，再逐步拉长运行时间。

#### 10.4.1 测试前准备

环境要求：

- 选择空旷、可控、低动态干扰的区域，例如办公室一角、实验室空地或走廊。
- 保证至少有一块区域地图/costmap 中可通行，但机器人尚未用相机看过。
- 现场至少一人负责观察机器人，随时准备急停。
- 地面无小物体、电线、玻璃门槛等容易造成规划或避障异常的障碍。

启动：

```bash
dimos run unitree-go2-agentic --robot-ip <机器人IP> --daemon
dimos status
dimos log -f
```

如果使用 MCP 工具调用，先确认工具可见：

```bash
dimos mcp status
dimos mcp list-tools
```

安全停止命令必须提前确认可用：

```bash
dimos agent-send "stop all motion"
dimos agent-send "stop_navigation"
dimos stop
```

MCP 方式：

```bash
dimos mcp call stop_all_motion
```

#### 10.4.2 数据链路检查

先确认机器人具备盲区选择所需输入：

| 输入 | 检查方式 | 期望 |
|---|---|---|
| odom | `dimos log -f` 或状态日志 | 持续更新机器人位置 |
| costmap | 可视化/日志/模块状态 | 有非空地图，包含 free cells |
| camera | 空间记忆日志 | 能收到图像帧 |
| spatial memory | 日志中出现 `Stored frame at position` | 能记录图像和位姿 |
| navigation | 手动发送小目标或已有导航 skill | `set_goal` 能驱动机器人移动 |

如果任一输入缺失，`explore_memory_blindspot` 和 `patrol_memory_blindspots` 都应只返回错误信息，不应移动。

#### 10.4.3 构造记忆盲区

1. 清空旧记忆，避免历史数据影响测试：

```bash
dimos agent-send "clear all memory"
```

2. 让机器人只观察一小块区域，例如只在当前位置附近转头或移动 1 到 2 米。
3. 保留另一侧区域不让相机看见，但该区域在 costmap 中仍然可通行。
4. 观察日志，确认空间记忆已记录当前区域：

```text
Stored frame at position (...)
```

#### 10.4.4 One-shot 低风险测试

第一轮只测试 one-shot，并使用小参数：

```bash
dimos mcp call explore_memory_blindspot \
  --arg search_radius_m=2.0 \
  --arg coverage_radius_m=0.8 \
  --arg stale_after_sec=600.0
```

如果通过 agent 自然语言调用：

```bash
dimos agent-send "检查附近2米内有没有空间记忆盲区，有的话过去探索一下"
```

期望结果：

- 系统返回一个盲区目标位置。
- 机器人只发送一个导航目标。
- 机器人不会连续选择下一个目标。
- 到达附近后空间记忆新增帧。

期望日志：

```text
Found memory blindspot candidate ...
Started navigating there to collect spatial memory
Stored frame at position ...
```

失败时预期：

- 如果无 odom/costmap，返回缺少输入，不移动。
- 如果附近都被覆盖，返回 coverage healthy，不移动。
- 如果目标不可达，取消当前目标，不继续无限重试。

#### 10.4.5 短时连续探索测试

确认 one-shot 稳定后，再测试连续模式。第一轮连续测试建议参数较小，用来验证循环、停止条件和目标更新逻辑：

```bash
dimos mcp call patrol_memory_blindspots \
  --arg search_radius_m=3.0 \
  --arg coverage_radius_m=0.8 \
  --arg stale_after_sec=600.0 \
  --arg max_goals=2 \
  --arg max_duration_sec=60.0 \
  --arg goal_timeout_sec=25.0 \
  --arg cooldown_sec=2.0
```

自然语言调用示例：

```bash
dimos agent-send "在附近3米内连续探索空间记忆盲区，最多去2个点，最多运行60秒"
```

期望结果：

- 机器人最多前往 2 个盲区。
- 每次只执行一个 goal，等当前 goal 到达/超时/失败后再计算下一个。
- 达到 `max_goals` 或 `max_duration_sec` 后自动停止。
- 找不到新盲区时自动停止。
- 中途调用 `stop_all_motion` 能立即停止导航和 tracking，并恢复站立。

期望结束文案：

```text
Memory-driven exploration finished: visited 2 goal(s), timed out 0, elapsed 52s.
Stopped because max_goals=2 was reached.
```

#### 10.4.6 长时间探索测试

短时连续探索稳定后，再做长时间测试。长时间测试的目标是验证机器人能持续根据空间记忆覆盖缺口探索未覆盖区域，而不是只补一个附近点。

建议从 10 分钟开始，再扩大到 30 到 60 分钟：

```bash
dimos mcp call patrol_memory_blindspots \
  --arg search_radius_m=8.0 \
  --arg coverage_radius_m=1.0 \
  --arg stale_after_sec=900.0 \
  --arg max_duration_sec=1800.0 \
  --arg goal_timeout_sec=90.0 \
  --arg cooldown_sec=2.0
```

如果现场需要限制目标数量，可以额外传入：

```bash
  --arg max_goals=20
```

自然语言调用示例：

```bash
dimos agent-send "根据空间记忆覆盖情况做长时间探索，去还没有记忆覆盖的区域，最多运行30分钟"
```

期望结果：

- 机器人持续选择空间记忆未覆盖区域或未知边界附近安全观察点。
- 每个导航目标都落在 costmap 的安全 free cell。
- 机器人不会直接把未知栅格或障碍栅格作为 goal。
- 每轮到达后，空间记忆新增该区域附近的图像帧。
- 探索结束原因明确：时间到、用户停止、无目标、连续失败或安全退出。

期望结束文案：

```text
Memory-driven exploration finished: visited 12 goal(s), timed out 1, stuck 0, elapsed 1800s.
Stopped because max_duration_sec reached.
```

#### 10.4.7 覆盖效果复测

连续模式结束后，再运行一次 one-shot 检查：

```bash
dimos mcp call explore_memory_blindspot \
  --arg search_radius_m=3.0 \
  --arg coverage_radius_m=0.8
```

预期：

- 如果附近盲区已补齐，返回 coverage healthy。
- 如果还有盲区，选择新的目标，不应重复选择刚刚补采样的位置。

同时检查日志中新增空间记忆帧是否位于刚才目标附近。

#### 10.4.8 真机验收标准

| 验收项 | 标准 |
|---|---|
| 数据安全 | 缺少 odom/costmap/camera 时不移动 |
| one-shot 行为 | 每次调用最多发送一个 goal |
| 长时间探索 | 能持续根据记忆覆盖缺口选择多个目标 |
| 停止条件 | `max_duration_sec`、可选 `max_goals`、stop command 必须生效 |
| 目标安全 | 可面向未知/未覆盖区域，但导航 goal 必须是 safe free cell |
| 补采样有效 | 到达盲区附近后空间记忆新增帧 |
| 物品清单 | 探索结束后返回本轮新探索区域识别到的物品，并能按最近一次 `exploration_run_id` 再次查询 |
| 不重复选择 | 刚补过的区域短时间内不再被选为盲区 |
| 可中断 | `stop_all_motion` 可停止连续探索 |
| 日志可解释 | 日志包含目标位置、选择原因、停止原因 |

#### 10.4.9 风险和回退

| 风险 | 处理 |
|---|---|
| 机器人长时间运行无法停止 | 检查 `stop_all_motion`、`stop_navigation`、`dimos stop` 是否有效 |
| 机器人持续选新点但不补记忆 | 检查空间记忆是否写入、cooldown 是否生效、coverage_radius 是否过小 |
| 反复选同一个点 | 增加最近访问点 cooldown 或检查新记忆帧是否写入 |
| 目标离障碍物太近 | 增大 obstacle clearance 或使用 inflated costmap |
| 到达后没新增记忆 | 检查 camera 和 spatial memory 是否正常运行 |
| 旧记忆影响判断 | 测试前调用 `clear_all_memory` 或使用 `new_memory=True` |
| 语音/agent 指令解析不稳定 | 使用 MCP 直接调用 skill 并显式传参 |

### 10.5 当前分支前置验证

本功能基于 `feat/semantic-nav-robust-loop` 开发。真机测试新增功能前，先验证当前分支已有的 room / landmark / VLM recognition / semantic navigation 链路是通的。

#### 10.5.1 验证目标

确认当前分支已经可以完成：

```text
tag_room / tag_location
→ 保存房间位置 + 房间参考图
→ VLM 识别当前房间内容
→ 保存 object landmark
→ query_landmarks 查到已有记忆
→ navigate_with_text / navigate_to_landmark 去找已有记忆
```

#### 10.5.2 启动和清空记忆

```bash
dimos run unitree-go2-agentic --robot-ip <机器人IP> --daemon
dimos status
dimos mcp list-tools
dimos agent-send "clear all memory"
```

确认 `mcp list-tools` 中能看到当前分支已有工具，例如：

- `tag_location`
- `tag_room`
- `detect_objects_in_view`
- `query_landmarks`
- `navigate_with_text`
- `navigate_to_landmark`
- `stop_all_motion`

#### 10.5.3 标记房间并触发 VLM 识别

推荐用房间级标记，触发多视角拍摄和异步 VLM 识别：

```bash
dimos agent-send "把这里标记为办公室，拍6张全景照片"
```

或 MCP 方式：

```bash
dimos mcp call tag_room --arg name=办公室 --arg num_photos=6
```

预期：

- 保存一个 ROOM landmark。
- 保存多张 room reference images。
- VLM 异步识别图中物体。
- 识别结果保存为 object landmark，例如 `电脑`、`椅子`、`桌子`。

如果只想识别当前画面：

```bash
dimos mcp call detect_objects_in_view
```

#### 10.5.4 查询已有记忆

```bash
dimos mcp call query_landmarks --arg query=all
dimos mcp call query_landmarks --arg query=rooms
dimos mcp call query_landmarks --arg query=objects
```

预期输出类似：

```text
[room] 办公室 at (1.2, 3.4) · seen 1x
[obj]  电脑 at (1.2, 3.4) · seen 1x
[obj]  椅子 at (1.2, 3.4) · seen 1x
```

如果没有 object landmark：

- 检查 camera 是否有图像。
- 检查 VLM API key 和 provider 是否配置。
- 检查日志里是否有 VLM batch failure。
- 使用 `detect_objects_in_view` 做最小化验证。

#### 10.5.5 导航到已有记忆

优先测试 `navigate_with_text`：

```bash
dimos agent-send "去找电脑"
```

或 MCP：

```bash
dimos mcp call navigate_with_text --arg query=电脑
```

如果想绕过 fallback，只验证 landmark memory：

```bash
dimos mcp call navigate_to_landmark --arg name=电脑
```

预期：

- `navigate_with_text` 优先命中 landmark memory。
- 如果 landmark 没命中，再进入当前画面 VLM、room sweep、VLM memory、CLIP map 等 fallback。
- 机器人向对应记忆位置导航。
- 可用 `stop_all_motion` 中断。

#### 10.5.6 与新增功能的衔接

当前分支前置验证通过后，再测试新增的长时间记忆驱动探索：

```bash
dimos mcp call patrol_memory_blindspots \
  --arg search_radius_m=8.0 \
  --arg coverage_radius_m=1.0 \
  --arg stale_after_sec=900.0 \
  --arg max_duration_sec=1800.0 \
  --arg goal_timeout_sec=90.0 \
  --arg cooldown_sec=2.0
```

新增功能验证重点：

- 是否能持续选择空间记忆未覆盖区域。
- 到达目标后是否新增空间记忆帧。
- 如果开启 `recognize_on_arrival`，是否能低频识别内容并保存 landmark。
- 新增 landmark 是否能被 `query_landmarks` 查到。
- 后续 `navigate_with_text` 是否能使用这些新记忆。

## 11. PR 描述草稿

标题：

```text
feat(memory): add blindspot-driven spatial memory exploration
```

描述：

```text
This change adds a long-running spatial-memory-driven exploration flow. The robot can inspect reachable costmap cells and memory-frontier observation points, compare them against stored spatial memory frame locations, and navigate toward areas with missing or stale memory coverage.

Motivation:
Spatial memory currently records what the robot has seen, but it does not help the robot reason about where memory coverage is missing. For patrol and inspection workflows, missing visual memory in reachable areas reduces the reliability of future semantic search and scene understanding. This feature lets the robot actively improve memory coverage.

This is memory-driven exploration rather than pure frontier exploration: targets are selected from areas with missing or stale spatial memory, including safe observation points near memory-uncovered or unknown boundaries.

Scope:
- Adds spatial memory location listing/query support.
- Fixes location metadata compatibility for pos_x/pos_y.
- Adds a navigation skill for exploring one nearby memory blindspot.
- Adds a long-running patrol skill for exploring spatial-memory-uncovered areas.
- Adds tests for blindspot selection, memory-frontier targets, one-shot behavior, and long-running patrol stop conditions.

Safety:
The MVP only sends one navigation goal at a time, requires odometry and costmap availability, keeps navigation goals on reachable free-space cells, and keeps long-running mode interruptible with stop commands, per-goal timeout, consecutive-failure limits, and max_duration_sec.
```

## 12. 总结

基于当前语义导航分支，新增记忆驱动长时间探索：机器人持续寻找空间记忆未覆盖区域，前往安全观察点补采样，并低频触发 VLM/LLM 识别保存 landmark。

## 13. 当前分支使用方法

本功能基于 `feat/semantic-nav-robust-loop` 分支开发和测试。当前分支推荐使用 DeepSeek 作为 agent LLM，DashScope/Qwen 作为 VLM。

### 13.1 环境变量

```bash
export OPENAI_API_KEY="sk-..."
export OPENAI_BASE_URL="https://api.deepseek.com"

export DIMOS_VLM_PROVIDER="dashscope"
export DASHSCOPE_API_KEY="sk-..."
export DIMOS_VLM_MODEL_NAME="qwen3.6-plus"  # 可选
```

说明：

| 配置 | 用途 |
|---|---|
| `OPENAI_API_KEY` | DeepSeek agent key |
| `OPENAI_BASE_URL` | DeepSeek OpenAI-compatible endpoint |
| `DIMOS_VLM_PROVIDER=dashscope` | 指定视觉模型走 DashScope |
| `DASHSCOPE_API_KEY` | DashScope/Qwen VLM key |
| `DIMOS_VLM_MODEL_NAME` | VLM 模型名，可选 |

### 13.2 启动命令

仿真启动：

```bash
dimos --simulation run unitree-go2-agentic-deepseek --disable security-module
```

真机启动：

```bash
dimos run unitree-go2-agentic-deepseek --robot-ip <机器人IP> --disable security-module
```

确认状态：

```bash
dimos status
dimos mcp list-tools
```

### 13.3 当前分支已有能力测试

清空旧记忆：

```bash
dimos tell '清空所有记忆'
```

标记当前位置：

```bash
dimos tell '标记一下当前是办公室'
```

如果需要房间全景采集，可以说：

```bash
dimos tell '把这里标记为办公室，拍6张全景照片'
```

查询已经保存的记忆：

```bash
dimos tell '列出所有 landmarks'
```

让 Go2 去找已有记忆里的东西：

```bash
dimos tell '去找电脑'
dimos tell '去找椅子'
dimos tell '去找xxx'
```

预期链路：

```text
标记房间/地点
→ 保存当前位置和房间参考图
→ VLM 识别当前画面内容
→ 保存 object landmark
→ query_landmarks 可查到
→ navigate_with_text 去找已有记忆
```

### 13.4 新增功能按同样模式测试

新增功能也通过 `dimos tell` 测试，保持和当前分支使用方式一致。

One-shot 记忆盲区探索：

```bash
dimos tell '检查附近有没有空间记忆盲区，有的话过去探索一下'
```

长时间记忆驱动探索：

```bash
dimos tell '根据空间记忆覆盖情况做长时间探索，去还没有记忆覆盖的区域，最多运行30分钟'
```

更自然的用户指令：

```bash
dimos tell '去探索一下未探索的区域'
```

这句话应该由 agent 映射到 `patrol_memory_blindspots`。预期行为是：机器人进入长时间记忆驱动探索模式，持续寻找空间记忆未覆盖区域或记忆未探索边界，前往安全观察点补采样，并在到达后低频触发 VLM/LLM 识别当前画面内容，保存新的 room/object landmark。

更保守的短时测试：

```bash
dimos tell '在附近3米内连续探索空间记忆盲区，最多去2个点，最多运行60秒'
```

停止探索：

```bash
dimos tell '停止所有运动'
dimos tell '停止导航'
```

查看本轮新探索区域识别到的物品：

```bash
dimos tell '列出刚才新探索区域的物品'
```

### 13.5 `dimos tell` 超时排查

如果测试时看到：

```text
Sending: 检查附近有没有空间记忆盲区，有的话过去探索一下
---
---
(timeout waiting for agent response)
(no response from agent — is an agent module deployed?)
```

这不是空间记忆盲区算法返回的失败结果，而是自然语言 agent 链路没有返回任何消息。`dimos tell` 的链路是：向 `/human_input` 发布文本，然后等待 `/agent` 或 `/agent_idle`。如果超时期间没有收到 agent 输出，就会打印这两行。

常见原因：

- 当前没有运行 DimOS 实例。可先执行 `dimos status` 检查；若显示 `No running DimOS instance`，需要先启动 blueprint。
- 启动的是非 agentic blueprint，里面没有 `McpClient` agent 模块，因此没有模块订阅 `/human_input` 并回复 `/agent`。
- agentic blueprint 还没完成启动，或 `McpClient` 因模型/API key/MCP server 连接失败等原因崩溃或卡住。
- 使用了只包含 `McpServer` 的配置。`McpServer` 只暴露工具；自然语言 `dimos tell` 还需要 `McpClient.blueprint()` 把用户文本映射成工具调用。
- 本次指令被 agent 映射到长时间 `patrol_memory_blindspots`，但 `dimos tell` 的等待时间太短。短时验证建议用更明确的 one-shot 指令，或增加 `--timeout`。

建议验证顺序：

```bash
dimos status
dimos log -n 100
dimos mcp status
dimos mcp list-tools | rg 'explore_memory_blindspot|patrol_memory_blindspots'
dimos tell --timeout 180 '检查附近有没有空间记忆盲区，有的话过去探索一下'
```

如果只想绕过 LLM/agent 路由，直接验证 skill 是否已暴露，可以用 MCP 直接调用：

```bash
dimos mcp call explore_memory_blindspot --arg search_radius_m=5 --arg coverage_radius_m=1
```

如果这个 MCP 直接调用能返回结果，而 `dimos tell` 仍然超时，问题在 agent 输入/输出或模型调用链路，不在 `explore_memory_blindspot` 本身。

预期新增功能链路：

```text
读取空间记忆覆盖
→ 找到记忆未覆盖区域或记忆未探索边界
→ 选择安全 free cell 作为观察点
→ Go2 导航过去
→ 空间记忆保存图像 + 位姿 + embedding
→ VLM/LLM 低频识别内容并保存 landmark
→ 返回本轮新探索区域识别到的物品清单
→ 继续寻找下一个未覆盖区域
```

## 14. 真实修改内容

本次实际落地 commit：

```text
74d78404 feat: add spatial memory blindspot exploration
```

真实修改文件和内容如下：

| 文件 | 修改内容描述 |
|---|---|
| `dimos/perception/spatial_perception.py` | 在 `SpatialMemory` 增加 `get_memory_locations()` RPC，对外返回已存空间记忆帧的位置和时间戳，供记忆盲区判断使用。 |
| `dimos/perception/spatial_memory_spec.py` | 在 `SpatialMemorySpec` 协议中补充 `get_memory_locations()` 方法声明，让导航 skill 可以通过现有 Spec 注入调用空间记忆查询能力。 |
| `dimos/agents_deprecated/memory/spatial_vector_db.py` | 新增 `get_memory_locations()`，从 Chroma image collection 的 metadata 中读取 `frame_id`、`pos_x`、`pos_y`、`pos_z`、`timestamp`；同时修复 `query_by_location()` 和 `get_all_locations()`，统一优先使用 `pos_x/pos_y/pos_z`，兼容旧字段 `x/y/z`，避免两套坐标字段并存。 |
| `dimos/agents/skills/navigation.py` | 在 `NavigationSkillContainer` 中接入现有 `global_costmap` 输入；新增记忆覆盖判断、costmap 安全 cell 过滤、未知边界识别、最近盲区目标选择逻辑；新增 `explore_memory_blindspot` one-shot skill；新增 `patrol_memory_blindspots` 长时间探索 skill；支持 `max_duration_sec`、可选 `max_goals`、单目标超时、连续失败退出、`stop_navigation` / `stop_all_motion` 中断；到达后可复用现有 VLM 识别物品，并为本轮新增 landmark 追加 `source=memory_blindspot_explorer`、`exploration_run_id`、`target_type`、`target_reason`、`target_pose` metadata。 |
| `dimos/agents/system_prompt.py` | 在 Go2 agent 的导航规则中补充工具选择说明：用户要求探索未探索区域、补齐空间记忆、刷新空间记忆时优先调用 `patrol_memory_blindspots`；只检查附近单个盲区时调用 `explore_memory_blindspot`。 |
| `dimos/agents/skills/test_navigation.py` | 增加 `FakeCostmap` 测试输入和空间记忆 stub 方法；新增单元测试覆盖 one-shot 盲区探索会发导航 goal、已有记忆覆盖区域不会重复选、连续探索会按 `max_goals` 停止、障碍物 cell 不会被选为目标；同时给旧 agent 测试补上 `global_costmap` 输入源，适配导航 skill 新增输入。 |

实际验证命令和结果：

```bash
uv run ruff check dimos/agents/system_prompt.py dimos/agents/skills/navigation.py dimos/agents/skills/test_navigation.py dimos/agents_deprecated/memory/spatial_vector_db.py dimos/perception/spatial_perception.py dimos/perception/spatial_memory_spec.py
# All checks passed

uv run pytest dimos/agents/skills/test_navigation.py -q -k 'explore_memory_blindspot or patrol_memory_blindspots or blindspot_goal'
# 4 passed

uv run pytest dimos/agents/skills/test_navigation.py -q -k 'not go_to_semantic_location and not stop_movement and not start_exploration'
# 14 passed
```

说明：曾额外运行包含 `test_go_to_semantic_location` 的旧 agent fixture 测试。日志显示 `navigate_with_text` 已成功调用并返回 “successfully navigated”，但测试等待 `finished_event` 超时，因此未作为本次新增功能的通过项记录。

## 15. 第一次代码评审结果

评审对象：

```text
74d78404 feat: add spatial memory blindspot exploration
```

初版评审结论（已处理）：

功能方向合理，已经补充空间记忆盲区探索 skill、`get_memory_locations()` RPC、测试和设计文档。初版评审指出的运行时边界问题已在当前实现中收敛，下面保留处理记录，方便后续追踪。

### 15.1 长时间巡逻默认值与 RPC 超时

`patrol_memory_blindspots` 当前默认最长运行 `120s`，与普通 RPC 默认超时保持一致。真机长时间探索时可以显式传入更大的 `max_duration_sec`，例如 `1800.0`，但需要注意 MCP/agent 调用侧也要允许更长等待，或者由后续版本改为后台任务加进度汇报。

相关位置：

- `dimos/agents/skills/navigation.py`：`patrol_memory_blindspots(max_duration_sec=120.0)`
- `dimos/protocol/rpc/spec.py`：`DEFAULT_RPC_TIMEOUT = 120.0`

当前处理：

- 如果需要超过默认 RPC 超时时间的真机长时间巡逻，后续应改成后台任务，并通过 tool progress 持续汇报状态。
- 或者为该 RPC 显式配置更长 timeout。
- 当前实现会按剩余 `max_duration_sec` 截断单目标等待，超时、停止命令、异常退出时都会取消正在执行的导航 goal。

### 15.2 导航拒绝或超时后会重复选择同一个失败目标

初版 `patrol_memory_blindspots` 只在成功到达后才把目标加入 `recent_goals`。如果最近的盲区点在 costmap 上看起来安全，但 planner 拒绝或无法到达，循环可能重复选择同一个目标，连续失败 3 次后直接退出，而不是继续尝试下一个候选点。

相关位置：

- `dimos/agents/skills/navigation.py`：`_find_nearest_memory_blindspot(...)`
- `dimos/agents/skills/navigation.py`：`if not self._navigation.set_goal(pose):`
- `dimos/agents/skills/navigation.py`：`if status == "timeout":`

当前处理：

- 对 `set_goal` rejected、timeout 和 stuck 的目标记录到 `recent_goals`。
- 后续调用 `_find_nearest_memory_blindspot()` 时通过 `exclude_recent_goals` 排除这些失败目标，让巡逻能尝试下一个候选盲区。

### 15.3 已验证项目

已运行：

```bash
uv run pytest dimos/agents/skills/test_navigation.py -q
```

结果：

```text
17 passed
```

同时检查：

```bash
ruff check
git diff --check
```

结果均通过。

### 15.4 后续建议

当前轮已修复 RPC 总时长边界和失败目标排除逻辑。后续如果继续增强长时间探索，建议补充以下测试：

- `patrol_memory_blindspots` 在 `set_goal` rejected 后不会重复选择同一个目标。
- `patrol_memory_blindspots` 在单目标 timeout 后会尝试下一个候选目标。
- 长时间巡逻被 `stop_navigation` 或 `stop_all_motion` 中断时，会取消导航并正常返回状态。
