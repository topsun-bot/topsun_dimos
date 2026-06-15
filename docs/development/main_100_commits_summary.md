# Main 分支 100 个提交总结（53f5c791 → bc442de1）

> 时间范围：截至 2026-05-29
> 提交范围：`53f5c791..bc442de1`（共 100 个提交）
> 主要主线：**语义导航 + 空间记忆 + 新技能（follow_me / orbit_object）+ Landmark Pack + nav-go2 完整示例**

---

## 一、新功能（Features）

### 1. 导航相关

#### nav-go2 示例套件（PR #72）
完整的 Unitree Go2 NoMaD 导航示例，新增目录 `examples/nav-go2/`：
- `traversability_grid.py` / `local_navigation_map_module.py`：本地导航栅格地图
- `multi_waypoints_selector.py`：多 waypoint 选择器
- `path_selector.py`：基于 costmap 的路径选择
- `trajectory_local_planner_module.py` / `trajectory_inference.py`：轨迹局部规划
- `engine/nomad/`：NoMaD 推理引擎封装
- `controller.py` / `go2_nomad_nav.py`：端到端控制与导航入口
- `record_image_module.py`：记录与导航两种模式

关键提交：
- `0b9fb75b` feat(examples): add nav-go2 Go2 NoMaD traversability example
- `79c6f004` feat: follower / config / costmap path selection
- `e1cdc9c3` feat: costmap route selection and standalone multi-waypoint selector
- `74f59936` feat: configurable waypoint selection + fix multi_waypoints deadlock
- `99319a57` feat: add nav-go2 record and navigate modes
- `38b31a48` fix: harden nav-go2 stream and grid timing

#### 语义导航鲁棒循环（PR #85 / PR #91 / PR #99）
- `feat/semantic-nav-robust-loop`：稳健的语义导航循环
- `feat/semantic-nav-visual-reverify`：到达时通过 VLM 进行视觉重验证
- `bc442de1` feat/gy semantic nav robust loop（最后并入）

关键提交：
- `569047bf` feat(nav): visual re-verify flow with VLM presence check
- `d4786b97` fix: store object bearing from VLM bbox + visual re-acquire at arrival
- `12c31f06` fix: skip stale coordinate check for current-session targets
- `b99c7072` fix: wait for each topological waypoint before proceeding

#### 拓扑导航（新模块）
- `dimos/navigation/topology.py`（+179 行）
- `dimos/navigation/test_topology.py`（+137 行）
- `dimos/navigation/visual/query.py` 扩展 + 新增 `test_query.py`
- `dimos/navigation/replanning_a_star/`：global / local planner 改进
- `ba30df5b` fix(nav-go2): preserve planner error context
- `4ab9063b` fix(nav-go2): harden trajectory planner inputs
- `0c208c7b` fix(nav-go2): handle traversability grid edges

---

### 2. 技能（Skills）

#### follow_me：基于 ReID 的跟随技能（PR #86）
- `dimos/agents/skills/follow_me.py`（+1841 行）
- 配套 Qwen-VL blueprint：`unitree_go2_qwen_follow.py`

#### orbit_object：基于 LiDAR 的目标绕行技能（PR #80）
- `dimos/agents/skills/orbit_object.py`（+508 行）
- `dimos/agents/skills/test_orbit_object.py`（+275 行）
- 文档：`docs/development/orbit_object.md`
- 独立 demo：`demo_orbit_standalone.py` / `demo_orbit_test.py`

#### navigation.py 大幅扩展
- `dimos/agents/skills/navigation.py`：+3334 行
- `dimos/agents/skills/test_navigation.py`：+1065 行

#### speak_skill 改造
- `dimos/agents/skills/speak_skill.py`（+153 / -大量行）
- `030fffcf` fix: route Go2 speech through AudioHub（PR #87）

---

### 3. 空间感知 / 记忆

#### 空间记忆 + 门检测 + Embodied RAG（`9cc59b5c`）
新增 `dimos/perception/detection/door/`：
- `door_detector.py`（+235 行）
- `door_spatial_memory.py`（+390 行）
- `door_spatial_memory_module.py`（+181 行）
- `test_door_spatial_memory.py` / `test_landmark_memory_dedup.py`

新增类型：
- `dimos/types/door_record.py` / `door_memory_spec.py`
- `dimos/types/spatial_record.py`

文档：
- `docs/development/spatial_memory_architecture.md`

#### 盲区探索（PR #90 / PR #101）
- 文档：
  - `docs/capabilities/memory/spatial_memory_blindspot_explorer.md`（+1218 行）
  - `docs/capabilities/memory/spatial_memory_blindspot_explorer_optimization.md`（+739 行）
- `dimos/perception/spatial_perception.py` 大幅改造（+505 行变更）

#### 长跑内存边界（PR #69）
- `dc3e24c4` perf: bound long-run memory in spatial memory and rerun bridge
- `dimos/perception/temporal_memory/temporal_memory.py` 改进
- `dimos/visualization/rerun/bridge.py` 内存控制

#### Object Tracker 2D 改进（PR #105）
- `dimos/perception/object_tracker_2d.py`（+72 行）
- 文档：`docs/development/object_tracker_2d_3d.md`（+240 行）

---

### 4. Landmark Pack（PR #100）

新增完整的 landmark 包导入/导出体系：
- `dimos/landmark/landmark_pack.py`（+409 行）
- `dimos/landmark/test_landmark_pack.py`（+355 行）
- 示例包 `dimos/landmark/_packs/demo_office/`：`landmarks.json` + `manifest.yaml`

CLI 支持：
- `dimos/robot/cli/landmarks.py`（+183 行）：`dimos landmarks` 子命令
- `dimos/robot/cli/tell.py`（+156 行）：`dimos tell` 子命令
- `dimos/utils/cli/tell_robot.py`（+62 行）

依赖：`pyyaml` 加入基础依赖（`1f85bf97`）

---

### 5. 新 Agent / Blueprint / Model

#### Qwen MLX
- `dimos/agents/qwen_mlx_agent.py`（+66 行）
- `unitree_go2_agentic_qwen_mlx.py` blueprint
- PR #95：替换 Qwen MLX blueprint 默认值为可移植的 localhost

#### DeepSeek Blueprint
- `unitree_g1_agentic_deepseek.py` / `unitree_g1_agentic_sim_deepseek.py`
- `unitree_go2_agentic_deepseek.py`

#### DashScope（阿里云）模型与 TTS
- `dimos/models/vl/dashscope.py`（+142 行）
- `dimos/stream/audio/tts/node_dashscope.py`（+153 行）
- DashScope 声明为可选依赖（`1bdf219c`、`fe651385`）

---

### 6. 全局配置 / 传输层（PR #84）

**Global Transport Toggle**：通过 `GlobalConfig` + Blueprint factory 切换 LCM / SHM 传输。

关键提交：
- `af26e4f6` feat: add global transport toggle via GlobalConfig + Blueprint factory
- `e99dccb4` fix: pin LCM for externally-consumed streams + SHM capacity for images
- `267255cb` refactor: replace `_LCM_PINNED_NAMES` with module-level `_stream_transport_pins`
- `f1e95e13` fix: raise ValueError for invalid `default_transport`
- `041f36bc` fix: platform-aware default + pin lookup respects remapping

涉及文件：
- `dimos/core/global_config.py`（+21 行）
- `dimos/core/coordination/blueprints.py` / `module_coordinator.py` / `test_blueprints.py`
- `dimos/robot/all_blueprints.py`

---

## 二、基础设施 / 自动化

### AI Long-Run Harness
- `ai_longrun_harness/run_pr_pipeline.sh`（+70 行）
- `ai_longrun_harness/run_to_main.sh`（+125 行）
- `ai_longrun_harness/config.env.example`
- `bin/auto_to_main`

### CI 优化（PR #73 / #75 / #76 / #77）
- `426f664c` perf(ci): cut md-babel LFS download from 19 GB to ~200 MB
- `4c853e3e` fix(ci): derive md-babel LFS cache key from paths
- `54187aad` fix(ci): pre-pull doc get_data LFS archives for md-babel
- `acea0844` refactor(ci): DRY md-babel data/.lfs archive list
- `879a86c5` / `da08a2dd` fix(ci): skip macOS self-hosted matrix leg

### Auto-merge 工作流（PR #2 / 早期）
- `f0dda057` ci: add auto-merge workflow for PRs labeled "automerge"
- `cc5f265d` ci: drop automerge label gate, add codex review
- `9739a7ea` ci(auto-merge): poll up to 30 min for Codex 👍
- `b525c3d7` fix: resume-based Codex check instead of 30min timeout polling
- `1c07a93f` fix(auto-merge): match Codex bot login with `[bot]` suffix

### 其他工具
- `scripts/demo_semantic_nav.sh`：语义导航演示
- `scripts/fetch_unitree_aes_key.py`（+155 行）
- `scripts/sync_feishu_wiki_doc.sh`（+167 行）：飞书 wiki 同步

---

## 三、文档

### 模块架构翻译（PR #3 / #4 / #5 / #6）
- `33ce4e14` 中文翻译
- `75f742ab` 日文翻译
- `ac7fc76f` 德文翻译

### 新增开发文档
- `docs/development/orbit_object.md`（+519 行）
- `docs/development/object_tracker_2d_3d.md`（+240 行）
- `docs/development/spatial_memory_architecture.md`（+261 行）

### 能力文档
- `docs/capabilities/memory/spatial_memory_blindspot_explorer.md`（+1218 行）
- `docs/capabilities/memory/spatial_memory_blindspot_explorer_optimization.md`（+739 行）

### 平台文档
- `docs/platforms/quadruped/go2/index.md`（+16 行）

### nav-go2 README
- `examples/nav-go2/README.md`（+201 行）

---

## 四、Bug 修复 & 杂项

### 主要 Bug 修复
- `030fffcf` fix: route Go2 speech through AudioHub（PR #87）
- `9a8b24f2` fix(test): stabilize LCM context in pubsub `test_spec`（PR #102）
- `89183bbe` fix: P0/P1 review on spatial memory and VLM bbox parsing
- `5b071768` docs(tracker): clarify ObjectTracker2D behavior comments（PR #105）

### CI / 依赖
- `1bdf219c` / `fe651385` chore: dashscope 设为可选依赖
- `4d6b5c5c` chore(ruff): 允许中文 VLM prompt 中的全角标点
- `08de6f79` 修复 CI 问题 / `b370184b` 修改 CI bug

### 提交工程改进
- `877f77da` chore: add Codex review rules and fix `model_kwargs`
- `5c2c3178` fix: batch fix strategy for Codex reviews
- `83bf3cde` chore: clean up non-business PR changes

---

## 五、关键统计

- **总提交数**：100
- **变更文件**：119 个
- **新增行数**：约 20,806 行
- **删除行数**：约 292 行
- **主要 PR**：#69、#72、#80、#84、#85、#86、#87、#90、#91、#95、#99、#100、#101、#102、#105

## 六、对开发者的影响

1. **新依赖**：`pyyaml`（必装）；`dashscope`（可选）
2. **新 CLI**：`dimos landmarks` / `dimos tell`
3. **新示例**：`examples/nav-go2/`
4. **配置变更**：可通过 `GlobalConfig` 设置 `default_transport`（LCM/SHM）
5. **能力升级**：
   - 跟随：`follow_me` 技能（ReID）
   - 避障/绕行：`orbit_object` 技能（LiDAR）
   - 导航：语义导航 + VLM 视觉重验证 + 拓扑导航
   - 记忆：空间记忆 + 门检测 + 盲区探索
   - 模型：Qwen MLX、DeepSeek、DashScope 接入
