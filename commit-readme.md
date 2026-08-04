# Commit 变更日志

> 记录本仓库 fork 上的每次提交，便于追溯改动、快速上手用法、以及出问题时回滚到指定版本。
>
> **维护规则**：每次 `git commit` / `cherry-pick` / `merge` / `push` 后，由 Agent 自动在本文件顶部追加一条记录（见 `.cursor/rules/commit-readme.mdc`）。

---

## 快速回滚

```bash
# 查看某次提交的完整 diff
git show <sha>

# 回滚到指定版本（保留工作区改动）
git checkout <sha> -- <file>

# 硬回滚整个分支到某次提交（慎用）
git reset --hard <sha>
```

| 想做的事 | 回滚到 |
|----------|--------|
| 旋转 settle 失败中止搜索 / worker GlobalConfig 全量同步 | `a3edc2eef` |
| 合并 upstream/main（99 commits，0709~0729）前的基线 | `dde106238` |
| spatial memory search V1 默认参数 | `28796439d` |
| 原地旋转最小摇杆 \|rx\|=0.2 | `ba00a2136` |
| 启动默认清空 landmarks / CLIP（new_memory） | `597dc068f` |
| 导航诊断 trace / `dimos nav analyze` | `0ed24b321` |
| Go2 4G Remote WebRTC / unitree-go2 remote | `fe4b31a77` |
| uv.lock 去重 / bugfix §12-13 / free_avoid 默认关 | `94b2cf002` |
| 合并 upstream/main fdf3cb7d（Zenoh/spy/WebRTC/scene） | `77ca3291c` |
| fast ICP 诊断 / point-to-plane / 开机 odom 分析 | `bacc407a` |
| Go2 启动 foxglove_config TypeError | `792a4c4d` |
| relocalize 离线逐步调试脚本 | `58cfbe41` |
| 日志目录 cwd 解析 / Go2 Rerun 雷达可见 | `88abe28d` |
| Go2 重定位调用链解读与注释 | `b3b88279` |
| 查看/维护提交日志 | `af7df1395` |
| Rerun 画面冻结 / 内存暴涨 | `58a9830b5` |
| replay-db 找不到本地 .db | `58a9830b5` |
| Go2 扬声器 TTS / 人体检测 lookout | `c40daacb0` |
| WebRTC publish_request 卡死 | `c40daacb0` |

---

## 提交记录

### f7edeb1f — docs(plan): expand Go2 odom fidelity rotation state machine and bounded recovery

| 字段 | 内容 |
|---|---|
| **时间** | 2026-08-04 17:03:47 +0800 |
| **分支** | `jtlinux` |
| **作者** | `jiang.tao` |

**修改文件**

| 文件 | 改动 |
|---|---|
| `jiangtao/plan/2026-07-30-Go2原始Odom时间保真与零速度静止闭环实施计划.md` | 补充 PRECHECK/enforce、有界恢复、60s 旋转硬上限与状态机细节 |

**改进点**

1. 明确原地转向状态机新增启动前检查与 enforce 下 5s 等待 baseline。
2. 反馈异常恢复改为最多 5s、单次最多 2 次，避免无限等待。
3. 单次旋转总时长硬上限 60s，与 3s 静止确认的关系写清。

**用法**

```bash
# 实施前参考计划文档中的状态机与 DIMOS_ODOM_* 标定项
cat jiangtao/plan/2026-07-30-Go2原始Odom时间保真与零速度静止闭环实施计划.md
```

**回滚**

```bash
git checkout 5fe99055 -- jiangtao/plan/2026-07-30-Go2原始Odom时间保真与零速度静止闭环实施计划.md
```

---

### a3edc2eef — fix(go2): abort search on rotate settle failure and sync worker GlobalConfig

| 字段 | 内容 |
|---|---|
| **时间** | 2026-08-04 10:05:39 +0800 |
| **分支** | `jtlinux` |
| **作者** | `jiangtao-huazhijian` |

**修改文件**

| 文件 | 改动 |
|---|---|
| `dimos/robot/unitree/unitree_skill_container.py` | `rotate_in_place` 零速后等待 yaw settle；失败返回 False；新增 settle 相关环境变量 |
| `dimos/agents/skills/navigation.py` | 旋转失败时中止寻物/扫房链路，记录 `_rotation_safety_error` |
| `dimos/agents/skills/test_navigation.py` | 补旋转失败中止搜索的单测 |
| `dimos/agents/skills/test_unitree_skill_container.py` | 补 settle/零速确认相关单测 |
| `dimos/core/coordination/python_worker.py` | worker 同步完整 `GlobalConfig`，不再只拷贝 `transport` |
| `jiangtao/doc/*`、`jiangtao/plan/*`、`jiangtao/scripts/*`、`jiangtao/test_report/*` | 上游同步分析、旋转验证脚本与真机报告 |

**改进点**

1. Remote 原地旋转超转后，零速下发不等于机身已静止；settle 闭环避免下一拍带着残余角速度继续转。
2. 视觉伺服/扫房若旋转失败，不再当作“没找到目标”继续搜，直接中止整条搜索链。
3. forkserver worker 以前只同步 transport，simulation/replay 等标志会丢；改为全量 `model_dump()` 同步。

**用法**

```bash
export DIMOS_ROTATE_MIN_RAD_S=0.2
export DIMOS_ROTATE_MAX_RAD_S=0.25
export DIMOS_ROTATE_SETTLE_ENABLED=true
dimos run unitree-go2-agentic --robot-ip <ip>
# 可选验证脚本
python jiangtao/scripts/validate_go2_rotation_stop.py
```

**回滚**

```bash
git checkout a3edc2eef^ -- \
  dimos/robot/unitree/unitree_skill_container.py \
  dimos/agents/skills/navigation.py \
  dimos/agents/skills/test_navigation.py \
  dimos/agents/skills/test_unitree_skill_container.py \
  dimos/core/coordination/python_worker.py
# 或
git reset --hard 9c47dc40c
```

---

### bc31f3dae — merge: sync upstream/main into jtlinux

| 字段 | 内容 |
|---|---|
| **时间** | 2026-07-31 18:11:46 +0800 |
| **分支** | `jtlinux` |
| **作者** | `jiangtao-huazhijian` |

**修改文件**（合并提交涉及冲突解决的主要文件，完整列表见 `git show --stat bc31f3dae`）

| 文件 | 改动 |
|---|---|
| `.gitignore` | 合并本地 `jiangtao/` 忽略项与上游新增的 `apriltags.pdf`/`*.ignore*` |
| `pyproject.toml` | 合并 CLI entry points（`doclinks`/`dtop`/`tell-robot`）、package include；追加 largefiles ignore 白名单（历史 PDF） |
| `uv.lock` | 手动合并依赖条目，随后由 pre-commit 自动校验通过 |
| `dimos/cli/dimos.py`、`dimos/cli/tell.py`、`dimos/cli/tell_robot.py` | `tell-robot` 迁移到 `dimos.cli`；合并 typer 子命令（`nav_app`/`piper_app`/`shell`/`cache_app`） |
| `dimos/constants.py` | 抽出 `_resolve_project_relative_dir()`，统一 `LOG_DIR`/`RECORDINGS_DIR` 解析优先级 |
| `dimos/agents/mcp/mcp_client.py` | HTTP 客户端由 `httpx` 切换为 `requests.Session`，保留本地代理清理逻辑与 `model_provider`/`model_kwargs` |
| `dimos/agents/skills/navigation.py` | `ObjectTrackingSpec`/`SpatialMemorySpec` 导入路径迁移到 `dimos.perception.experimental.*` |
| `dimos/perception/experimental/*` | 上游把 `spatial_memory`/`object_tracker_2d`/`perceive_loop_skill` 等移动到 `experimental/` 子目录，随迁移更新全部引用方 |
| `dimos/robot/all_blueprints.py` | 合并本地 `crow-agent` 与上游 `dan-holonomic-tc`/`dan-local-planner`，更新 `spatial-memory`/`object-tracker` 路径 |
| `dimos/core/global_config.py` | 合并本地导航 tracing 参数与上游 `zenoh_scouting`/`dimsim_headless`/`local_relay` 等新字段 |
| `dimos/mapping/relocalization/module.py`、`relocalize.py` | `VoxelGrid` 导入路径更新；`tf` 端口改为 `Out[TFMessage]` 包装；`open3d` 改为函数内惰性导入（`_o3d_registration()`），修复合并后 `NameError` |
| `dimos/navigation/replanning_a_star/global_planner.py` | 调整 `_find_wide_path` 中 `_clear_robot_footprint` 与计时起点的先后顺序 |
| `dimos/robot/unitree/connection.py`、`go2/connection.py`、`dimsim_connection.py`、`mujoco_connection.py` | 合并 `velocity_api`、`trace_sink` 参数链路；合并 `free_avoid`/`set_light`/`switch_joystick`/`sport_command` 等 RPC 方法 |
| `dimos/robot/unitree/unitree_skill_container.py` | 合并 `tf: In[TFMessage]` 端口声明与本地 `start()` 中的提前初始化 TF 逻辑（合并前 stash 冲突） |
| `dimos/robot/unitree/test_connection.py`、`go2/test_connection.py` | 补齐 `DEFAULT_THREAD_JOIN_TIMEOUT` 导入；合并新增 TF 测试与 `velocity_api=False` 断言 |
| `docs/usage/cli.md` | 保留本地 `<run-id>/<run-id>.jsonl` 日志路径格式，对齐上游 `ROBOT_IP` 环境变量命名 |

**改进点**

1. 落后上游 main 分支 99 个提交（约合并周期 0709~0729），本次一次性同步完毕，涉及 CLI 重构、感知模块 `experimental/` 目录整理、Zenoh 支持、WebRTC `velocity_api` 等大量特性。
2. 手动解决约 20 个文件的内容冲突，均已通过 `ast.parse` 语法校验，并对受影响模块跑过 `pytest`（`unitree/`、`mapping/relocalization/`、`navigation/replanning_a_star/`、`agents/mcp/`、`core/`、`perception/experimental/`）。
3. 剩余的 8 个测试失败（`mapping/relocalization/test_module.py` 7 个 + `agents/skills/test_navigation.py` 1 个）经与合并前 `jtlinux`（`dde106238`）detached HEAD 对比验证，均为**合并前已存在的问题**，与本次合并无关，未在本次处理。
4. 合并提交触发的 `largefiles-check` 钩子拦截了 6 个历史遗留超限 PDF（`jiangtao/cursor/*.pdf`，非本次新增），已加入 `pyproject.toml` 的 `[tool.largefiles].ignore` 白名单后完成提交。

**用法**

```bash
# 查看合并引入的完整改动统计
git show --stat bc31f3dae

# 查看某个冲突文件合并前后差异
git diff dde106238 bc31f3dae -- dimos/mapping/relocalization/relocalize.py

# 验证语法 / 跑受影响模块测试
uv run pytest dimos/robot/unitree/ dimos/mapping/relocalization/test_relocalize.py \
  dimos/navigation/replanning_a_star/ dimos/agents/mcp/ dimos/core/ \
  dimos/perception/experimental/test_spatial_memory_lightweight.py \
  dimos/robot/test_all_blueprints_generation.py -q
```

**依赖 / 前置**

- 合并前对本地未提交改动执行了 `git stash push -u -m "wip before upstream merge 20260731-1731"`，合并完成后已 `git stash pop` 并解决其中 `dimos/robot/unitree/unitree_skill_container.py` 的二次冲突（工作区未提交，未纳入本次 merge commit）。

**回滚**

```bash
# 回滚到合并前基线（丢弃本次同步的全部上游改动，谨慎使用）
git reset --hard dde106238
# 或仅回退单个文件
git checkout dde106238 -- <file>
```

---

### 28796439d — feat(nav): tune Go2 spatial memory search defaults for V1

| 字段 | 内容 |
|---|---|
| **时间** | 2026-07-31 16:18:27 +0800 |
| **分支** | `jtlinux` |
| **作者** | `jiangtao-huazhijian` |

**修改文件**

| 文件 | 改动 |
|---|---|
| `dimos/agents/skills/navigation.py` | 默认 `DIMOS_ROTATION_STEP_DEG=60`、`DIMOS_ROOM_SCAN_ROTATIONS=5`、确认容差 10° |
| `dimos/robot/unitree/unitree_skill_container.py` | 默认 `DIMOS_ROTATE_MAX_RAD_S=0.25`（MIN 仍为 0.2） |
| `pyproject.toml` | `navigation.py` 加入 largefiles ignore（>75KB 历史大文件） |

**改进点**

1. 真机验证通过的寻物/标记参数写入代码默认，无需每次 export env。
2. 标记全景 60°×5、搜索步长自动 40°、视觉确认容差 10°、原地转最大摇杆 0.25。

**用法**

```bash
# 直接跑 agentic blueprint，默认即 V1 参数
dimos run unitree-go2-agentic-deepseek --robot-ip <ip>
# 或 relocalization-memory 版
dimos run unitree-go2-relocalization-memory-agentic-deepseek --robot-ip <ip>
# 打 tag  checkout
git checkout spatial_memory_search_V1
```

**回滚**

```bash
git checkout c11458b9f -- dimos/agents/skills/navigation.py dimos/robot/unitree/unitree_skill_container.py pyproject.toml
# 或
git reset --hard c11458b9f
```

---

### ba00a2136 — fix(go2): lower rotate min stick to 0.2 after 4G calibration

| 字段 | 内容 |
|---|---|
| **时间** | 2026-07-28 18:56:12 +0800 |
| **分支** | `jtlinux` |
| **作者** | `jiangtao-huazhijian` |

**修改文件**

| 文件 | 改动 |
|---|---|
| `dimos/robot/unitree/unitree_skill_container.py` | `DIMOS_ROTATE_MIN_RAD_S` 默认 `0.4`→`0.2`；注释标明为摇杆 \|rx\| 非 rad/s |
| `jiangtao/run.md` | 补充 `DIMOS_ROTATE_MIN_RAD_S=0.2`、`OPENAI_BASE_URL`；确认容差改为 10° |
| `jiangtao/scripts/demo_go2_rotate_calibration.py` | 4G 旋转角速度/最小角标定脚本 |
| `jiangtao/scripts/demo_go2_rotate_calibration_safe.py` | 低幅 yaw 摇杆安全表征脚本 |

**改进点**

1. 4G 真机复验：`|rx|<0.15` 基本不转，`0.20` 起才可靠；把默认最小摇杆从过猛的 0.4 降到 0.2，减轻小角度过冲。
2. 文档与标定脚本落地，便于后续复测死区与 °/s 换算。

**用法**

```bash
export DIMOS_ROTATE_MIN_RAD_S=0.2
# 可选复测
python jiangtao/scripts/demo_go2_rotate_calibration.py --hold 2.0
```

**回滚**

```bash
git checkout 32e3adf7c -- dimos/robot/unitree/unitree_skill_container.py jiangtao/run.md
git rm -f jiangtao/scripts/demo_go2_rotate_calibration.py jiangtao/scripts/demo_go2_rotate_calibration_safe.py
# 或
git reset --hard 32e3adf7c
```

---

### 4a810400 — docs: update Go2 4G relocalization run guide in jiangtao/run.md

| 字段 | 内容 |
|---|---|
| **时间** | 2026-07-27 09:04:18 +0800 |
| **分支** | `jtlinux` |
| **作者** | `jiang.tao` |

**修改文件**

| 文件 | 改动 |
|---|---|
| `jiangtao/run.md` | 重组 4G 重定位导航流程、环境变量与建图/导出步骤；敏感 key 改为占位符 |

**改进点**

1. 将 4G Remote 重定位导航、建图、导出 premap 命令集中到文档前部，便于真机一条龙执行。
2. 补充 VLM 云端 fallback 与导航 trace 相关环境变量说明。
3. 推送前将 AES/API/VLM key 脱敏为占位符，避免明文入库。

**用法**

```bash
# 参考 jiangtao/run.md「4G Remote 重定位导航与诊断日志」章节
source .venv/bin/activate
# key 填 jiangtao/run-self.md 或本机 .env 后按文档 export
dimos --navigation-trace-level full --unitree-webrtc-method remote run unitree-go2-relocalization-memory-agentic-deepseek \
  --disable security-module -o relocalizationmodule.map_file=recording_go2
```

**回滚**

```bash
git checkout 13267db3 -- jiangtao/run.md
# 或
git reset --hard 13267db3
```

---


| 字段 | 内容 |
|---|---|
| **时间** | 2026-07-26 16:50:17 +0800 |
| **分支** | `jtlinux` |
| **作者** | `jiangtao-huazhijian` |

**修改文件**

| 文件 | 改动 |
|---|---|
| `dimos/core/global_config.py` | `new_memory` 默认改为 `True` |
| `dimos/perception/detection/door/door_spatial_memory_module.py` | `new_memory=True` 时 start 清空 landmarks.json / snapshots |
| `dimos/robot/unitree/go2/blueprints/smart/unitree_go2_spatial.py` | landmark 模块传入 `global_config.new_memory` |
| `dimos/perception/detection/door/test_landmark_memory_dedup.py` | 启动清空 / 保留记忆单测 |
| `docs/usage/cli.md` / `configuration.md` | 文档默认值同步 |
| `jiangtao/run.md` | Step 5 说明默认清空与 `--no-new-memory` |

**改进点**

1. 重启后不再误加载旧 odom 坐标系下的 landmarks / CLIP 房间图，避免「视觉像办公室但坐标差 11m」拒航。
2. 原先 `--new-memory` 只清 Chroma，现已同时清 `landmarks.json`。
3. 需要跨次保留记忆时显式 `--no-new-memory` 或 `DIMOS_NEW_MEMORY=false`。

**用法**

```bash
# 默认清空记忆后启动
dimos --robot-ip 10.10.155.110 run unitree-go2-relocalization-memory-agentic-deepseek \
  --disable security-module \
  -o relocalizationmodule.map_file=recording_go2

# 保留上次 landmarks / CLIP
dimos --no-new-memory --robot-ip 10.10.155.110 \
  run unitree-go2-relocalization-memory-agentic-deepseek \
  --disable security-module \
  -o relocalizationmodule.map_file=recording_go2
```

**回滚**

```bash
git checkout 52165b6aa -- \
  dimos/core/global_config.py \
  dimos/perception/detection/door/door_spatial_memory_module.py \
  dimos/robot/unitree/go2/blueprints/smart/unitree_go2_spatial.py
```

---

### 0ed24b321 — feat(nav): add bounded navigation diagnostics trace and offline analyze

| 字段 | 内容 |
|---|---|
| **时间** | 2026-07-26 11:41:25 +0800 |
| **分支** | `jtlinux` |
| **作者** | `jiangtao-huazhijian` |

**修改文件**

| 文件 | 改动 |
|---|---|
| `dimos/navigation/diagnostics/*` | 新增 TraceSink、manifest、report、`dimos nav analyze` CLI |
| `dimos/navigation/replanning_a_star/*` | planner/local/path_clearance 埋点与 session |
| `dimos/navigation/movement_manager/movement_manager.py` | mux 命令链 trace |
| `dimos/mapping/costmapper.py` / `relocalization/module.py` | costmap / 重定位候选与 attempt trace |
| `dimos/robot/unitree/connection.py` / `go2/connection.py` | WebRTC 发送与 odom/lowstate 可选记录 |
| `dimos/core/global_config.py` | `navigation_trace_level` 等配置，默认 `off` |
| `dimos/robot/cli/dimos.py` | 注册 `dimos nav`、写 navigation manifest |
| `docs/usage/navigation-diagnostics.md` / `jiangtao/run.md` | 用法与 4G 诊断启动说明 |

**改进点**

1. 可选记录规划→mux→WebRTC 控制链与 costmap/重定位证据，默认关闭不影响寻物。
2. 停机后 `dimos nav analyze <run-dir>` 生成 summary/report/plots，便于走过/转过排障。
3. 队列与磁盘预算失败时只降级诊断，不打断导航发令。

**用法**

```bash
dimos --navigation-trace-level full \
  --unitree-webrtc-method remote \
  --unitree-username "$UNITREE_USERNAME" \
  --unitree-password "$UNITREE_PASSWORD" \
  --unitree-serial "$UNITREE_SERIAL" \
  --unitree-region cn \
  run unitree-go2-relocalization-memory-agentic-deepseek \
  --disable security-module \
  -o relocalizationmodule.map_file=recording_go2

dimos stop
dimos nav analyze logs/<run-id>
```

**回滚**

```bash
git checkout 0ed24b321^ -- dimos/navigation/diagnostics dimos/core/global_config.py dimos/robot/cli/dimos.py
# 或
git reset --hard 29b240832
```

---

### fe4b31a77 — feat(go2): add 4G Remote WebRTC connection without breaking LocalSTA

| 字段 | 内容 |
|---|---|
| **时间** | 2026-07-21 11:17:45 +0800 |
| **分支** | `jtlinux` |
| **作者** | `jiangtao-huazhijian` |

**修改文件**

| 文件 | 改动 |
|---|---|
| `dimos/core/global_config.py` | 新增 remote 凭据与 `unitree_webrtc_method` |
| `dimos/robot/unitree/connection.py` | Remote: 共享 ICE + TURN-only + ULIDAR_SWITCH; LocalSTA 默认不变 |
| `dimos/robot/unitree/go2/connection.py` | `make_connection` 按 method 分流 |
| `dimos/robot/unitree/test_connection.py` 等 | 单测覆盖 remote / LocalSTA |
| `jiangtao/run.md` | 4G 启动说明（占位符，个人配置见 run-self.md） |
| `jiangtao/plan/2026-07-20-Go2-4G远程WebRTC跨电脑完整测试指南.md` | 完整测试与排障指南 |
| `jiangtao/test/4G-WebRTC-test/*` | 验收脚本、JSON、相机样张 |
| `.gitignore` | 忽略 `jiangtao/run-self.md` |

**改进点**

1. `dimos run unitree-go2` 可通过 4G 云 TURN 建连，不破坏原有 LAN LocalSTA。
2. 修复 aiortc 多 m-line 不同 ice-ufrag 导致的 Remote DTLS 失败。
3. 点云三开关与 DimOS 导航链在 4G 上已验收通过。

**用法**

```bash
# 个人配置复制到 jiangtao/run-self.md（已 gitignore）后:
source jiangtao/run-self.md  # 或手动 export 其中变量
dimos --viewer none \
  --unitree-webrtc-method remote \
  --unitree-username "$UNITREE_USERNAME" \
  --unitree-password "$UNITREE_PASSWORD" \
  --unitree-serial "$UNITREE_SERIAL" \
  --unitree-region cn \
  run unitree-go2
```

**回滚**

```bash
git checkout fe4b31a77^ -- dimos/core/global_config.py dimos/robot/unitree/connection.py dimos/robot/unitree/go2/connection.py
```

---

### 94b2cf002 — chore: post-merge uv.lock fix, docs, and Go2 defaults


| 字段 | 内容 |
|---|---|
| **时间** | 2026-07-10 10:40:43 +0800 |
| **分支** | `jtlinux` |
| **作者** | `jiangtao-huazhijian` |

**修改文件**

| 文件 | 改动 |
|---|---|
| `uv.lock` | 合并后去除重复 `[[package]]` 块，恢复 `uv lock` / `uv sync` |
| `jiangtao/bugfix.md` | 新增 §12 uv.lock 重复包、§13 gitlab venv/zenoh 踩坑 |
| `jiangtao/run.md` | 启动前 topsun_dimos venv 自检说明 |
| `dimos/core/global_config.py` | `free_avoid` 默认改为 `False` |
| `docs/cursor/20260709-upstream-merge-fdf3cb7d说明.md/.pdf` | 扩充 upstream merge 说明与 PDF |
| `commit-readme.md` | 补记 `adfaf2ffe` 文档提交 |
| `.cursor/rules/dimos-document-template.mdc` | 文档落点改为 `jiangtao/cursor/` |

**改进点**

1. 修复合并 upstream 后 `uv sync` 因 lock 重复包失败的问题。
2. 明确必须用 `topsun_dimos/.venv`，避免误用 gitlab venv 缺 zenoh。
3. Go2 默认关闭 FreeAvoid，与现场联调偏好一致。

**用法**

```bash
cd /home/jiangtao/huazhijian/topsun-bot/topsun_dimos
deactivate 2>/dev/null; source .venv/bin/activate
uv lock && uv sync --extra all
python -c "import zenoh; print('ok')"
```

**回滚**

```bash
git checkout 94b2cf002^ -- uv.lock jiangtao/bugfix.md dimos/core/global_config.py
```

---

### 77ca3291c — merge: integrate upstream/main fdf3cb7d on 7d2affd7d baseline
| **分支** | `jtlinux` |
| **作者** | `jiangtao-huazhijian` |

**修改文件**

| 文件 | 改动 |
|---|---|
| `dimos/core/transport.py` 等 500+ 文件 | 合入 upstream 75 commits（Zenoh、spy、WebRTC、scene、多层导航等） |
| `dimos/mapping/relocalization/module.py` | 保留 topsun fast ICP / point-to-plane 增强 |
| `dimos/robot/unitree/go2/blueprints/basic/unitree_go2_basic.py` | 保留 Mid-360 Rerun 配置 |
| `dimos/robot/unitree/go2/connection.py` | 合并 upstream rage mode + topsun FreeAvoid |
| `uv.lock` | 依赖锁同步 upstream |
| `docs/cursor/20260709-upstream-merge-fdf3cb7d说明.md` | 合并说明文档（dimos 模板） |

**改进点**

1. 在 `7d2affd7d` 文件同步基线上合入 upstream `fdf3cb7d`（75 commits），获得 Zenoh 传输、dimos spy、WebRTC SFU、Scene 包、多层导航等新能力。
2. 采用 `merge -X theirs` + topsun 补丁策略，避免 Git 历史分叉导致的 100+ 假冲突；实际手工处理 17 个文件。
3. 完整保留 topsun 重定位增强、Mid-360 blueprint、FreeAvoid 接线、examples/mapping-go2。

**用法**

```bash
# 确认合并结果
git log -1 --oneline   # 77ca3291c
wc -l dimos/mapping/relocalization/module.py   # 应为 850

# 更新依赖后启动
uv sync --extra all
dimos run unitree-go2 --robot-ip <GO2_IP> --viewer rerun

# 查看合并说明
cat docs/cursor/20260709-upstream-merge-fdf3cb7d说明.md
```

**回滚**

```bash
git reset --hard cadd99364
# 或仅回滚单个上游文件
git checkout cadd99364 -- dimos/core/transport.py
```

---
### adfaf2ffe - docs: add upstream merge fdf3cb7d report and update commit-readme

| 字段 | 内容 |
|---|---|
| **时间** | 2026-07-09 11:19:14 +0800 |
| **分支** | `jtlinux` |
| **作者** | `jiangtao-huazhijian` |

**修改文件**

| 文件 | 改动 |
|---|---|
| `docs/cursor/20260709-upstream-merge-fdf3cb7d说明.md` | 按 dimos 模板重写 upstream merge 说明（传输层/学习管线/Go2增强/建图导航/冲突解决，995 行） |
| `docs/cursor/20260709-upstream-merge-fdf3cb7d说明.pdf` | PDF 重生成（29 页 / 1.77 MB） |
| `commit-readme.md` | 追加 77ca3291c merge 记录 |

**改进点**

1. 按 dimos 文档模板重写 upstream merge 说明, 从「只列 PR」升级为「通俗篇 + 总览图 + 分主题详解（问题->答案->代码片段->对比表）+ 冲突解决 + 端到端实战 + cheatsheet」。
2. 深度调研三个方向（传输层、学习管线、LIO/Go2）, 引用实际源码路径和类名, 解释「为什么改」和「怎么落地」。

**用法**

```bash
# 查看重写后的文档
cat docs/cursor/20260709-upstream-merge-fdf3cb7d说明.md
# 或看 PDF
xdg-open docs/cursor/20260709-upstream-merge-fdf3cb7d说明.pdf
```

**回滚**

```bash
git checkout adfaf2ffe~1 -- docs/cursor/20260709-upstream-merge-fdf3cb7d说明.md
```

---


### bacc407a — feat(relocalization): fast ICP diagnostics and point-to-plane option

| 字段 | 内容 |
|---|---|
| **时间** | 2026-07-07 09:54:23 +0800 |
| **分支** | `jtlinux` |
| **作者** | `jiangtao-huazhijian` |

**修改文件**

| 文件 | 改动 |
|---|---|
| `dimos/mapping/relocalization/relocalize.py` | FastIcpDiagnostics、cached ICP 两阶段诊断、可配置 point_to_point / point_to_plane (TukeyLoss) |
| `dimos/mapping/relocalization/module.py` | cached_start 门槛、fast_icp_estimation 配置、结构化 ICP 日志 |
| `dimos/mapping/relocalization/test_*.py` | 新增/更新 fast ICP 与 point-to-plane 单测 |
| `jiangtao/scripts/record_boot_consistency.py` | 开机 odom/lidar/global_map 一致性录制与 compare |
| `jiangtao/20260706-开机odom一致性与yaw漂移分析报告.md` | 站定 yaw 漂移 (~0.31°/s) 与双流传 odom 分析 |
| `jiangtao/run.md` | 补充 boot consistency 用法与报告链接 |

**改进点**

1. 快速 ICP 输出 wall/full 前后 fitness、裁剪 ROI、初值偏差等诊断，便于排查固定 T 失败。
2. cached_start 增加 fitness / 平移 / yaw 验收门槛，避免 JSON 初值偏差过大仍发布 TF。
3. `fast_icp_estimation=point_to_plane` 与全局 RANSAC 后 ICP 使用相同 TukeyLoss 配置。
4. 附带 boot consistency 工具与报告，验证 Unitree odom yaw 漂移而 world 点云短期稳定。

**用法**

```bash
# 快速 ICP 切换 point-to-plane
dimos --robot-ip 192.168.12.1 run unitree-go2-relocalization \
  -o relocalizationmodule.map_file=recording_go2 \
  -o relocalizationmodule.fast_icp_estimation=point_to_plane

# 开机 odom 一致性录制
.venv/bin/python jiangtao/scripts/record_boot_consistency.py record --mode lcm --duration 25 --tag boot1
.venv/bin/python jiangtao/scripts/record_boot_consistency.py compare jiangtao/cache/boot_consistency/boot1_*
```

**回滚**

```bash
git checkout bacc407a^ -- dimos/mapping/relocalization/
git checkout bacc407a^ -- jiangtao/scripts/record_boot_consistency.py jiangtao/run.md
```

---

### 792a4c4d — fix(go2): remove invalid foxglove_config from vis_module blueprint

| 字段 | 内容 |
|---|---|
| **时间** | 2026-07-03 15:46:09 +0800 |
| **分支** | `jtlinux` |
| **作者** | `jiangtao-huazhijian` |

**修改文件**

| 文件 | 改动 |
|---|---|
| `dimos/robot/unitree/go2/blueprints/basic/unitree_go2_basic.py` | 删除 `vis_module()` 不支持的 `foxglove_config` 参数 |
| `jiangtao/run.md` | 真机命令改为 AP 热点 IP `192.168.12.1` |

**改进点**

1. Foxglove 已从 dimos 移除，保留 `foxglove_config=` 会在 `unitree-go2` / `unitree-go2-relocalization` 启动时报 `TypeError`。
2. 运行笔记与当前 Go2 热点联调环境对齐。

**用法**

```bash
dimos --robot-ip 192.168.12.1 run unitree-go2-relocalization \
  -o relocalizationmodule.map_file=recording_go2
```

**回滚**

```bash
git checkout 792a4c4d^ -- dimos/robot/unitree/go2/blueprints/basic/unitree_go2_basic.py
```

---

### 58cfbe41 — feat(relocalization): add offline debug script for relocalize step-by-step

| 字段 | 内容 |
|---|---|
| **时间** | 2026-07-03 11:07:23 +0800 |
| **分支** | `jtlinux` |
| **作者** | `jiangtao-huazhijian` |

**修改文件**

| 文件 | 改动 |
|---|---|
| `jiangtao/scripts/debug_relocalize.py` | 离线逐步调试 `relocalize()`：从本地 premap + 录制 db 构造输入，打印 Step 0–7 变量 |

**改进点**

1. 不连真机、不依赖 WebRTC/torch，仅用本地 `recording_go2.pc2.lcm` 与 `recording_go2.db` 验证重定位算法。
2. 逐步展开 RANSAC 候选、质心 yaw flip、重力过滤、wall-only 重排与 ICP，便于 IDE 断点对照 `relocalize.py` 阅读。
3. 末尾对比原版 `relocalize()` 输出，确认逐步逻辑与生产代码一致。

**用法**

```bash
cd ~/huazhijian/topsun-bot/topsun_dimos
.venv/bin/python jiangtao/scripts/debug_relocalize.py

# IDE: 选 launch 配置 "Debug: relocalize 离线算法 (推荐, 不连真机)"
```

**回滚**

```bash
git checkout 58cfbe41^ -- jiangtao/scripts/debug_relocalize.py
# 或
git reset --hard 58cfbe41^
```

---

### 88abe28d — fix: resolve log dir from cwd and restore Go2 lidar Rerun visibility

| 字段 | 内容 |
|---|---|
| **时间** | 2026-07-01 17:00:30 +0800 |
| **分支** | `jtlinux` |
| **作者** | `jiangtao-huazhijian` |

**修改文件**

| 文件 | 改动 |
|---|---|
| `dimos/constants.py` | 新增 `resolve_log_dir()`，优先 `DIMOS_LOG_DIR` / cwd 项目根 / 包根 |
| `dimos/robot/cli/dimos.py` | CLI 启动时用 `resolve_log_dir()` 写 per-run 日志 |
| `dimos/utils/logging_config.py` | 日志初始化同步使用 `resolve_log_dir()` |
| `dimos/robot/unitree/go2/blueprints/basic/unitree_go2_basic.py` | 恢复 Rerun `world/lidar` 可见，设 `max_hz=2` |
| `jiangtao/bugfix.md` | Go2 真机/WebRTC/录制/gtsam 等问题知识库 |
| `jiangtao/run.md` | 更新录制、PGO、重定位运行笔记 |

**改进点**

1. 修复 editable 安装指向其他 checkout 时日志写到错误目录的问题；在 `topsun_dimos` 下 `dimos run` 日志落到本仓库 `logs/`。
2. 恢复 Go2 basic blueprint 中 Rerun 雷达点云图层，避免误以为无 lidar 数据。
3. 沉淀现场排障记录到 `jiangtao/bugfix.md`，便于后续复用。

**用法**

```bash
# 默认: 在 dimos 项目根目录运行时日志写到 ./logs/
cd ~/huazhijian/topsun-bot/topsun_dimos
dimos --replay run unitree-go2-relocalization -o relocalizationmodule.map_file=recording_go2

# 显式指定日志目录
export DIMOS_LOG_DIR=/tmp/dimos-logs
dimos run unitree-go2-memory --robot-ip 10.10.197.155
```

**回滚**

```bash
git checkout 88abe28d^ -- dimos/constants.py dimos/robot/cli/dimos.py dimos/utils/logging_config.py \
  dimos/robot/unitree/go2/blueprints/basic/unitree_go2_basic.py jiangtao/bugfix.md jiangtao/run.md
# 或
git reset --hard 88abe28d^
```

---

### b3b88279 — feat(relocalization): document call flow and annotate relocalization internals

| 字段 | 内容 |
|---|---|
| **时间** | 2026-06-30 17:21:31 +0800 |
| **分支** | `jtlinux` |
| **作者** | `jiang.tao` |

**修改文件**

| 文件 | 改动 |
|---|---|
| `dimos/mapping/relocalization/module.py` | 增补重定位模块中文注释, 明确订阅链路、阈值和 TF/融合行为 |
| `dimos/mapping/relocalization/relocalize.py` | 增补多尺度 RANSAC+ICP 细节注释, 解释缓存、候选筛选与重排逻辑 |

**改进点**

1. 将 `unitree-go2-relocalization` 从 CLI 到重定位核心算法的调用链解释补齐, 降低后续联调排障成本。
2. 把 `map<-world` 与 `world<-map` 的坐标系转换意图写清楚, 降低 TF 使用和地图融合误用风险。
3. 明确 `RELOC_INTERVAL`、`MIN_LOCAL_POINTS`、`fitness_threshold` 对性能与稳定性的影响, 便于现场调参。

**用法**

```bash
# 真机启动重定位
dimos run unitree-go2-relocalization --robot-ip 192.168.123.161 \
  -o relocalizationmodule.map_file=recording_go2

# 回放验证重定位
dimos --replay --replay-db recording_go2 run unitree-go2-relocalization \
  -o relocalizationmodule.map_file=recording_go2
```

**回滚**

```bash
git checkout b3b88279^ -- dimos/mapping/relocalization/module.py dimos/mapping/relocalization/relocalize.py
# 或
git reset --hard b3b88279^
```

---

### 8c5936431 — docs: update commit-readme for af7df1395

| 字段 | 内容 |
|------|------|
| **时间** | 2026-06-24 |
| **分支** | `up-main` |

修正 changelog 自引用 SHA（`af7df1395`）。

---

### af7df1395 — docs: add commit-readme changelog and document fork commits

| 字段 | 内容 |
|------|------|
| **时间** | 2026-05-27 |
| **分支** | `up-main` |
| **作者** | jiangtao-huazhijian |

**修改文件**

| 文件 | 改动 |
|------|------|
| `commit-readme.md` | 新建：fork 提交变更日志，含快速回滚索引 |
| `.cursor/rules/commit-readme.mdc` | 新建（本地，不入库）：Agent 自动维护规则 |

**改进点**

1. 集中记录每次 fork 提交的改动、用法、回滚方式。
2. 写入 Cursor 规则，后续 commit/cherry-pick/merge/push 自动追加记录。

**用法**

```bash
# 查看所有 fork 提交记录
cat commit-readme.md

# 按 SHA 回滚单个文件
git checkout 58a9830b5 -- dimos/memory2/replay.py
```

---

### c40daacb0 — feat: Go2 robot speaker TTS, YOLO lookout, and connection timeout

| 字段 | 内容 |
|------|------|
| **时间** | 2026-05-27 |
| **分支** | `up-main` |
| **作者** | jiangtao-huazhijian |

**修改文件**

| 文件 | 改动 |
|------|------|
| `dimos/agents/skills/speak_skill.py` | Go2 扬声器 TTS、音频缓存、`speak_cached` |
| `dimos/perception/perceive_loop_skill.py` | `look_out_for` 连续模式、YOLO 快检、cooldown |
| `dimos/models/vl/create.py` | 注册 `openai` VL 模型 |
| `dimos/models/vl/types.py` | `VlModelName` 增加 `"openai"` |
| `dimos/robot/unitree/connection.py` | `publish_request` 10s 超时防卡死 |

**改进点**

1. **Go2 扬声器 TTS**：检测到 `GO2Connection` 时，TTS 音频通过 WebRTC AudioHub 上传到机器人扬声器播放；无连接时回退本地扬声器。
2. **低延迟告警**：`speak_cached(text)` 首次生成并上传音频（~2-3s），后续同文本即时播放（~0.2s）；`precache_audio(texts)` 可提前预热。
3. **YOLO 快检 lookout**：`look_out_for(..., use_yolo=True)` 用本地 YOLO 检测人体等 COCO 类（~30ms），替代 VLM（~3s）。
4. **连续监控**：`continuous=True` + `cooldown=10.0` 支持重复触发，适合门禁/巡逻告警。
5. **连接防卡死**：`publish_request` 加 10s 超时，避免 WebRTC 无响应时线程永久阻塞。

**用法**

```bash
# 启动 agentic blueprint（真机）
dimos run unitree-go2-agentic --robot-ip 192.168.123.161

# Agent 说话（走机器人扬声器）
dimos agent-send "say hello in Chinese"

# 预缓存固定告警语（低延迟）
dimos mcp call precache_audio --json-args '{"texts": ["Person detected", "Intruder alert"]}'
dimos mcp call speak_cached --arg text="Person detected"

# 连续 YOLO 人体检测 + 语音告警
dimos agent-send 'look out for a person and speak "Person detected" when found, use yolo and continuous mode with 10 second cooldown'
```

**依赖 / 前置**

- `OPENAI_API_KEY` 环境变量（TTS 用 `tts-1`）
- Go2 真机 WebRTC 连接正常（AudioHub API）
- YOLO 模式需 `ultralytics`（`uv sync --extra perception`）

**回滚**

```bash
git checkout 58a9830b5 -- dimos/agents/skills/speak_skill.py dimos/perception/perceive_loop_skill.py dimos/models/vl/create.py dimos/models/vl/types.py dimos/robot/unitree/connection.py
```

---

### 58a9830b5 — fix: prevent Rerun freeze and fix replay-db path resolution

| 字段 | 内容 |
|------|------|
| **时间** | 2026-05-27 14:41:09 +0800 |
| **分支** | `up-main` |
| **作者** | jiangtao-huazhijian |

**修改文件**

| 文件 | 改动 |
|------|------|
| `dimos/visualization/rerun/bridge.py` | `memory_limit` 从 `"25%"` 改为 `"2GB"` |
| `dimos/robot/unitree/go2/blueprints/basic/unitree_go2_basic.py` | Rerun `max_hz` 节流：map 2Hz、image 5Hz、costmap 2Hz |
| `dimos/memory2/replay.py` | `resolve_db_path` 统一走 `resolve_named_path`，支持本地 `.db` |
| `docs/team-git-workflow.md` | 新增团队 Git 工作流文档 |

**改进点**

1. **防 Rerun 卡死**：固定 2GB 内存上限 + 渲染节流，避免 WebRTC 视频轨 + 高频点云导致 Rerun OOM/冻结。
2. **replay-db 路径修复**：`--replay-db recording_go2` 现在能正确找到项目根目录的 `recording_go2.db`，不再误去 LFS 找内置数据。
3. **团队文档**：记录 fork 双远端、分支策略、PR 流程。

**用法**

```bash
# 重定位 replay 测试
dimos --replay --replay-db recording_go2 run unitree-go2-relocalization \
  -o relocalizationmodule.map_file=recording_go2_twopass_map

# 也可显式指定 .db 路径
dimos --replay --replay-db ./recording_go2.db run unitree-go2-basic
```

**回滚**

```bash
# 回滚到 upstream/main（不含本次修复）
git checkout b45e5d581
```

---

> 更早的 upstream 提交见 `git log upstream/main`。本文件从 fork 本地改动 `58a9830b5` 起记录。
