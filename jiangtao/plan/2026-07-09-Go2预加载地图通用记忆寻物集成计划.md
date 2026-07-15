# Go2 基于预加载地图的通用记忆寻物集成计划

创建日期：2026-07-09
修订日期：2026-07-09
当前 base 分支：`jtlinux`
目标开发分支：`relocalization-change-local-vlm`
参考实现分支：`origin/feat/change-local-vlm`
目标物体：通用物体（灭火器、垃圾桶、木箱等都只是测试样例，不能写死）
目标目录：`dimos/agents/skills/`, `dimos/perception/`, `dimos/navigation/`, `dimos/mapping/`, `dimos/robot/unitree/go2/blueprints/`

## 1. 目标重新定义

这次不是从零实现“物体记忆”，也不是做一个“灭火器专用”的寻物能力。

真实目标是：

```text
沿用 feat/change-local-vlm 已经实现的通用记忆寻物能力
  + 接入 jtlinux 当前已有的 RelocalizationModule / preloaded map
  + 让历史记忆可以落到同一张预加载地图坐标里
  + 用这张预加载地图做导航、认物体、记住物体、跨房间搜索
```

用户现场流程应该是：

```text
1. 用户带 Go2 到一个房间，例如会议室 A
2. 用户说“这里是会议室 A”或调用 tag_room("会议室A")
3. 机器人原地转一圈，按配置采集多张图（现场可设为 6 张）
4. VLM 对这些图识别可见物体，并把房间、图像、物体、机器人位姿写入记忆
5. 用户把 Go2 带到别处
6. 用户说“去找某个物体”，例如灭火器、垃圾桶、木箱、椅子等
7. 系统先查历史视觉/地标记忆，导航到最可能的位置
8. 到达后原地扫描并视觉确认
9. 如果旧位置没有找到，就遍历已标记房间继续搜索
```

新增的重定位要求是：

```text
保存好的 premap/map
  -> RelocalizationModule 加载
  -> 重定位得到 world <- map
  -> premap 被变换到当前 world
  -> merged_map 作为 CostMapper / planner 的导航地图
  -> 记忆中的房间/物体坐标也通过 map 坐标转换到当前 world
  -> navigate_with_text 在当前 world 下执行导航
```

因此，计划重点不是新增一个 `remember_fire_extinguisher()`，而是把已有 `tag_room()` / `tag_location()` / `navigate_with_text()` 的记忆寻物链路和当前重定位地图链路接起来。

## 2. 当前代码判断

### 2.1 `origin/feat/change-local-vlm` 已有能力

参考分支已经具备这条上层逻辑：

- `NavigationSkillContainer.tag_room(name, num_photos=0)`
  - 用于标记当前房间。
  - 内部调用 `tag_location()`。
  - 默认做 360 度全景采集。
  - 可通过 `num_photos` 或旋转步长控制采图数量；现场可把房间采图配置成 6 张。
- `NavigationSkillContainer.tag_location(location_name, num_photos=-1)`
  - 记录当前位置。
  - 转一圈采集多张图。
  - 每张图送 VLM 做物体识别。
  - 把房间参考图、检测到的物体、角度和位姿写入记忆。
- `NavigationSkillContainer.navigate_with_text(query)`
  - 默认 fallback 是 `object_room`：
    - 先查物体地标记忆。
    - 命中后导航到记忆位置。
    - 到达后做 360 度扫描确认。
    - 没找到则进入 `room_sweep`，逐个已记录房间搜索。
- `SpatialLandmarkMemory`
  - 使用 `SpatialRecord` 存 `ROOM`、`LANDMARK`、`DOOR`。
  - 持久化到 `landmarks.json`。
  - 支持 `resolve_by_query()` 和 `query_by_type()`。
- `TopologyGraph`
  - 可基于记录的空间点组织粗粒度 waypoint。
- `unitree_go2_spatial`
  - 已接入 `SpatialMemory`、`SpatialLandmarkMemoryModule`、`ObjectTracker2D`、`BBoxNavigationModule` 等模块。

这说明“到房间标记、转圈采图、VLM 识别、之后按物体名称查记忆并导航过去”已经是参考分支的核心能力。实现时应该尽量保留它，而不是重新发明一套物体记忆框架。

### 2.2 当前 `jtlinux` 已有能力

当前分支的关键优势是重定位和地图加载：

- `RelocalizationModule` 可以通过 `map_file` 加载保存好的 premap。
- premap 被统一到 `map` 坐标系。
- 当前观测的 `global_map` 在 `world` 坐标系。
- 重定位算法输出 `map <- world`，模块发布 `world <- map`。
- `_on_merge_input()` 使用 `world <- map` 把 premap 变换到当前 `world`。
- 输出 `merged_map`，下游导航可以基于加载后的地图规划。
- cache 里已经有 `map_key` 设计，用于区分不同预加载地图。

当前分支的缺口是：

- 没有参考分支那套通用 `SpatialLandmarkMemory` / `room_sweep` 记忆寻物闭环。
- agentic 寻物栈和 relocalization 栈还没有合并成一个面向现场测试的蓝图。
- 记忆记录还没有绑定 `map_key` 和 `pose_map`，跨启动或重定位后容易只停留在旧 `world` 坐标。

## 3. 总体结论

可以联系起来，而且联系点非常明确：

```text
SpatialMemory / SpatialLandmarkMemory 负责“我见过什么、在哪个房间、从哪张图看到”
RelocalizationModule 负责“当前 world 和预加载 map 的关系”
NavigationSkillContainer 负责“把自然语言目标变成房间/物体搜索和导航动作”
```

真正要补的是坐标持久化：

```text
参考分支已有：
  物体名/房间名 -> 历史 SpatialRecord -> world position

需要新增：
  物体名/房间名 -> 历史 SpatialRecord -> map_key + pose_map
  当前启动时：
    pose_map + 当前 world<-map -> pose_world_now
```

运行时仍然把目标交给当前 `world` 坐标下的 planner。也就是说，预加载地图只是稳定的长期坐标锚点，真正导航目标仍然是当前运行时的 `world` pose。

## 4. 分支策略：先直接 merge，再人工收敛冲突

上一版计划写成“选择性移植”，容易给人一种要绕开参考分支已有实现的感觉。根据当前代码判断，新的策略应改为：

```text
以当前 jtlinux 为 base
  -> 新建 relocalization-change-local-vlm
  -> 直接 merge origin/feat/change-local-vlm
  -> 保留参考分支的通用记忆寻物主逻辑
  -> 人工解决冲突，确保 jtlinux 的重定位模块不被破坏
  -> 在合并后的代码上补 map_key / pose_map / relocalization-ready 接入
```

建议命令：

```bash
git switch jtlinux
git fetch origin feat/change-local-vlm
git switch -c relocalization-change-local-vlm
git merge --no-ff origin/feat/change-local-vlm
```

如果目标分支已经存在：

```bash
git switch relocalization-change-local-vlm
git merge --no-ff origin/feat/change-local-vlm
```

### 4.1 为什么这次优先直接 merge

因为参考分支已经有完整的行为闭环：

- room tag
- panoramic capture
- VLM object extraction
- landmark memory
- object landmark fallback
- room sweep
- visual scan confirm
- bbox navigation / servo 接线

这些逻辑互相耦合在 `NavigationSkillContainer`、`SpatialLandmarkMemory`、`SpatialRecord`、blueprint 接线和 prompt/tool schema 里。直接 merge 更容易保留它们的行为一致性。

### 4.2 直接 merge 的主要问题

直接 merge 不是没有风险。当前预检查显示至少这些区域会产生冲突或需要人工判断：

- `dimos/agents/skills/navigation.py`
  - 参考分支是大幅增强版。
  - 当前分支可能有本地 navigation 改动。
  - 处理原则：以参考分支的通用记忆寻物主逻辑为主，再补重定位坐标转换。
- `dimos/perception/spatial_perception.py`
  - 两边都改过。
  - 处理原则：保留参考分支的图像/记忆接口，同时不能破坏当前分支对 SpatialMemory 的运行方式。
- `dimos/robot/unitree/go2/blueprints/smart/unitree_go2_spatial.py`
  - 参考分支接入了 `SpatialLandmarkMemoryModule`、`ObjectTracker2D`、`BBoxNavigationModule`。
  - 当前分支接近原始 spatial stack。
  - 处理原则：保留参考分支的 memory stack，再新增一个 relocalization + memory 的组合蓝图。
- 配置文件、依赖和 lint ignore 可能也有冲突。
  - 处理原则：优先保证 `dimos run`、blueprint registry、pytest 能跑通。

### 4.3 不建议做的事

- 不新增 `FireExtinguisherMemorySkillContainer`。
- 不把“灭火器”写进蓝图名、skill 名或核心 schema。
- 不把 `navigate_with_text("灭火器")` 做成特例。
- 不绕开参考分支已有 `tag_room()` / `room_sweep()`，重新做一套并行流程。

## 5. 坐标接入设计

### 5.1 记录写入时

当 `tag_room()`、`tag_location()` 或 VLM 检测到物体并写入 `SpatialRecord` 时，仍保留原来的 `position` 字段，兼容参考分支已有逻辑。

同时在 `metadata` 里新增地图绑定信息：

```json
{
  "map_key": "go2_hongkong_office_twopass_map",
  "map_file": "go2_hongkong_office_twopass_map.pc2.lcm",
  "frame": "map",
  "pose_map": {
    "position": [1.23, 4.56, 0.0],
    "rotation": [0.0, 0.0, 1.57]
  },
  "pose_world_observed": {
    "position": [2.34, 5.67, 0.0],
    "rotation": [0.0, 0.0, 1.60]
  },
  "T_world_map_at_observation": [[...]],
  "room_name": "会议室A",
  "observation_source": "tag_room_panorama"
}
```

转换关系：

```text
pose_world_observed = 当前机器人 world 位姿
T_world_map = 当前 RelocalizationModule 发布的 world<-map
T_map_world = inverse(T_world_map)
pose_map = T_map_world * pose_world_observed
```

如果当前没有可用 `world<-map`：

- 可以继续写入原来的 `world position`，用于单 session 测试。
- 但记录应标记为 `metadata.relocalization_bound=false`。
- 跨启动地图导航时，不使用这类未绑定地图的长期记录。

### 5.2 记录读取时

当用户调用：

```text
navigate_with_text("任意目标物体")
```

查询逻辑应该是：

```text
1. SpatialLandmarkMemory.resolve_by_query(query)
2. 过滤 metadata.map_key == 当前 RelocalizationModule.map_key 的记录
3. 取 record.metadata.pose_map
4. 用当前 T_world_map_now 转成 pose_world_now
5. 把 pose_world_now 交给现有导航目标接口
6. 到达后执行现有 360 度视觉确认
```

如果没有 `pose_map` 或 `map_key` 不匹配：

- 同 session 内可以 fallback 到原 `record.position`。
- 跨 session 或明确启用 relocalization/map mode 时，不应盲目使用旧 `world` 坐标。
- 可以返回提示：“该物体记忆没有绑定当前地图，需要重新 tag_room 或重新扫描。”

### 5.3 room_sweep 时

`room_sweep` 仍使用参考分支已有逻辑，但房间锚点也需要同样转换：

```text
ROOM record.pose_map
  -> 当前 T_world_map_now
  -> room_pose_world_now
  -> 导航到房间锚点
  -> 原地扫描目标物体
```

这样当物体从 A 房间移动到 B 房间时，流程就是：

```text
1. 查询目标物体，找到历史 LANDMARK 记录
2. 导航到旧位置，例如会议室 A
3. 360 度扫描，没有视觉确认
4. 标记旧 LANDMARK 为 stale 或降低 confidence
5. 遍历 ROOM 记录
6. 到会议室 B 扫描时识别到目标
7. 更新该目标物体的最新 room_name / pose_map / confidence / last_seen
```

## 6. 蓝图集成计划

### 6.1 保留参考分支的 spatial memory stack

merge 后，`unitree_go2_spatial` 应保留参考分支的结构：

```python
unitree_go2_spatial = autoconnect(
    unitree_go2,
    SpatialMemory.blueprint(new_memory=global_config.new_memory),
    SpatialLandmarkMemoryModule.blueprint(),
    ObjectTracker2D.blueprint(frame_id="camera_link"),
    BBoxNavigationModule.blueprint(),
    PerceiveLoopSkill.blueprint(),
    SecurityModule.blueprint(camera_info=GO2Connection.camera_info_static),
)
```

### 6.2 新增 relocalization + memory 组合蓝图

建议新增一个独立蓝图，避免直接改变已有 `unitree_go2_spatial` 的默认行为：

```python
unitree_go2_relocalization_memory = autoconnect(
    unitree_go2,
    RelocalizationModule.blueprint(),
    SpatialMemory.blueprint(new_memory=global_config.new_memory),
    SpatialLandmarkMemoryModule.blueprint(),
    ObjectTracker2D.blueprint(frame_id="camera_link"),
    BBoxNavigationModule.blueprint(),
    PerceiveLoopSkill.blueprint(),
    SecurityModule.blueprint(camera_info=GO2Connection.camera_info_static),
)
```

如果要接 agent/MCP，再新增 agentic 版本：

```python
unitree_go2_relocalization_memory_agentic = autoconnect(
    unitree_go2_relocalization_memory,
    McpServer.blueprint(),
    McpClient.blueprint(),
    _common_agentic,
)
```

推荐注册名：

```text
unitree-go2-relocalization-memory
unitree-go2-relocalization-memory-agentic
```

不要命名成 `fire-extinguisher`，因为目标物体必须是通用查询。

新增或改名蓝图后运行：

```bash
uv run pytest dimos/robot/test_all_blueprints_generation.py
```

## 7. 运行流程设计

### 7.1 建图/预加载地图准备

已有流程保持不变：

```text
离线保存 premap
  -> 配置 relocalizationmodule.map_file
  -> 启动组合蓝图
  -> RelocalizationModule 加载 premap
  -> 等待第一次重定位成功
  -> merged_map 开始输出
```

示例：

```bash
dimos run unitree-go2-relocalization-memory-agentic \
  --robot-ip 192.168.123.161 \
  -o relocalizationmodule.map_file=go2_hongkong_office_twopass_map \
  -o relocalizationmodule.subsequent_relocalization_mode=global
```

第一阶段建议先用 `global`，不要急着打开 same-run fast ICP。等寻物链路稳定后，再评估 fast ICP 对地图/坐标稳定性的影响。

### 7.2 房间标记

用户带 Go2 到会议室 A 后：

```text
tag_room("会议室A", num_photos=6)
```

预期行为：

```text
1. 记录 ROOM: 会议室A
2. 原地转一圈采集 6 张左右参考图
3. VLM 识别每张图里的可命名物体
4. 对识别到的物体写入 LANDMARK
5. 每条 ROOM/LANDMARK 记录都补 metadata.map_key 和 metadata.pose_map
```

这里识别到什么就记什么，不预设必须是灭火器。

### 7.3 按物体名称寻找

用户之后说：

```text
navigate_with_text("灭火器")
navigate_with_text("垃圾桶")
navigate_with_text("木箱")
navigate_with_text("饮水机")
```

都应该走同一套通用逻辑：

```text
query
  -> landmark memory
  -> map_key 过滤
  -> pose_map 转当前 pose_world
  -> 导航到候选位置
  -> 360 度视觉确认
  -> 找到则更新 last_seen / pose_map / confidence
  -> 没找到则进入 room_sweep
```

### 7.4 物体被移动后的搜索

以“目标物体从 A 房间移动到 B 房间”为验收场景：

```text
1. 目标物体原来在会议室 A，被记成 LANDMARK
2. 用户把目标物体搬到会议室 B
3. 用户下达 navigate_with_text("目标物体名称")
4. Go2 先到会议室 A 的历史候选点
5. 视觉确认失败
6. 系统不返回成功
7. 进入 room_sweep，按 ROOM 记录逐房间搜索
8. 在会议室 B 扫描到目标物体
9. 更新目标物体最新位置为会议室 B
```

这部分主要复用参考分支已有 `room_sweep` 和 room scan，不要写成目标物体专用逻辑。

## 8. 实施阶段

### 阶段 0：建立分支并直接 merge

目标：把参考分支的通用记忆寻物框架完整带进来。

步骤：

```bash
git switch jtlinux
git fetch origin feat/change-local-vlm
git switch -c relocalization-change-local-vlm
git merge --no-ff origin/feat/change-local-vlm
```

冲突解决原则：

- `navigation.py`：以参考分支增强版为主，后续补重定位坐标转换。
- `spatial_perception.py`：保留参考分支需要的图像/记忆接口，同时保留当前分支能跑的基础行为。
- `unitree_go2_spatial.py`：保留参考分支 memory stack。
- `dimos/mapping/relocalization/`：保留当前分支版本，不被参考分支覆盖。

完成标准：

```bash
uv run python -m compileall dimos/agents/skills dimos/perception dimos/navigation dimos/mapping
uv run pytest dimos/robot/test_all_blueprints_generation.py
```

### 阶段 1：验证参考分支寻物能力在合并后仍可用

目标：先确认已有功能没被 merge 破坏。

验证项：

- `dimos mcp list-tools` 能看到 `tag_room`、`tag_location`、`navigate_with_text`、`detect_objects_in_view`。
- `tag_room("会议室A", num_photos=6)` 能触发全景采图。
- VLM 识别结果能写入 `SpatialLandmarkMemory`。
- `query_landmarks` 能看到 ROOM 和 LANDMARK。
- 单 session 内 `navigate_with_text("任意已识别物体")` 能导航到记录位置并扫描确认。

这一步暂时不强依赖 relocalization，目的是确认上层记忆寻物框架完整。

### 阶段 2：暴露 Relocalization 状态给寻物逻辑

目标：让 `NavigationSkillContainer` 或 `SpatialLandmarkMemoryModule` 可以拿到当前地图状态。

建议新增一个 spec，例如：

```python
class RelocalizationStateSpec(Spec, Protocol):
    def get_current_map_key(self) -> str | None: ...
    def get_current_map_file(self) -> str | None: ...
    def get_world_to_map(self) -> Transform | None: ...
    def is_relocalized(self) -> bool: ...
```

在 `RelocalizationModule` 增加 RPC：

- `get_current_map_key()`
- `get_current_map_file()`
- `get_world_to_map()`
- `is_relocalized()`

注意：

- 只读状态，不改变重定位主流程。
- 没有 `map_file` 或还没成功重定位时，返回 `None/False`。
- 不让 memory 模块直接依赖 relocalization 内部私有字段。

### 阶段 3：写入 `pose_map`

目标：房间和物体记录都绑定当前预加载地图。

改动点：

- 在 `tag_location()` / `tag_room()` 写 ROOM 记录时补 `metadata.map_key` 和 `metadata.pose_map`。
- 在 `_store_detected_objects()` 写 LANDMARK 记录时补同样字段。
- 如果记录里有物体相对画面中心的 bearing，也继续保留，方便到达房间后先朝向历史方向扫描。

完成标准：

- `landmarks.json` 中新记录包含 `metadata.map_key`。
- `landmarks.json` 中新记录包含 `metadata.pose_map`。
- 没有 relocalization 时，老逻辑仍能单 session 使用。

### 阶段 4：读取时把 `pose_map` 转回当前 `world`

目标：历史记忆跨启动可用。

改动点：

- `navigate_to_landmark()` 或其下层目标解析逻辑：
  - 优先用 `record.metadata.pose_map`。
  - 校验 `record.metadata.map_key == current_map_key`。
  - 使用当前 `T_world_map` 转成 `pose_world_now`。
  - 把 `pose_world_now` 交给 planner。
- `room_sweep` 遍历 ROOM 时同样转换。
- 如果 `map_key` 不匹配，跳过该记录或明确提示。

完成标准：

- 机器人重启后，只要加载同一张 map 并重定位成功，历史 ROOM/LANDMARK 仍可导航。
- 旧 `world` 坐标不会被误当成当前 `world` 使用。

### 阶段 5：新增组合蓝图

目标：给现场测试一个清晰入口。

新增：

```text
unitree-go2-relocalization-memory
unitree-go2-relocalization-memory-agentic
```

这个蓝图应包含：

- `unitree_go2`
- `RelocalizationModule`
- `SpatialMemory`
- `SpatialLandmarkMemoryModule`
- `ObjectTracker2D`
- `BBoxNavigationModule`
- `PerceiveLoopSkill`
- `SecurityModule`
- agentic 版本额外包含 `McpServer` / `McpClient` / `_common_agentic`

完成标准：

```bash
dimos list | rg "unitree-go2-relocalization-memory"
uv run pytest dimos/robot/test_all_blueprints_generation.py
```

### 阶段 6：现场验收

#### 验收 A：同房间历史记忆找物体

```text
1. 启动 relocalization-memory-agentic 蓝图并加载预地图
2. 等待 relocalization 成功
3. 带 Go2 到会议室 A
4. tag_room("会议室A", num_photos=6)
5. 确认 VLM 识别到若干物体
6. 把 Go2 带到别处
7. navigate_with_text("其中一个已识别物体")
8. Go2 导航到历史位置并视觉确认
```

#### 验收 B：物体从 A 移到 B 后跨房间搜索

```text
1. 已标记会议室 A 和会议室 B
2. 目标物体最初在会议室 A 被识别和记录
3. 手动把目标物体移动到会议室 B
4. navigate_with_text("目标物体名称")
5. Go2 先去 A 的历史位置
6. A 没找到后进入 room_sweep
7. 在 B 扫描到目标
8. 更新目标物体最新记录到 B
```

#### 验收 C：重启后基于预加载地图找物体

```text
1. 第一次运行完成 tag_room 和物体记忆
2. 停止进程
3. 第二次运行加载同一 map_file
4. RelocalizationModule 重定位成功
5. navigate_with_text("目标物体名称")
6. 系统使用 pose_map -> 当前 world 的转换导航
7. 成功到达并视觉确认
```

## 9. 关键风险

### 9.1 merge 冲突规模

直接 merge 是正确主线，但冲突不会少。处理方式是先保证参考分支上层寻物能力完整，再把当前分支重定位能力接回去。

如果直接 merge 后冲突超过可控范围，可以退回到选择性移植，但那应该是 fallback，不是首选方案。

### 9.2 fast ICP 对长期记忆的影响

当前重定位有 same-run fast ICP / cache 逻辑。对于记忆寻物第一阶段，建议先使用更保守的全局重定位模式：

```bash
-o relocalizationmodule.subsequent_relocalization_mode=global
```

等 `pose_map` 记录、读取和导航闭环稳定后，再打开 fast ICP 做效率优化。

### 9.3 VLM 识别不稳定

VLM 对同一个物体可能输出不同名称。参考分支已有 query expansion，但不能只靠硬编码同义词。

建议：

- 保存原始中文名和英文名。
- 保存 VLM description。
- 保存来源房间和图片。
- 查询时先用精确/同义词，再用文本相似度或 LLM 判断。

### 9.4 物体移动后的旧记忆处理

旧位置未找到时，不要立即删除记录。建议：

- 降低 confidence。
- 增加 `metadata.stale=true` 或 `last_missed_at`。
- 在新房间找到后再更新最新 pose。
- 保留历史 observation，便于调试。

## 10. 最小可交付版本

第一版最小闭环：

```text
1. 直接 merge feat/change-local-vlm
2. 合并后 tag_room / navigate_with_text 单 session 可用
3. 新增 relocalization 状态 RPC
4. ROOM 和 LANDMARK 写入 map_key + pose_map
5. navigate_with_text 读取时 pose_map -> 当前 world
6. 新增 unitree-go2-relocalization-memory-agentic 蓝图
7. 完成 A/B/C 三组验收
```

不在第一版做：

- 不做灭火器专用 skill。
- 不做复杂多楼层地图。
- 不做自动探索全楼层。
- 不重写 VLM 物体识别。
- 不改底层 planner 的核心算法。

## 11. 一句话版本

这次应该直接把 `feat/change-local-vlm` 的通用记忆寻物能力合进来，把它保留下来作为主流程；我们只在它写入和读取空间记录时补上 `map_key + pose_map`，再用当前分支的 `RelocalizationModule` 把历史地图坐标转换到当前 `world`，让 Go2 可以基于预加载地图去找任意曾经识别过的物体。
