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
| 地标 JPEG 红蓝通道互换 | `d97b514b7` |
| VLM bbox 0-1000 尺度 / yaw 符号 / HFOV | `5b44f3322` |
| 按 run_id 命名的日志文件 | `5b44f3322` |
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

### d97b514b7 — fix(nav): encode landmark snapshots via to_opencv to avoid RGB/BGR swap

| 字段 | 内容 |
|---|---|
| **时间** | 2026-07-16 21:24:46 +0800 |
| **分支** | `relocalization-change-local-vlm` |
| **作者** | `jiangtao-huazhijian` |

**修改文件**

| 文件 | 改动 |
|---|---|
| `dimos/agents/skills/navigation.py` | `tag_location` / 沿途确认快照改用 `to_opencv()` 再 `imencode` |

**改进点**

1. Go2 相机帧为 RGB，`cv2.imencode` 按 BGR 写 JPEG；直接编码 `.data` 会导致红蓝互换。
2. 统一走 `Image.to_opencv()`，按 `format` 正确转换后再落盘。

**用法**

```bash
# 重新标记房间后新 jpg 颜色正常；旧快照需重拍
dimos tell '标记一下当前是办公室'
```

**回滚**

```bash
git checkout d97b514b7^ -- dimos/agents/skills/navigation.py
```

---

### 5b44f3322 — fix(nav): correct VLM bbox 0-1000 scale and unify yaw/HFOV

| 字段 | 内容 |
|---|---|
| **时间** | 2026-07-16 21:19:22 +0800 |
| **分支** | `relocalization-change-local-vlm` |
| **作者** | `jiangtao-huazhijian` |

**修改文件**

| 文件 | 改动 |
|---|---|
| `dimos/navigation/visual/query.py` | 修复 `_scale_bbox_to_image` 尺度歧义；prompt 明确要求 0-1000 |
| `dimos/navigation/visual/test_query.py` | 增加 1280×720 / 左中右 yaw 符号 / HFOV 测试 |
| `dimos/agents/skills/navigation.py` | 统一 HFOV=69°；`object_yaw = capture - offset`；VLM list prompt 坐标约定 |
| `dimos/utils/logging_config.py` | 日志文件名改为 `<run_id>.jsonl` |
| `dimos/core/log_viewer.py` / `dimos/robot/cli/dimos.py` | 按 run_id 解析日志路径 |
| `dimos/core/test_per_run_logs.py` 等 | 同步日志命名断言与文档 |
| `jiangtao/run.md` | 补充 `DIMOS_NAV_SPEED` / 居中容差说明 |

**改进点**

1. Qwen 0-1000 bbox 在 1280×720 上不再被误判为像素坐标，视觉伺服/二次确认角度不再系统性偏小。
2. 存物路径与沿途寻物路径的 yaw 符号、相机 HFOV 统一，避免左右镜像和 69°/90° 混用。
3. 每次 run 的日志文件与目录同名，便于 `dimos log` / `nav_log_summary` 定位。

**用法**

```bash
# 导航线速度建议降到 0.5，减小运动模糊
export DIMOS_NAV_SPEED=0.5
export DIMOS_CAMERA_HFOV_DEG=69   # 默认值，可覆盖

dimos --robot-ip 10.206.176.64 run unitree-go2-relocalization-memory-agentic-deepseek \
  --disable security-module \
  -o relocalizationmodule.map_file=recording_go2

# 日志：~/.local/state/dimos/logs/<run-id>/<run-id>.jsonl
dimos log -n 50
```

**回滚**

```bash
git checkout 5b44f3322^ -- \
  dimos/navigation/visual/query.py \
  dimos/navigation/visual/test_query.py \
  dimos/agents/skills/navigation.py \
  dimos/utils/logging_config.py \
  dimos/core/log_viewer.py \
  dimos/robot/cli/dimos.py
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
