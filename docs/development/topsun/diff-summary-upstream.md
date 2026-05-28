# Topsun 分支相对 upstream/main 切分点的差异总结

> 切分点: `6e171ac4` (upstream/main)
> 当前 HEAD: `70b9492a` (Merge pull request #85)
> 统计: 97 files changed, 11549 insertions(+), 252 deletions(-)
> 分析日期: 2026-05-26

---

## 1. dimos 本体框架改动

### 1.1 Agent 技能层 (`dimos/agents/`)

**导航技能大幅增强** ([navigation.py](/dimos/agents/skills/navigation.py)) — +2036/-28 行

- 引入 `TopologyGraph` 拓扑图导航，支持房间节点/走廊边建模和拓扑级路径规划
- 新增多层级语义回退策略：`landmark` → `in_frame` (VLM bbox) → `room_sweep` → `vlm_memory` (批量VLM) → `clip_map` → `tagged`，可通过 `DIMOS_NAV_FALLBACK` 环境变量切换
- 支持 VLM bbox 视觉重捕获 (visual re-acquire at arrival)
- 集成 `WavefrontFrontierExplorer` 前沿探索
- 跳过当前 session 目标坐标的过期检查，提升连续导航鲁棒性
- 新增 `test_navigation.py` 测试 (+375 行)

**新增绕障技能** ([orbit_object.py](/dimos/agents/skills/orbit_object.py)) — 新文件 +508 行

- 基于 LiDAR 点云的障碍物环绕能力，支持指定 orbit 半径和角度
- 依赖 `SpatialMemorySpec` 和 `ObjectTrackingSpec` 接口
- 包含完整测试 `test_orbit_object.py` (+275 行)

**Qwen MLX Agent** ([qwen_mlx_agent.py](/dimos/agents/qwen_mlx_agent.py)) — 新文件 +66 行

- 新增 Qwen MLX 模型 agent 配置，支持 Apple Silicon 本地推理

**MCP Client 改进** ([mcp_client.py](/dimos/agents/mcp/mcp_client.py)) — +40/-30 行

**System Prompt 微调** ([system_prompt.py](/dimos/agents/system_prompt.py)) — +7 行

### 1.2 感知层 (`dimos/perception/`)

**门检测模块** (全新子模块 `detection/door/`)

- [door_detector.py](/dimos/perception/detection/door/door_detector.py) — 门体检测器 +235 行
- [door_spatial_memory.py](/dimos/perception/detection/door/door_spatial_memory.py) — 门空间记忆存储 +390 行
- [door_spatial_memory_module.py](/dimos/perception/detection/door/door_spatial_memory_module.py) — dimos 模块封装 +181 行
- 测试: `test_door_spatial_memory.py` +143 行, `test_landmark_memory_dedup.py` +94 行

**空间感知大幅增强** ([spatial_perception.py](/dimos/perception/spatial_perception.py)) — +500/-21 行

- 增强空间物体关系建模、坐标转换和记忆召回

**对象跟踪改进** ([object_tracker_2d.py](/dimos/perception/object_tracker_2d.py)) — +57/-23 行

**空间记忆 spec 扩展** ([spatial_memory_spec.py](/dimos/perception/spatial_memory_spec.py)) — +9 行

**时间记忆改进** ([temporal_memory.py](/dimos/perception/experimental/temporal_memory/temporal_memory.py)) — +35/-1 行

### 1.3 导航层 (`dimos/navigation/`)

**拓扑导航** ([topology.py](/dimos/navigation/topology.py)) — 新文件 +179 行

- 实现房间/走廊拓扑图构建与路径查找

**拓扑测试** ([test_topology.py](/dimos/navigation/test_topology.py)) — 新文件 +137 行

**视觉查询增强** ([visual/query.py](/dimos/navigation/visual/query.py)) — +137/-89 行

- VLM bbox 解析增强，支持物体方位角提取
- 新增 `test_query.py` 测试 (+54 行)

**重规划 A\* 规划器** — global_planner +6 行, local_planner +22 行

**bbox 导航微调** ([bbox_navigation.py](/dimos/navigation/bbox_navigation.py)) — 2 行微调

### 1.4 VLM 模型层 (`dimos/models/vl/`)

**DashScope VLM 支持** ([dashscope.py](/dimos/models/vl/dashscope.py)) — 新文件 +142 行

- 集成阿里云 DashScope 视觉语言模型，支持图像理解和 bbox 检测
- 声明为可选依赖 `[agent]` (vlm extra)

**OpenAI VLM 改进** ([openai.py](/dimos/models/vl/openai.py)) — +30/-3 行

### 1.5 核心框架 (`dimos/core/`)

**全局传输切换** ([global_config.py](/dimos/core/global_config.py)) — +8 行

- 新增 `GlobalConfig` 全局传输层切换开关 (LCM/SHM)，通过 `GlobalConfig.default_transport` 控制
- 修复 stream transport pins 查找逻辑，支持 platform-aware default

**Blueprints 工厂模式** ([blueprints.py](/dimos/core/coordination/blueprints.py)) — +22 行

- Blueprint factory 支持全局 transport toggle 注入
- `test_blueprints.py` 测试扩展 +154 行

**模块协调器** ([module_coordinator.py](/dimos/core/coordination/module_coordinator.py)) — +53/-10 行

### 1.6 类型系统 (`dimos/types/`)

- [spatial_record.py](/dimos/types/spatial_record.py) — 新文件 +103 行，定义 `RecordType`、`SpatialRecord` 等空间记忆记录类型
- [door_memory_spec.py](/dimos/types/door_memory_spec.py) — 新文件 +38 行，门记忆 spec 接口
- [door_record.py](/dimos/types/door_record.py) — 新文件 +32 行，门记录数据结构
- [test_door_record.py](/dimos/types/test_door_record.py) — 新文件 +84 行

### 1.7 机器人蓝图层 (`dimos/robot/`)

**DeepSeek Agentic 蓝图** (新增 4 个)

- `unitree-g1-agentic-deepseek` / `unitree-g1-agentic-sim-deepseek` — G1 人形机器人 DeepSeek VLM agent (+49/42 行)
- `unitree-go2-agentic-deepseek` — Go2 四足 DeepSeek agent (+46 行)
- `unitree-go2-agentic-qwen-mlx` — Go2 Qwen MLX agent (+46 行)

**蓝图注册** ([all_blueprints.py](/dimos/robot/all_blueprints.py)) — +6 行

**现有蓝图修改**

- `unitree-g1-basic-sim` (+126 行) — G1 模拟器基础蓝图大幅扩展
- `unitree-go2-basic` (+31/-17 行) — Go2 基础蓝图调整
- `unitree-go2-spatial` (+27/-4 行) — Go2 空间蓝图增强
- `_perception_and_memory.py` (+15/-6 行) — 感知记忆公共模块

### 1.8 CLI 工具 (`dimos/robot/cli/`)

- [tell.py](/dimos/robot/cli/tell.py) — 新文件 +156 行，自然语言指令发送 CLI
- [dimos.py](/dimos/robot/cli/dimos.py) — +21 行，dimos 主 CLI 命令扩展

### 1.9 工具层 (`dimos/utils/`)

- [tell_robot.py](/dimos/utils/cli/tell_robot.py) — 新文件 +62 行，"tell robot" 通用工具
- [generic.py](/dimos/utils/generic.py) — +38 行，新增 `extract_json_from_llm_response` 等工具函数
- [data.py](/dimos/utils/data.py) — 2 行微调

### 1.10 可视化 (`dimos/visualization/`)

- [bridge.py](/dimos/visualization/rerun/bridge.py) — +44 行，rerun 桥接增强，添加长时间运行内存边界控制
- [vis_module.py](/dimos/visualization/vis_module.py) — +2 行

### 1.11 废弃 Agent 层 (`dimos/agents_deprecated/memory/`)

**空间向量数据库** ([spatial_vector_db.py](/dimos/agents_deprecated/memory/spatial_vector_db.py)) — +96 行

- 添加 FIFO 帧数量限制 (`max_stored_frames`)，防止长时间运行内存泄漏
- 新增 `remove_image_vector()`、`_enforce_frame_limit()`、`_sync_frame_order_from_collection()` 方法
- Location tagging 添加名称去重逻辑

**视觉记忆** ([visual_memory.py](/dimos/agents_deprecated/memory/visual_memory.py)) — +15 行

- 新增 `remove()` 方法支持单帧删除

**图像嵌入** ([image_embedding.py](/dimos/agents_deprecated/memory/image_embedding.py)) — +13 行

- CLIP processor 优先从本地数据目录加载（避免 HuggingFace 网络/SOCKS 代理问题）

### 1.12 音频流 (`dimos/stream/`)

- TTS OpenAI 节点微调 (+5/-1 行)

---

## 2. Examples 新增与改动

### 全新 `examples/nav-go2/` 示例包 (+3096 行，23 个文件)

基于 NoMaD (Navigation with Map Distribution) 的 Go2 四足机器人视觉导航示例。

**核心模块:**

| 文件 | 行数 | 功能 |
|------|------|------|
| [go2_nomad_nav.py](/examples/nav-go2/go2_nomad_nav.py) | 209 | 主入口，支持 record/navigate 两种模式 |
| [controller.py](/examples/nav-go2/controller.py) | 251 | 机器人运动控制器 |
| [trajectory_local_planner_module.py](/examples/nav-go2/trajectory_local_planner_module.py) | 414 | 轨迹局部规划器 dimos 模块 |
| [local_navigation_map_module.py](/examples/nav-go2/local_navigation_map_module.py) | 206 | 局部导航代价地图模块 |
| [multi_waypoints_selector.py](/examples/nav-go2/multi_waypoints_selector.py) | 243 | 多路点选择器 |
| [path_selector.py](/examples/nav-go2/path_selector.py) | 122 | 基于代价地图的路径选择 |
| [traversability_grid.py](/examples/nav-go2/traversability_grid.py) | 99 | 可通行性网格推理 |
| [trajectory_inference.py](/examples/nav-go2/trajectory_inference.py) | 60 | 轨迹推理工具 |
| [record_image_module.py](/examples/nav-go2/record_image_module.py) | 88 | 图像录制模块 |

**NoMaD 引擎子包** (`engine/nomad/`):

| 文件 | 行数 | 功能 |
|------|------|------|
| [inference.py](/examples/nav-go2/engine/nomad/inference.py) | 309 | NoMaD 模型推理核心 |
| [config.py](/examples/nav-go2/engine/nomad/config.py) | 125 | 模型配置 |
| [local_planner_module.py](/examples/nav-go2/engine/nomad/local_planner_module.py) | 32 | 局部规划器模块封装 |

**测试文件** (7 个测试，覆盖 controller、trajectory、waypoints、path_selector、local_map、traversability):

- `test_controller.py` (49 行)
- `test_trajectory_local_planner.py` (236 行)
- `test_multi_waypoints_selector.py` (121 行)
- `test_path_selector.py` (48 行)
- `test_local_navigation_map_module.py` (110 行)
- `test_traversability_grid.py` (116 行)

**配置与文档:**

- `config/nomad_nav.yaml` — NoMaD 导航运行参数配置
- `README.md` (201 行) — 依赖安装、使用方法、架构说明

---

## 3. Workflow / CI / 配置改动

### 3.1 CI/CD 流水线 (`.github/`)

**CI 工作流** ([ci.yml](/.github/workflows/ci.yml)) — +72 行

- 添加 `issues:write` 权限
- 临时跳过 macOS self-hosted runner（fork 环境不可用）
- 改进 md-babel LFS 缓存策略：按路径计算缓存 key，将下载量从 ~19GB 降至 ~200MB
- 预拉取 doc get_data LFS 归档以加速数据依赖安装
- 对齐 self-hosted container tag 与 docker-build 输出

**Docker 构建** ([docker-build.yml](/.github/workflows/docker-build.yml)) — +18 行

- 镜像推送到 topsun-bot GHCR registry (`ghcr.io/topsun-bot/`)
- 构建模板 `_docker-build-template.yml` +6 行

**其他:**
- GitHub Actions docker-build action 微调
- 新增 `.env.example` 环境变量模板 (+9 行)

### 3.2 CI 自动化 (通过 GitHub Actions，无本地文件)

- Codex AI Review 工作流（由 `.github/codex-review-rules.md` 配置）
- Auto-merge 工作流：Codex bot 点赞后自动合入
- Stale issue/pr 处理自动化

### 3.3 容器与开发环境

**Docker:**

- `docker/dev/Dockerfile` — 基础镜像版本更新
- `docker/python/Dockerfile` — Python 镜像更新
- `docker/dev/docker-compose.yaml` / `docker-compose-cuda.yaml` — compose 镜像 tag 更新
- `docker/python/module-install.sh` — 模块安装脚本版本对齐

**Dev Container:**

- `devcontainer.json` — 配置微调

**脚本:**

- `bin/dev` — +4 行微调
- `bin/dockerbuild` — 版本更新

### 3.4 Python 项目配置

- [pyproject.toml](/pyproject.toml) — +5 行，声明 `dashscope` 为可选 `[agent]` 依赖
- [uv.lock](/uv.lock) — +666 行，锁文件更新（依赖版本锁定）

---

## 4. Data / Docs / Demo 等其他改动

### 4.1 文档 (`docs/`)

- [orbit_object.md](/docs/development/orbit_object.md) — 新文件 +519 行，绕障技能完整开发文档（架构、接口、使用示例）

- [docker.md](/docs/development/docker.md) — +6/-3 行，Docker 开发环境文档更新

### 4.2 独立 Demo 脚本

- [demo_orbit_standalone.py](/demo_orbit_standalone.py) — 新文件 +253 行，绕障技能独立演示脚本（不依赖完整 dimos 运行时）
- [demo_orbit_test.py](/demo_orbit_test.py) — 新文件 +103 行，绕障技能测试脚本

### 4.3 Data 层

- 无新增数据文件（`get_data` 相关改动仅涉及 CI 缓存优化，不新增 LFS 数据）

---

## 总结

本次分支相对 upstream/main 切分点的改动主要集中在 **5 大方向**:

| 方向 | 关键内容 | 影响范围 |
|------|----------|----------|
| 语义导航鲁棒性 | 拓扑图、多级回退、VLM bbox 增强 | dimos/agents/, dimos/navigation/ |
| 空间感知与记忆 | 门检测、空间记忆边界控制、记录类型 | dimos/perception/, dimos/types/, dimos/agents_deprecated/ |
| 多 VLM 后端支持 | DeepSeek、Qwen MLX、DashScope | dimos/models/vl/, dimos/robot/ |
| NoMaD 视觉导航示例 | Go2 可通行性预测与轨迹规划 | examples/nav-go2/ |
| 基础设施适配 | GHCR 镜像、CI 缓存优化、传输层切换 | .github/, docker/, dimos/core/ |
