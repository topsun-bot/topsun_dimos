# 空间记忆盲区连续探索优化方案

## 1. 背景

当前 `explore_memory_blindspot` / `patrol_memory_blindspots` 已经具备基础能力：从 costmap 中寻找附近可通行但缺少空间记忆覆盖的点，然后调用导航前往该点补采样。

但现有实现更偏“最近盲点补洞”：

- 在机器人附近逐格采样候选 free cell。
- 对每个候选点单独判断空间记忆是否覆盖。
- 选分数最低的单点作为目标。

这在小范围补采样时可用，但在真实巡检中会出现一个明显问题：

> 如果一条路从入口到尽头都没有走过，那么整条路都是连续空间记忆盲区。机器人不应该只走到最近的盲区边缘，而应该沿着这片连续盲区向深处推进，到达后识别、保存，再继续寻找下一个盲区。

本优化方案的目标是把当前点级盲区探索升级为区域级、连续推进式盲区探索。

## 2. 核心目标

新增或优化空间记忆盲区探索逻辑，使机器人能够：

1. 识别连续空间记忆盲区，而不是只识别单个 grid cell。
2. 在长走廊、未巡视通道、房间深处等场景中，优先选择盲区区域的深处或末端作为目标。
3. 到达目标后等待空间记忆写入，再进行当前视野内容识别。
4. 继续寻找剩余盲区，直到没有安全可达目标、达到时间/目标数上限，或用户停止。

一句话：

> 从“找最近盲点”升级为“沿连续记忆空白区域推进”。

## 3. 当前实现问题

### 3.1 目标太近

当前 `_find_nearest_memory_blindspot()` 以候选点到机器人的直线距离为主要 score。虽然对 `memory_frontier` 和 `missing` 有 bonus，但整体仍偏向最近点。

在一条未巡视走廊中，入口附近的 free cell 会先被选中，机器人可能只前进很短距离，然后下一轮继续重新计算。这会造成探索碎片化。

### 3.2 盲区没有区域概念

当前每个 blind cell 都是独立候选。系统不知道“这些盲点其实属于同一条走廊”。

因此它无法做这些决策：

- 这片盲区面积很大，应优先探索。
- 这片盲区是连续通道，应往深处走。
- 刚刚失败的是这一整片区域，不应马上重试相邻点。

### 3.3 位置半径不等于视觉覆盖

当前空间记忆覆盖主要按“目标点附近是否有历史记忆帧位置”判断。但机器人相机真正覆盖的是前方视野，而不是以机器人位置为圆心的均匀圆。

一个点可能离历史帧位置很近，但从未被相机看见；也可能离历史帧位置稍远，但实际在画面中清晰出现过。

MVP 可以继续用半径近似，但后续应向相机 FOV 覆盖模型升级。

### 3.4 到达后识别时机可能过早

`patrol_memory_blindspots()` 到达目标后会直接调用 `_detect_objects_in_current_view()`。但空间记忆模块可能还没来得及采样并写入新帧。

更稳的流程应该是：

1. 到达目标。
2. 等待机器人姿态和画面稳定。
3. 等待空间记忆新增 frame，或至少等待一个短 settle 时间。
4. 再调用 VLM 识别并保存 landmark。

## 4. 优化设计

### 4.1 从点级候选升级为区域级候选

新增 blind cell mask：

```text
blind_cell = free_and_safe_cell
          && within_search_radius
          && not_recently_failed
          && missing_or_stale_spatial_memory
```

然后对 blind cell mask 做 connected components。

每个 component 表示一片连续空间记忆盲区：

```python skip
@dataclass
class MemoryBlindspotRegion:
    region_id: str
    target_type: Literal["memory_gap", "memory_frontier"]
    reason: Literal["missing", "stale"]
    cells: list[tuple[int, int]]
    area_m2: float
    centroid: tuple[float, float]
    nearest_distance_m: float
    farthest_distance_m: float
    frontier_cell_count: int
    score: float
```

区域类型规则：

- 如果 component 内任意 safe free cell 靠近 unknown cell，则区域可标记为 `memory_frontier`。
- 否则为 `memory_gap`。
- 如果 component 内同时有 missing 和 stale，优先标记为 `missing`。

### 4.2 使用洪水漫灌找连续通路和岔路

对于室内走廊、房间入口和多岔路场景，可以把机器人附近的 safe free cells 看成一张栅格图，从当前机器人所在 free cell 开始做 flood fill / BFS。

目标不是一次性把所有未知区域都走完，而是先构建一个局部“可走空间图”：

```text
robot_cell
→ flood fill reachable safe free cells
→ 标记其中缺少空间记忆覆盖的 cells
→ 提取连续盲区 region
→ 找出 region 内的端点、岔路点、frontier 观察点
→ 按优先级逐个探索
```

#### 4.2.1 Flood fill 输入

Flood fill 只在安全可走 cell 上扩散：

```text
reachable_cell = inside_search_radius
              && costmap cell is free
              && obstacle clearance is enough
              && not unknown
              && not occupied
```

这样可以避免把未知格或障碍格直接作为导航目标。

#### 4.2.2 盲区标记

对 flood fill 得到的 reachable cells，再叠加空间记忆覆盖判断：

```text
blind_reachable_cell = reachable_cell
                    && missing_or_stale_spatial_memory
```

这一步可以得到“机器人当前附近能安全到达、但还没有好好记住”的所有区域。

#### 4.2.3 岔路点检测

在 reachable graph 或 blind graph 上统计每个 cell 的邻居数量：

```text
degree = number of reachable neighbors in 8-neighborhood
```

可用规则：

| 类型 | 判断 | 用途 |
|---|---|---|
| `dead_end` | degree <= 1 | 走廊尽头、房间深处，适合作为推进目标 |
| `corridor` | degree == 2 | 普通通道，沿通道继续推进 |
| `junction` | degree >= 3 | 岔路口，需要记录为待探索分支 |
| `frontier_viewpoint` | safe free cell near unknown | 未知边界观察点 |

实际 costmap 会有噪声，MVP 不需要逐 cell 判断精确拓扑。可以先对 blind region 做轻量规则：

- region 内距离机器人较远的边界 cell 作为端点候选；
- 邻近多个方向 blind/free 子区域的 cell 作为岔路候选；
- 靠近 unknown 的 safe free cell 作为 frontier 观察点。

#### 4.2.4 分支队列

连续探索时维护一个分支队列，而不是每轮只重新找最近点：

```python skip
@dataclass
class MemoryExplorationBranch:
    branch_id: str
    branch_type: Literal["dead_end", "junction", "frontier_viewpoint", "region_deep_point"]
    target_pose: PoseStamped
    region_id: str
    priority: float
    status: Literal["pending", "visiting", "visited", "failed"]
    attempts: int = 0
```

探索流程：

```text
1. flood fill 当前附近安全可走空间
2. 找出所有 blind regions
3. 从 blind regions 中提取 branch targets
4. 与上一轮 visit/failure state 做 region 匹配，继承 cooldown 和失败次数
5. 选最高优先级 branch 导航过去
6. 到达后等待空间记忆写入并识别
7. 重新 flood fill，重新生成候选，不沿用过期地图判断
8. 继续探索下一轮最高优先级候选目标
```

#### 4.2.5 慢慢探索策略

“慢慢探索”建议采用保守队列策略：

- 每轮只发一个导航 goal。
- 到达后停 1 到 3 秒，等待画面稳定。
- 等待空间记忆写入新 frame。
- 识别当前视野物体。
- 重新计算 flood fill 和盲区，不沿用过期地图判断。
- 对失败 branch 做 cooldown，避免原地反复尝试。

这比一次性规划整条路线更稳，因为真实机器人运行时 costmap、障碍物、人和门状态都会变化。

MVP 推荐不要维护长期 pending branch queue。每轮都从最新 costmap 和空间记忆重新生成候选，只保留 visit/failure state。这样可以避免门、人、动态障碍物或 costmap 更新后，继续执行已经过期的 branch。

### 4.3 区域打分

区域排序不应只看最近距离。建议 MVP score：

```text
score =
    path_distance_m * distance_weight
  - min(area_m2, max_area_bonus_m2) * area_weight
  - frontier_bonus
  - corridor_depth_bonus
  + recent_failure_penalty
```

默认建议：

```python skip
distance_weight = 1.0
area_weight = 0.15
max_area_bonus_m2 = 8.0
frontier_bonus = 1.0
corridor_depth_bonus = min(farthest_distance_m, 5.0) * 0.2
recent_failure_penalty = 3.0
```

含义：

- 近的区域仍然优先。
- 大片连续盲区优先于碎片盲点。
- 面积 bonus 必须有上限，避免大 open space 压过距离和安全性。
- 靠近 unknown 的 `memory_frontier` 优先。
- 纵深明显的区域优先。
- 最近失败区域暂时降权。

距离建议优先使用 flood-fill / geodesic 路径距离，而不是机器人到 cell 的直线距离。直线距离会穿墙、穿障碍，容易把实际绕路很远的区域误判为近目标。

### 4.4 区域内目标选择

对选中的 region，不再选择最近 cell，而是在区域内选择更适合作为观察点的 safe free cell。

MVP 推荐策略：

1. 找出 region 内所有 safe free cells。
2. 排除离机器人过近的 cell。
3. 优先选择距离机器人较远、但仍在 `search_radius_m` 内的 cell。
4. 如果是 `memory_frontier`，优先选择靠近 unknown 边界但仍安全的 free cell。
5. 给 goal 设置朝向，使机器人朝向盲区深处或 unknown 边界。
6. 发布 goal 前做 planner 可达性或轻量验证。
7. 如果 goal 离障碍物不足开阔区域要求，但该 region 是可贯通的窄通道，则允许使用通道 clearance；如果是桌底/墙角 pocket，则跳过。

走廊场景下，这会让机器人一次走到更深的位置，而不是停在入口附近。

`safe free cell` 不等于导航一定可达。真机上还需要考虑通道宽度、转身空间、局部规划器限制和动态障碍物。最终发布目标前建议增加一层验证：

- 如果导航模块支持 plan-only / goal validation，先检查目标是否能规划出路径。
- 如果没有 plan-only 接口，至少检查 flood-fill 路径距离、目标附近安全邻域数量和 obstacle clearance。
- 如果目标姿态朝向会让机器人贴墙或原地转身受限，退回到 region 内更开阔的 cell。
- planner 拒绝或 set_goal 失败时，把该 region 记为 `rejected` 并进入 cooldown。

### 4.4.1 窄通道例外和桌底/口袋拒绝

真机探索时，机器人需要继续探索窄通道，但不应为了补空间记忆钻向桌子下方、桌脚旁边、墙边死角或设备架下方。因此不能简单把所有探索目标都限制为 0.5m clearance，而应区分“可贯通通道”和“局部口袋”。

MVP 使用三组阈值：

```python skip
traversal_clearance_m = 0.35
open_goal_clearance_m = 0.5
corridor_goal_clearance_m = 0.35
```

规则：

- blindspot 候选 cell 必须至少满足 `traversal_clearance_m`，否则不进入可走空间图。
- 普通 open-space / room / frontier goal 优先要求 `open_goal_clearance_m = 0.5m`。
- 如果 region 被识别为 `corridor`，允许 goal clearance 降到 `corridor_goal_clearance_m = 0.35m`，以免阻止机器人探索窄通道。
- 如果 region 被识别为 `pocket` / `dead_end_narrow`，即使满足 `0.35m` 也不作为探索推进目标，避免钻桌底、墙角或设备架下方。
- recovery escape goal 仍优先选择更开阔区域，推荐使用 `open_goal_clearance_m`；如果当前已经在窄通道内，可退而使用 `traversal_clearance_m` 找最近可退出点。

MVP corridor 判断可以用轻量几何规则：

```text
region_length_m >= 1.5
region_length_m / max(region_width_m, resolution) >= 2.5
farthest_distance_m >= 0.9
region 内 deep goal 附近沿主方向前后都有 reachable free cells
```

MVP pocket / 桌底风险判断：

```text
obstacle_clearance_m < open_goal_clearance_m
&& not corridor
&& (
    region_area_m2 < 0.8
    || safe_neighbor_count is low
    || farthest_distance_m - nearest_distance_m < 0.8
)
```

这个规则是探索层的目标选择约束，不替代底层避障。底层 planner 仍然负责实际避障；探索层负责“不主动选择容易钻桌底或卡墙角的目标”，同时允许机器人沿可贯通窄通道继续探索。

目标选择示意：

```text
机器人当前位置 R

R . . . . . . . . X
        连续盲区区域

旧逻辑：选择靠近 R 的第一个盲点
新逻辑：选择区域深处 X
```

### 4.5 目标朝向

当前目标 pose 复用机器人当前 orientation。对于 `memory_frontier` 或走廊盲区，这不够。

建议设置目标 yaw：

- `memory_frontier`: 朝向最近 unknown cell 的方向。
- `memory_gap`: 朝向 region centroid 或 farthest cell 的方向。
- 如果无法计算，则退回当前 odom orientation。

这样机器人到达后相机会更可能看到需要补记忆的区域。

### 4.6 到达后等待空间记忆写入

新增参数：

```python skip
arrival_settle_sec: float = 2.0
wait_for_memory_frame_sec: float = 5.0
```

到达目标后的流程：

```text
navigation reached
→ 立即记录 arrival_time 和 memory_frame_count_before
→ sleep(arrival_settle_sec)
→ 等待 get_memory_locations() 中出现新增 frame，或出现 timestamp >= arrival_time 的 frame
→ 如果等不到，也继续执行识别，但返回中标记 memory_frame_confirmed=False
→ detect_objects_in_current_view()
→ 保存 landmark metadata
```

注意不要在 `sleep(arrival_settle_sec)` 之后才记录 `arrival_time`，否则 settle 期间空间记忆已经写入的新帧会被漏掉。实现上优先使用 frame id / frame count 判断是否新增，其次才使用 wall-clock timestamp。timestamp 应使用同一时钟源，避免系统时间跳变导致误判。

保存 metadata 建议增加：

```python skip
{
    "source": "memory_blindspot_explorer",
    "exploration_run_id": run_id,
    "target_region_id": region_id,
    "target_type": target_type,
    "target_reason": reason,
    "target_pose": [x, y, z],
    "memory_frame_confirmed": True,
}
```

### 4.7 连续探索状态管理

当前 `recent_goals` 是点级黑名单。优化后应升级为区域级状态：

```python skip
@dataclass
class MemoryBlindspotVisitState:
    region_id: str
    target_pose: tuple[float, float, float]
    status: Literal["reached", "timeout", "stuck", "rejected"]
    timestamp: float
```

使用方式：

- `reached`: 不需要强行 blacklist，新的空间记忆覆盖后该区域会自然变小或消失。
- `timeout/stuck/rejected`: 对整个 region 临时降权或跳过。
- 如果区域很大，失败后可以尝试同 region 的另一个目标，但需要限制次数。

`region_id` 不能只用 connected component 的列表序号。每轮重新 flood fill 后，component 顺序和形状都可能变化，如果 id 不稳定，失败 cooldown 会失效。MVP 建议使用以下规则生成和匹配 region：

```text
region_fingerprint = quantized_centroid + quantized_bbox + target_type
```

并在每轮重算后做一次近邻匹配：

- centroid 距离小于 `region_match_radius_m`；
- bbox 或 cell overlap 超过阈值时视为同一区域；
- 如果一个旧 region 被拆成多个新 region，把失败状态继承给与旧失败目标最近的新 region；
- 如果多个旧 region 合并，继承最严重的失败状态和最长 cooldown。

推荐参数：

```python skip
region_match_radius_m = 1.0
region_overlap_ratio = 0.25
region_failure_cooldown_sec = 60.0
max_region_attempts = 2
```

### 4.8 视觉覆盖模型后续优化

短期仍可用 `coverage_radius_m` 判断空间记忆覆盖。

中期建议引入相机 FOV coverage：

```text
memory frame pose + yaw + hfov + max_range
→ 投影到 costmap
→ 标记被相机看过的 free cells
```

这样可以区分：

- 机器人走过但没看过的侧方区域。
- 已经在画面中出现过但距离机器人位置较远的区域。
- 走廊尽头、门口、房间入口等需要视觉补采样的位置。

MVP 可先用前向扇形近似：

```python skip
coverage_angle_deg = 90.0
coverage_depth_m = 4.0
```

如果第一版仍只实现半径覆盖，文案和验收应明确这是“位置采样覆盖”，不是严格的“视觉覆盖”。如果演示重点是“机器人没有看过某个方向”，建议把前向扇形近似纳入 MVP，否则机器人走过但没看向侧方的区域可能被误判为已覆盖。

## 5. 建议实现顺序

### Step 1: 保留现有 skill API

不改用户可见接口：

- `explore_memory_blindspot(...)`
- `patrol_memory_blindspots(...)`

内部把 `_find_nearest_memory_blindspot()` 替换或扩展为：

```python skip
def _find_best_memory_blindspot_region(...) -> dict[str, Any] | None:
    ...
```

返回仍包含 `pose`、`target_type`、`reason`，并新增：

```python skip
{
    "region_id": "...",
    "region_fingerprint": "...",
    "region_area_m2": 3.5,
    "region_cell_count": 56,
    "target_selection": "region_deep_point",
    "path_distance_m": 4.2,
    "goal_validation": "validated",
}
```

### Step 2: 实现 blind cell mask

复用现有逻辑：

- `_is_costmap_goal_cell_safe()`
- `_costmap_cell_has_unknown_neighbor()`
- `_memory_coverage_reason()`
- `_memory_locations()`

新增 helper：

```python skip
def _build_memory_blindspot_mask(...) -> np.ndarray:
    ...
```

### Step 3: Connected components

对 mask 做 4-neighbor 或 8-neighbor BFS。

MVP 推荐 8-neighbor，能更自然表示走廊和斜向连通区域。

新增 helper：

```python skip
def _connected_blindspot_regions(mask: np.ndarray) -> list[list[tuple[int, int]]]:
    ...
```

### Step 4: 区域内选目标

新增 helper：

```python skip
def _select_region_goal_cell(region_cells, robot_pose, costmap, target_type) -> tuple[int, int]:
    ...
```

初版策略：

- `memory_frontier`: 选靠近 unknown 且离机器人较远的 safe cell。
- `memory_gap`: 选 region 内离机器人最远但不超过 `search_radius_m` 的 safe cell。
- 最终目标必须通过 planner 可达性或轻量可达性验证。
- 最终目标优先满足 `open_goal_clearance_m = 0.5m`；如果是 corridor region，可使用 `corridor_goal_clearance_m = 0.35m`；如果是 pocket/dead-end narrow region，跳过。

### Step 5: 到达后等待新记忆

新增 helper：

```python skip
def _wait_for_new_memory_frame(
    after_timestamp: float,
    previous_frame_count: int,
    timeout_sec: float,
) -> bool:
    ...
```

`patrol_memory_blindspots()` 到达后：

```python skip
arrival_time = time.time()
previous_frame_count = len(self._memory_locations())
time.sleep(arrival_settle_sec)
memory_frame_confirmed = self._wait_for_new_memory_frame(
    arrival_time,
    previous_frame_count,
    wait_for_memory_frame_sec,
)
```

### Step 6: 卡住和背角落恢复

连续探索必然会遇到窄通道、墙角、死胡同、动态障碍物或导航局部规划失败。优化版不应只取消 goal 后换下一个点，还应该先尝试从卡住位置恢复出来。

#### 6.1 卡住判断

沿用当前 `stuck_timeout_sec` 和 `progress_epsilon_m`，但增加更细的状态：

```text
stuck_no_progress: odom 长时间几乎不动
stuck_goal_oscillation: 在小范围反复震荡
stuck_corner: 当前 free space 邻域很小，靠近障碍/墙角
stuck_rejected: navigation set_goal 或 planner 拒绝
```

MVP 可以先实现 `stuck_no_progress` 和 `stuck_corner`。

#### 6.2 恢复目标选择

当检测到卡住时，先从当前 costmap 做一个小半径 flood fill，找“逃逸点”：

```text
current_cell
→ flood fill safe free cells within recovery_radius_m
→ 过滤掉靠近障碍物和 unknown 的 cell
→ 选择离障碍更远、离当前点 0.8m 到 2.0m 的 cell
→ 优先选择朝向机器人来路或更开阔区域的 cell
```

逃逸点不追求继续探索，只负责让机器人从角落、墙边或局部卡死状态里出来。

如果卡住原因来自局部规划器，单纯再发送一个导航 escape goal 也可能继续失败。因此恢复应分两级：

1. 低层恢复：取消目标、停止底盘、站稳，必要时短距离后退或转向 free-space 方向。
2. 导航恢复：低层恢复后，再选择 recovery escape goal 并通过导航前往。

所有恢复动作都必须可被 `stop_all_motion` / `stop_navigation` / `dimos stop` 打断。`sleep`、等待导航、等待 memory frame、VLM 识别前后都要检查停止标志，避免用户停止后还继续执行下一步。

推荐参数：

```python skip
recovery_radius_m = 2.0
recovery_min_move_m = 0.6
recovery_max_attempts = 3
recovery_goal_timeout_sec = 10.0
recovery_clearance_m = 0.5
```

#### 6.3 恢复动作顺序

卡住后的恢复流程：

```text
1. cancel 当前探索 goal
2. stop_navigation / 停止底盘速度
3. 如果有 Unitree，执行 RecoveryStand
4. 如果支持低层速度控制，先短距离退回或转向 free-space 方向
5. 从当前 costmap flood fill 找 recovery escape goal
6. 导航到 escape goal，等待短 timeout
7. 如果成功移动出卡住点，重新计算盲区和候选 region
8. 如果连续恢复失败，标记当前 region/branch failed 并停止或换区域
```

#### 6.4 背角落时的特殊处理

“背角落”通常表现为：

- 机器人离多个障碍边界很近；
- 周围 safe free cells 很少；
- 当前目标方向被墙/障碍挡住；
- odom 基本不动或局部 planner 反复失败。

此时不要继续往原目标推进。应优先：

1. 选择更开阔的 free cell 作为 escape goal。
2. 如果能控制朝向，先转向 free-space 方向。
3. 退回上一个成功目标附近，或者退回 flood fill 中距离障碍最远的 cell。
4. 把当前 branch 标记为 failed/cooldown，避免马上再次进入同一角落。

#### 6.5 区域失败记录

恢复失败后，记录失败状态：

```python skip
@dataclass
class MemoryExplorationFailure:
    region_id: str
    branch_id: str
    failure_type: Literal["stuck", "timeout", "rejected", "recovery_failed"]
    pose: tuple[float, float, float]
    timestamp: float
    cooldown_until: float
```

后续 flood fill 仍可发现这个区域，但 scoring 应加上 failure penalty，直到 cooldown 结束。

失败记录应绑定稳定 region fingerprint，而不是只绑定当轮 component 序号。否则同一区域在下一轮重新编号后会绕过 cooldown。

#### 6.6 返回文案

探索结束摘要应区分探索失败和恢复失败：

```text
Memory-driven exploration finished: visited 4 goal(s), timed out 1, stuck 2, recovered 1, recovery_failed 1.
Stopped because recovery failed near region memory_frontier_3; the robot avoided retrying that corner.
```

### Step 7: 增加测试

新增测试重点：

1. 长走廊全部缺少记忆时，目标选走廊深处，不选最近边缘。
2. 两片盲区时，大片连续盲区优先于小碎片。
3. `memory_frontier` region 优先靠近 unknown 的 safe cell。
4. 到达后会等待新 memory frame。
5. 新识别 landmark metadata 包含 `target_region_id` 和 `memory_frame_confirmed`。
6. 失败 region 会被降权或跳过。
7. 卡住后会取消原 goal，选择 escape goal 恢复。
8. 背角落恢复失败后，会给当前 branch/region 加 cooldown，不会立即重试。
9. 同一 region 重新编号后仍能继承失败 cooldown。
10. stop 命令能打断 settle sleep、memory frame wait、VLM 识别前检查和恢复流程。

## 6. 推荐参数

```python skip
search_radius_m = 8.0
coverage_radius_m = 1.0
stale_after_sec = 600.0
traversal_clearance_m = 0.35
open_goal_clearance_m = 0.5
corridor_goal_clearance_m = 0.35
min_region_area_m2 = 0.25
min_region_cells = 4
prefer_deep_region_goal = True
arrival_settle_sec = 2.0
wait_for_memory_frame_sec = 5.0
region_failure_cooldown_sec = 60.0
region_match_radius_m = 1.0
region_overlap_ratio = 0.25
max_region_attempts = 2
max_area_bonus_m2 = 8.0
recovery_radius_m = 2.0
recovery_min_move_m = 0.6
recovery_max_attempts = 3
recovery_goal_timeout_sec = 10.0
recovery_clearance_m = 0.5
```

真机走廊测试建议：

```python skip
search_radius_m = 6.0
coverage_radius_m = 0.8
max_duration_sec = 1800.0
goal_timeout_sec = 90.0
stuck_timeout_sec = 15.0
```

## 7. 预期行为

### 场景：一条未巡视走廊

初始状态：

- costmap 已知走廊 free。
- 空间记忆只覆盖机器人当前位置附近。
- 走廊深处没有 memory frame。

优化前：

```text
找到最近盲点 → 前进一小段 → 再找最近盲点 → 继续碎片化前进
```

优化后：

```text
识别整条走廊为一个连续盲区 region
→ 选择走廊深处 safe cell
→ 导航过去
→ 等新空间记忆写入
→ 识别当前画面物体
→ 重新计算剩余盲区
→ 继续向走廊尽头或下一个盲区推进
```

### 用户体验

用户说：

```text
沿着没记住的地方继续探索。
```

机器人应该调用：

```text
patrol_memory_blindspots
```

返回示例：

```text
Memory-driven exploration finished: visited 5 goal(s), timed out 0, stuck 0, elapsed 612s.
Stopped because no reachable memory-uncovered target remained within 8.0m.
New objects found in explored areas:
- memory_frontier@(6.20,-1.40): 椅子, 桌子, 显示器
- memory_gap@(9.10,-1.35): 灭火器, 门牌
```

## 8. 和现有实现的关系

本方案不推翻当前实现，而是在当前 MVP 上替换目标选择策略：

| 当前实现 | 优化后 |
|---|---|
| 单点 blind cell | 连续 blindspot region |
| 最近点优先 | 区域深处/高价值目标优先 |
| 点级 recent_goals | 区域级 visit/failure state |
| 到达后直接识别 | 到达后等待稳定和新 memory frame |
| goal 朝向复用当前 odom | goal 朝向盲区深处或 unknown |
| 只检查 safe cell | 发布前增加 planner 可达性或轻量可达性验证 |
| 可能贴近桌脚/墙角继续推进 | 区分 corridor 和 pocket：窄通道允许 0.35m，桌底/口袋拒绝 |
| 半径覆盖表述为视觉覆盖 | 明确 MVP 是位置采样覆盖，或增加前向扇形 FOV 近似 |

用户可见 skill 名称可以保持不变，避免影响 agent prompt 和现有调用链。

## 9. 验收标准

MVP 优化完成后，应满足：

1. 在模拟长走廊 costmap 中，目标点明显落在盲区深处，而不是机器人附近第一个盲点。
2. 连续探索每轮只发送一个 goal。
3. 到达目标后会等待空间记忆新增 frame 或超时。
4. stop 命令仍能中断 patrol。
5. 障碍物和 unknown cell 不会被直接选为 goal。
6. 失败目标不会在下一轮立即以相邻点形式重复尝试。
7. 返回结果包含访问目标数、停止原因和识别到的新物体摘要。
8. 机器人卡在角落或无进展时，会先尝试 escape goal 恢复；恢复失败后不继续硬闯同一区域。
9. 同一片 region 即使 connected component 重新编号，也会继承 timeout/stuck/rejected cooldown。
10. 到达后 frame 确认不会漏掉 `arrival_settle_sec` 期间写入的新帧。
11. 最终导航目标在发布前经过 planner 可达性或轻量可达性验证。
12. 如果第一版只使用半径覆盖，验收报告应称为“位置采样覆盖”；如果要验收“视觉覆盖”，必须启用前向扇形 FOV 近似。
13. 窄通道仍可探索：corridor region 允许使用 `corridor_goal_clearance_m`；桌底/墙角 pocket 不会被选为探索推进目标。
