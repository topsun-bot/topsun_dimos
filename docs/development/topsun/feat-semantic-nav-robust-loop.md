# feat/semantic-nav-robust-loop — DimOS 框架改动总结

> 对比基准：`main`  
> 范围：`dimos/` 包内模块与运行时入口；不含 CI/workflow、独立测试脚本、ai_longrun_harness 等。

## 一、功能概览

本分支在 DimOS 框架内实现 **语义导航鲁棒闭环**：地标记忆（JSON + ChromaDB）、拓扑 waypoint 规划、VLM/CLIP 多层 fallback 检索、SLAM 漂移检测与视觉校正，并扩展 Agent blueprint 与 CLI 入口以支持 Go2/G1 真机与仿真部署。

---

## 二、新增模块

| 路径 | 类型 | 说明 |
|------|------|------|
| `dimos/types/spatial_record.py` | 数据模型 | 统一空间记录 `SpatialRecord`（ROOM / LANDMARK / UNKNOWN） |
| `dimos/types/door_record.py` | 数据模型 | `LandmarkRecord` 别名，兼容旧 door 命名 |
| `dimos/types/door_memory_spec.py` | Spec 协议 | `SpatialLandmarkMemorySpec`，供 Skill 注入地标记忆 RPC |
| `dimos/navigation/topology.py` | 导航 | `TopologyGraph`：地标图 + A* 中间 waypoint |
| `dimos/perception/detection/door/door_spatial_memory.py` | 感知/记忆 | `SpatialLandmarkMemory`：内存 dict + JSON 持久化 |
| `dimos/perception/detection/door/door_spatial_memory_module.py` | Module | `SpatialLandmarkMemoryModule`：地标 CRUD RPC 封装 |
| `dimos/perception/detection/door/door_detector.py` | 感知 | YOLO + CLIP 门检测 pipeline（可选组件） |
| `dimos/models/vl/dashscope.py` | 模型 | 百炼原生 MultiModalConversation VLM 后端 |
| `dimos/agents/qwen_mlx_agent.py` | Agent 配置 | 本地 Qwen MLX OpenAI 兼容服务 URL/模型名 helper |
| `dimos/agents/skills/orbit_object.py` | Skill Module | `OrbitObjectSkillContainer`：沿 costmap 边缘绕物体行走 |
| `dimos/robot/cli/tell.py` | CLI 逻辑 | `tell_robot()`：LCM `/human_input` 同步对话 |
| `dimos/utils/cli/tell_robot.py` | CLI 入口 | 独立命令 `tell-robot` |
| `dimos/robot/unitree/go2/blueprints/agentic/unitree_go2_agentic_deepseek.py` | Blueprint | Go2 + DeepSeek V4 Pro Agent |
| `dimos/robot/unitree/go2/blueprints/agentic/unitree_go2_agentic_qwen_mlx.py` | Blueprint | Go2 + 本地 Qwen MLX Agent |
| `dimos/robot/unitree/g1/blueprints/agentic/unitree_g1_agentic_deepseek.py` | Blueprint | G1 真机 + DeepSeek Agent |
| `dimos/robot/unitree/g1/blueprints/agentic/unitree_g1_agentic_sim_deepseek.py` | Blueprint | G1 仿真 + DeepSeek Agent |

---

## 三、修改模块（按框架层次）

### 3.1 Agent 层（`dimos/agents/`）

| 文件 | 改动要点 |
|------|----------|
| `skills/navigation.py` | **核心变更**：扩展 `NavigationSkillContainer`，合并原 door/semantic 导航逻辑；六层 `navigate_with_text` fallback；VLM 批量物体检测；拓扑 + 漂移校正；新增/增强多个 `@skill`（见下文） |
| `system_prompt.py` | Agent 导航流程指引：优先 `navigate_with_text`、中文地标名、`stop_all_motion` 等 |
| `mcp/mcp_client.py` | 支持 `model_provider` / `model_kwargs`；`supports_vision=False` 跳过图像 artefact；socks 代理兼容 |

### 3.2 感知与记忆（`dimos/perception/`）

| 文件 | 改动要点 |
|------|----------|
| `spatial_perception.py` | `SpatialMemory` 大幅增强：房间参考图 CLIP 集合、`query_location_by_image`、`clear_all`、`new_memory`、ChromaDB 恢复、帧 FIFO 上限 |
| `spatial_memory_spec.py` | 扩展 Protocol：`query_location_by_image`、`get_room_images`、`clear_all` 等 |
| `object_tracker_2d.py` | 配合 in-frame VLM bbox 跟踪导航的小幅调整 |
| `experimental/temporal_memory/temporal_memory.py` | 与空间记忆边界相关的 minor 更新 |

### 3.3 导航（`dimos/navigation/`）

| 文件 | 改动要点 |
|------|----------|
| `visual/query.py` | VLM bbox 解析增强：多格式 JSON、中英文 label 匹配、坐标缩放 |
| `bbox_navigation.py` | 与 spatial blueprint remapping 配合的 minor 调整 |
| `replanning_a_star/global_planner.py` | 局部规划/重规划行为微调 |
| `replanning_a_star/local_planner.py` | 路径跟踪与障碍触发 replan 相关调整 |

### 3.4 模型（`dimos/models/`）

| 文件 | 改动要点 |
|------|----------|
| `vl/openai.py` | VLM 配置与调用路径扩展，配合 NavigationSkillContainer 多 provider |
| `vl/dashscope.py` | **新增**（见上） |

### 3.5 机器人 Blueprint（`dimos/robot/`）

| 文件 | 改动要点 |
|------|----------|
| `all_blueprints.py` | 注册 4 个新 blueprint 名 + `orbit-object-skill-container` |
| `unitree/go2/blueprints/smart/unitree_go2_spatial.py` | 挂载 `SpatialLandmarkMemoryModule`、`ObjectTracker2D`、`BBoxNavigationModule`；`new_memory` 配置 |
| `unitree/go2/blueprints/agentic/_common_agentic.py` | 挂载 `OrbitObjectSkillContainer` |
| `unitree/go2/blueprints/basic/unitree_go2_basic.py` | 基础栈 minor 调整 |
| `unitree/g1/blueprints/basic/unitree_g1_basic_sim.py` | 仿真栈扩展（costmap / 导航相关 wiring） |
| `unitree/g1/blueprints/perceptive/_perception_and_memory.py` | 感知记忆模块 wiring 更新 |

### 3.6 CLI（`dimos/robot/cli/`、`dimos/utils/cli/`）

| 文件 | 改动要点 |
|------|----------|
| `robot/cli/dimos.py` | 新增子命令 `dimos tell` |
| `robot/cli/tell.py` | **新增** LCM 对话实现 |
| `utils/cli/tell_robot.py` | **新增** 独立入口脚本 |

### 3.7 基础设施与其它

| 文件 | 改动要点 |
|------|----------|
| `agents_deprecated/memory/spatial_vector_db.py` | 帧 FIFO 驱逐、location 去重、启动 sync |
| `agents_deprecated/memory/image_embedding.py` | CLIP ONNX 本地 processor 加载优化 |
| `agents_deprecated/memory/visual_memory.py` | 配合帧驱逐的 minor 更新 |
| `core/global_config.py` | `rerun_save` / `rerun_save_dir` 配置项 |
| `utils/generic.py` | `extract_json_from_llm_response` 支持 JSON array |
| `visualization/rerun/bridge.py` | rerun 保存相关支持 |
| `stream/audio/tts/node_openai.py` | minor 调整 |

### 3.8 依赖（`pyproject.toml`）

核心新增运行时依赖（与 Agent / VLM / 记忆相关）：

- `langchain*`、`langgraph`、`chromadb`、`dashscope`、`httpx`、`transformers`、`ultralytics` 等

新增 console scripts：

- `tell-robot` → `dimos.utils.cli.tell_robot:main`
- `dtop-plot` → `dimos.utils.cli.dtop_plot:main`（调试辅助，非语义导航主路径）

---

## 四、运行时入口

### 4.1 Blueprint 入口（`dimos run <name>`）

| Blueprint 名 | 说明 |
|--------------|------|
| `unitree-go2-spatial` | Go2 感知 + 空间/地标记忆 + bbox 导航（无 LLM Agent） |
| `unitree-go2-agentic-deepseek` | Go2 spatial 栈 + DeepSeek Agent + 全套 Skill |
| `unitree-go2-agentic-qwen-mlx` | Go2 spatial 栈 + 本地 Qwen MLX Agent |
| `unitree-g1-agentic-deepseek` | G1 真机 + DeepSeek + NavigationSkill |
| `unitree-g1-agentic-sim-deepseek` | G1 仿真 + DeepSeek + NavigationSkill |

Agent 栈公共组成（Go2 `_common_agentic`）：

```
NavigationSkillContainer
PersonFollowSkillContainer
UnitreeSkillContainer
OrbitObjectSkillContainer   ← 本分支新增
WebInput
SpeakSkill
McpServer + McpClient
```

Spatial 栈在 `unitree_go2` 基础上新增：

```
SpatialMemory
SpatialLandmarkMemoryModule   ← 本分支新增
ObjectTracker2D               ← 本分支新增
BBoxNavigationModule          ← 本分支新增
PerceiveLoopSkill
SecurityModule
```

### 4.2 CLI 入口

| 命令 | 模块 | 作用 |
|------|------|------|
| `dimos run <blueprint>` | `dimos.robot.cli.dimos` | 启动完整 Module 协调栈 |
| `dimos tell "..."` | `dimos.robot.cli.tell` | 向运行中 Agent 发自然语言并等待回复 |
| `tell-robot "..."` | `dimos.utils.cli.tell_robot` | 同上，独立可执行入口 |

### 4.3 Module 级 RPC 入口（Blueprint 内自动启动）

| Module | 关键 RPC / 能力 |
|--------|-----------------|
| `SpatialLandmarkMemoryModule` | `record`、`resolve_by_query`、`get_all`、`clear_all`、`save`/`load` |
| `SpatialMemory` | `tag_location`、`tag_location_with_image`、`query_location_by_image`、`query_tagged_location`、`clear_all` |
| `NavigationSkillContainer` | 见下节 Agent Skills |
| `McpClient` | Agent 对话循环，MCP tool 调用 |

---

## 五、新增 / 增强 Agent Skills

### NavigationSkillContainer（`navigation.py`）

| Skill | 作用 |
|-------|------|
| `navigate_with_text` | 自然语言导航主入口，六层 fallback 链 |
| `navigate_to_landmark` | 按已存地标名精确导航（拓扑 + 漂移校正） |
| `tag_location` / `tag_room` | 打 ROOM 标签，可选 360° 环视 + 后台 VLM 物体入库 |
| `detect_objects_in_view` | 当前画面 VLM 检测并写入 LANDMARK |
| `query_landmarks` | 查询 rooms / objects / 全部地标 |
| `find_room_visually` | CLIP 图像匹配当前房间（不依赖坐标） |
| `clear_all_memory` | 清空地标 + spatial 记忆 |
| `stop_all_motion` / `emergency_stop` / `stop_movement` | 急停与取消导航 |

Fallback 链顺序（`DIMOS_NAV_FALLBACK=semantic` 默认）：

```
landmark → in_frame → room_sweep → vlm_memory → clip_map → tagged
```

### OrbitObjectSkillContainer（`orbit_object.py`）

| Skill | 作用 |
|-------|------|
| `orbit_object` | 沿 LiDAR costmap 边缘绕障物行走指定圈数 |

---

## 六、模块依赖关系（简图）

```
CLI: dimos run / dimos tell / tell-robot
         │
         ▼
Blueprint (unitree-go2-agentic-* / unitree-go2-spatial)
         │
    ┌────┴────────────────────────────────────────────┐
    ▼                    ▼                            ▼
McpClient          NavigationSkillContainer    SpatialLandmarkMemoryModule
(Agent LLM)        (VLM + Skills)              (JSON 地标记忆)
    │                    │                            │
    │              ┌─────┴─────┐                      │
    │              ▼           ▼                      │
    │         TopologyGraph  ReplanningAStarPlanner   │
    │              │           (全局 A* + 避障)       │
    │              ▼                                   │
    │         SpatialMemory ◄── CLIP 房间图 + MiniLM 文本 tag
    │              │
    └──────── ObjectTracker2D + BBoxNavigationModule (in-frame 精定位)
```

---

## 七、环境变量（运行时配置）

| 变量 | 影响模块 |
|------|----------|
| `OPENAI_API_KEY` / `OPENAI_BASE_URL` | McpClient（DeepSeek 等 Agent LLM） |
| `DIMOS_QWEN_MLX_BASE_URL` / `DIMOS_QWEN_MLX_MODEL` | Qwen MLX Agent |
| `DASHSCOPE_API_KEY` / `DIMOS_VLM_PROVIDER` / `DIMOS_VLM_MODEL_NAME` | NavigationSkillContainer 内 VLM |
| `DIMOS_NAV_FALLBACK` | `navigate_with_text` fallback 顺序 |
| `--new-memory`（global_config） | SpatialMemory / 地标 JSON 是否清空重建 |

---

## 八、未纳入本总结的变更

以下 deliberately 不在本文档范围：

- `.github/workflows/*`、CI/auto-merge 配置
- `**/test_*.py`、`*_test.py` 及测试 fixture
- 仓库根目录独立脚本（如 `test_vlm_navigation.py`、`vlm.py`）
- `ai_longrun_harness/`、`docs/analysis/` 等非框架运行时文档
- `uv.lock` 体量变更
