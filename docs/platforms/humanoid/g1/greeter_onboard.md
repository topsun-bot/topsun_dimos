# G1 Orin 导览迎宾 — 开发文档

在笔记本 WebRTC 版迎宾（见 [greeter.md](./greeter.md)）验证通过后，将同一套迎宾能力部署到 **G1 机载 Jetson Orin**：
接入激光雷达建图、导航带路、地标到站介绍，声音与麦克风走机身硬件。

> **状态**：`unitree-g1-greeter-onboard` 蓝图已实现（阶段 A–E 代码完成，离线测试通过）。真机行为（DDS 手势/全身舞、Mid360 建图、标点带路、到站讲解）需在 Orin 上验证，见 §9 与 §13。

> **对话策略（与笔记本版一致）**：**不使用 LLM 回答开放问题**，仅走 `GreeterIntentRouter` 已配置的模板短路；模板外输入固定拒答。`McpClient` 可留在蓝图内但**不接 LLM 输入**（`llm_enabled=False`）。导航、带路、到站介绍也走**预置模板 / 标点时录入的讲解词**，不让 LLM 现场编造。

---

## 1. 与笔记本版的区别

| 能力 | 笔记本 + WebRTC | Orin 机载 |
|------|-----------------|-----------|
| 连接 | `G1Connection`（WebRTC，仅动作） | `G1HighLevelDdsSdk`（DDS 本地） |
| 移动 / 导航 | ❌ | ✅ `unitree_g1_nav_onboard` |
| 感知 | ❌ | Mid360 LiDAR + FastLIO2 |
| 麦克风 / 喇叭 | 笔记本 | Orin 音频 → G1 机身 |
| 跳舞 | 固定手臂舞步（`webrtc_arms`） | UniStore 全身舞（`onboard_random`） |
| 问路 | 固定拒答（未建图） | 标点后导航 + 到站播报**标点时录入的讲解词** |
| 开放问答 | 固定拒答（`llm_enabled=False`） | **同样**固定拒答，不用 LLM |

笔记本版适合快速迭代「对话 + 语音 + 手势」；Orin 版在相同对话策略上增加建图、标点、带路与到站讲解。

---

## 2. 对话策略（模板-only，与笔记本版相同）

与当前笔记本 greeter 一致，配置见 `_greeter_stack.py`：

```python
GREETER_ROUTER_KWARGS = {"llm_enabled": False}
```

| 输入类型 | 处理方式 |
|----------|----------|
| 你好 / 再见 | 固定迎宾 / 道别 + 挥手 |
| 挥手 / 比心 / 跳舞等 | 短台词 + 手势 / 全身舞 |
| 你是谁 | FAQ 固定回答 |
| 问路（未标点） | 固定「还没录入地图」 |
| 问路 / 带路（已标点） | 模板短路 → 导航（见 §3） |
| **其它任意问题** | **固定拒答**，不调用 LLM |

Orin 版**不**恢复 `llm_enabled=True`。`McpServer` / `McpClient` 可保留（便于日后扩展），但运行时**不向** `llm_human_input` 转发任何用户输入。

模板外拒答文案（可配置 `llm_fallback_template`）：

> 抱歉，这个我还不会。您可以试试说你好、挥手或跳个舞。

---

## 3. 建图、标点与讲解词

Orin 版在笔记本迎宾能力之外，增加**地标标点 + 讲解词持久化**。讲解词在**标点时一次性录入**，之后问路、带路、到站均播这段固定文字，**不经 LLM 生成**。

### 3.1 标点时要记什么

工作人员在每个地点标点时，除名称与地图坐标外，**必须同时保存该点的讲解词**（`intro_script`）：

| 字段 | 说明 | 示例 |
|------|------|------|
| `name` | 地标名称（问路关键词） | `前台`、`厕所`、`展厅A` |
| `pose` | 地图坐标（由 `tag_location` / 导航栈写入） | 自动 |
| `intro_script` | **到站后播报的讲解词**（固定模板，1～3 句） | 「这里是前台，请在此登记来访信息。」 |

可选：同义词列表（如 `卫生间` → `厕所`），供 `GreeterIntentRouter` 问路短路匹配。

### 3.2 讲解词用在哪

1. **到站介绍**：导航到达标点后，自动 `speak(intro_script)`（可选手势）；不调用 LLM。
2. **问路短路**（已标点）：客人问「厕所在哪」→ 可播报简短指引模板 + 可选发起导航；**位置事实只来自已标点数据**，不编造。
3. **TTS 预缓存**：标点完成或蓝图启动加载地标表后，把所有 `intro_script` 加入 `prewarm_texts()`，与寒暄 / 手势台词一并预合成。

### 3.3 数据流（标点 → 使用）

```
工作人员现场标点
  → tag_location(name="前台", intro_script="这里是前台……")
  → 持久化：名称 + 坐标 + 讲解词（地标表 / spatial memory）
  → 触发 TTS 预缓存该讲解词

客人说「带我去前台」
  → GreeterIntentRouter 匹配问路/带路模板
  → navigate_with_text("前台")（不经 LLM）
  → 到站 → speak(intro_script)   # 播标点时录入的讲解词
```

### 3.4 与 `GreeterIntentRouter` 的关系

- 未标点任何地点：`location_landmarks_available=False`（与笔记本相同）→ 问路走 `location_unknown_template`。
- 至少标点一处后：`location_landmarks_available=True` → 问路 / 带路走地标表 + 导航短路，**仍不经过 LLM**。
- 讲解词内容**仅**来自标点时写入的 `intro_script`，禁止运行时让 LLM 改写或扩写。

---

## 4. 目标功能

1. **机载迎宾**：保留 `GreeterIntentRouter` 短路（寒暄、手势、FAQ、跳舞）；**`llm_enabled=False`**。
2. **免提语音**：`VadVoiceInput` + 机载 Whisper（建议 `tiny`）。
3. **建图导航**：复用 `unitree_g1_nav_onboard`（FastLIO2 + nav_stack + MovementManager）。
4. **标点 + 讲解词**：`tag_location(name, intro_script=...)` 同时写入坐标与讲解词；持久化并可重载。
5. **带路导览**：「带我去前台」→ 模板短路 → 导航；到站 `speak(intro_script)`。
6. **问路升级**：标点后 `location_landmarks_available=True`；已录地标走路标 / 导航模板，未录地标仍固定拒答。
7. **TTS 预缓存**：`collect_greeter_prewarm_texts()` + 各地标 `intro_script`。

---

## 5. 目标蓝图（待实现）

建议名称：`unitree-g1-greeter-onboard`

```
unitree-g1-greeter-onboard
  = FastLio2 + nav_stack + MovementManager + G1HighLevelDdsSdk   # 来自 nav_onboard
  + GreeterSkillContainer + SpeakSkill + GreeterIntentRouter
  + VadVoiceInput
  + McpServer + McpClient（llm_enabled=False，不接开放对话）
  + （导航技能容器：tag_location(name, intro_script=...) / navigate_with_text）
```

参考文件：

- 导航：`dimos/robot/unitree/g1/blueprints/navigation/unitree_g1_nav_onboard.py`
- 迎宾：`dimos/robot/unitree/g1/blueprints/agentic/unitree_g1_greeter_hands_free.py`
- 共享配置：`dimos/robot/unitree/g1/blueprints/agentic/_greeter_stack.py`

Orin 蓝图**不要**包含 `G1Connection`（WebRTC）；移动与手臂走 DDS。

---

## 6. Orin 环境

### 6.1 SSH 与网络

```bash
# 笔记本有线连机器人
ssh -L 3030:localhost:3030 unitree@192.168.123.164
# 密码: 123
```

更多网络说明见 [index.md](./index.md)。

### 6.2 关键 IP

| 设备 | 默认 IP |
|------|---------|
| Orin | `192.168.123.164` |
| Mid360 | `192.168.123.120` |

环境变量（导航栈已使用）：

```bash
export LIDAR_HOST_IP=192.168.123.164
export LIDAR_IP=192.168.123.120
```

### 6.3 运行导航栈（已有）

```bash
source .venv/bin/activate
dimos --rerun-host 0.0.0.0 run unitree-g1-nav-onboard
```

笔记本用 `dimos-viewer` 连 Rerun 查看建图（见 `index.md`）。

### 6.4 音频

将 Orin 默认音频输出指到 G1 喇叭（ALSA / PulseAudio 配置），`SpeakSkill` 无需改代码。
机载麦作为 `VadVoiceInput` 默认输入设备。

### 6.5 机器人姿态

导航前 G1 须处于 Sport 平衡模式（手柄 **R2 + A** 等），见 [index.md](./index.md) 第 3 节。

---

## 7. 实现任务清单

### 阶段 A — 空壳蓝图

- [x] 新建 `dimos/robot/unitree/g1/blueprints/agentic/unitree_g1_greeter_onboard.py`
- [x] `autoconnect` 合并 `nav_onboard` 与 greeter 模块，`GreeterSkillContainer` 注入 DDS Spec（`G1HighLevelDdsSdk` 结构化满足 `G1ConnectionSpec`）
- [ ] SSH 上 `dimos run unitree-g1-greeter-onboard` 能启动不崩溃 *(真机)*
- [x] `pytest dimos/robot/test_all_blueprints_generation.py` 注册蓝图

### 阶段 B — 迎宾链路

- [x] DDS 下 `greet_guest` / `execute_arm_command` / `perform_dance`（全身舞）可用：`G1HighLevelDdsSdk.publish_request` 接入 `G1ArmActionClient`（7106 预设手势、7108/7113 全身舞）
- [x] `VadVoiceInput` + `GreeterIntentRouter`；`prewarm_texts` 预缓存固定台词
- [ ] 声音从 G1 喇叭出；说「你好」「挥挥手」与笔记本版行为一致 *(真机)*

### 阶段 C — 标点、讲解词与导览

- [x] 扩展 `tag_location(name, intro_script, synonyms)`（G1 专用封装 `GreeterTourSkillContainer`）：标点时**同时持久化**坐标 + 讲解词
- [x] 地标表可重载（`GreeterLandmarkStore` 写 JSON，重启后 `load()` 恢复名称/坐标/`intro_script`）
- [x] 标点或加载地标后，将所有 `intro_script` 加入 `prewarm_texts()`
- [x] 有标点时 `GreeterIntentRouterConfig(location_landmarks_available=True)`
- [x] 「厕所在哪 / 带我去前台」→ 模板短路 → `GreeterTourSkillContainer.handle_location_query`（**不经 LLM**）
- [x] 未录入地标名称仍走固定「未录入」模板，不编造位置

### 阶段 D — 到站介绍

- [x] 订阅 `corrected_odometry` 距离到达 → `speak(intro_script)`（播标点录入的讲解词，可选 `execute_arm_command`）
- [x] 确认 `llm_enabled=False`：模板外问题仍固定拒答，与笔记本版一致
- [x] **未**新建依赖 LLM 的 `greeter_tour_system_prompt`；移动 / 导航 / 讲解均由模板与标点数据驱动

### 阶段 E — 文档与测试

- [x] 单元测试：`test_greeter_landmark_store.py`、`test_greeter_tour_skill.py`（地标查询、到站判定、带路/讲解决策）
- [x] 更新本文档「验收」一节为实测记录（见 §9.1）
- [x] Troubleshooting：DDS、LiDAR、音频设备、Sport 模式（见 §13）

---

## 8. 代码约束

遵循仓库 `AGENTS.md`：

| 规则 | 说明 |
|------|------|
| `@skill` | 强制 docstring、参数类型、`str` 返回值 |
| Spec 注入 | 跨模块 RPC 用 `Spec` Protocol，禁止字符串硬编码模块名 |
| `all_blueprints.py` | 只通过生成测试更新，禁止手改 |
| 最小 diff | 不重写 `greeter_intent_router` / `greeter_skill` 核心逻辑 |
| 秘密 | 不提交 `.env`、WebRTC AES key、内网 IP |

### 关键模块索引

```
dimos/robot/unitree/g1/
├── greeter_intent_router.py       # location_landmarks_available
├── greeter_skill.py               # is_onboard_compute() / perform_dance
├── greeter_system_prompt.py       # 无移动 prompt（参考）
├── blueprints/navigation/unitree_g1_nav_onboard.py
├── blueprints/agentic/_greeter_stack.py
├── effectors/high_level/dds_sdk.py
└── system_prompt.py               # navigate_with_text / tag_location

dimos/agents/skills/speak_skill.py # prewarm_texts()
dimos/agents/voice_input.py        # VadVoiceInput
dimos/agents/skills/               # NavigationSkillContainer
```

### 舞蹈模式

`greeter_skill.resolve_dance_style()` 在 Orin 上（检测 `/etc/nv_tegra_release`）自动选 `onboard_random`，无需手改。

### TTS 预缓存

`GreeterIntentRouter.start()` 已调用 `collect_greeter_prewarm_texts()`；Orin 版在加载地标表后，把各地标 `intro_script` **追加**进预缓存列表。

Orin 蓝图沿用 `GREETER_ROUTER_KWARGS = {"llm_enabled": False}`（可与笔记本共用 `_greeter_stack.py`，或单独 `greeter_onboard_stack.py` 显式写明）。

---

## 9. 验收标准（真机）

在 Orin SSH 会话中：

```bash
dimos run unitree-g1-greeter-onboard
```

| 测试 | 预期 |
|------|------|
| 说「你好」 | 固定迎宾语 + 挥手，G1 喇叭出声 |
| 说「跳个舞」 | 全身舞（非手臂小舞步） |
| 标点「前台」+ 讲解词 | 名称、坐标、`intro_script` 均持久化；日志有讲解词 TTS 预缓存 |
| 说「带我去前台」 | 模板短路 → 导航；到站播报**标点时录入**的讲解词（非 LLM 生成） |
| 说「厕所在哪」（已标点） | 模板 / 导航指引，不编造 |
| 说「厕所在哪」（未标点） | 固定拒答 |
| 说「今天天气怎么样」 | 固定拒答（与笔记本版相同，不走 LLM） |
| `dimos list` | 可见 `unitree-g1-greeter-onboard` |

### 9.1 离线自测结果（无真机）

| 检查 | 结果 |
|------|------|
| 蓝图注册（`test_all_blueprints_generation.py`，CI 模式校验当前性） | ✅ 通过；`dimos list` 可见 `unitree-g1-greeter-onboard` |
| 单元测试 `test_greeter_landmark_store.py` + `test_greeter_tour_skill.py` + `test_greeter_intent_router.py` | ✅ 29 passed |
| ruff format + check | ✅ 通过（greeter 文件 RUF001 中文标点已加 per-file-ignore） |
| mypy（新增/改动文件） | ✅ no issues |
| 新模块离线导入（store / tour / spec / router） | ✅ OK |

> 真机项（DDS 手势/全身舞、Mid360 建图、麦克风、标点带路、到站讲解、声音从 G1 喇叭出）需在 Orin 上按 §9 表与 §13 验证；本机未安装 `unitree_sdk2py`，无法完整 `.build()`。

### 9.2 实现要点

- **蓝图**：`blueprints/agentic/unitree_g1_greeter_onboard.py` = `unitree_g1_nav_onboard` + `GreeterSkillContainer` + `SpeakSkill` + `VadVoiceInput` + `GreeterIntentRouter`(`llm_enabled=False`, `location_landmarks_available=True`) + `GreeterTourSkillContainer` + `McpServer`/`McpClient`。手臂/移动走 DDS（无 WebRTC）。
- **DDS 手势/舞蹈**：`effectors/high_level/dds_sdk.py` 的 `publish_request` 在 `topic == rt/api/arm/request` 时转给 `G1ArmActionClient`（`ExecuteAction` 预设手势；`_Call(7108/7113)` 全身舞起停）。
- **地标**：`greeter_landmark_store.py`（`Landmark` + JSON 持久化/重载/最长关键词匹配）；`greeter_tour_skill.py` 的 `tag_location` 标点录入坐标 + `intro_script` + 同义词并预缓存。
- **问路/带路**：`GreeterIntentRouter` 新增可选 `_tour` 注入；命中地标→带路（发布 `goal`）或讲解，未命中→固定「未录入」；笔记本三蓝图无 `_tour`，行为不变。
- **到站**：`GreeterTourSkillContainer` 订阅 `corrected_odometry`，与目标平面距离 ≤ `arrival_threshold_m` 时 `speak(intro_script)` + 可选手势。

---

## 10. AI 开发 Agent 提示词

将以下整段复制到新的 Cursor 对话作为第一条消息，即可在仓库内增量实现任务 4：

---

你是 DimOS 仓库里的实现工程师，负责 **G1 Orin 导览迎宾**（任务 4）。

**已完成（勿破坏）**：笔记本版蓝图 `unitree-g1-greeter` / `-voice` / `-hands-free`，模块 `greeter_intent_router.py`、`greeter_skill.py`、`speak_skill.prewarm_texts()`。**对话策略：`llm_enabled=False`，仅模板短路，模板外固定拒答。**

**目标**：新建 `unitree-g1-greeter-onboard`，在 Orin（`192.168.123.164`）上 DDS + Mid360 导航 + **相同模板-only 对话策略** + 标点带路 + 到站讲解。**Orin 版同样不使用 LLM 回答开放问题。**

**标点要求**：`tag_location(name, intro_script=...)` 标点时同时保存坐标与讲解词；到站播 `intro_script`；讲解词纳入 TTS 预缓存。禁止 LLM 生成或改写讲解内容。

**实现顺序**：空壳蓝图 → DDS 手势/说话 → VadVoiceInput + Router（`llm_enabled=False`）→ 标点持久化 + intro_script → 问路模板 + 导航 → 到站 speak(intro_script)。

**硬约束**：`AGENTS.md`、`@skill` 规范、Spec 注入、`all_blueprints.py` 生成测试、最小 diff、不提交密钥、**不启用 `llm_enabled=True`**。

**验收**：「你好」迎宾、「跳个舞」全身舞、标点后「带我去前台」到站播录入讲解词、「天气怎么样」拒答、`pytest` + `mypy` + `ruff` 通过。

先阅读 `docs/platforms/humanoid/g1/greeter_onboard.md` 与 `greeter.md`，再动手。从阶段 A 空壳蓝图开始。

---

## 11. 禁止事项

- 在 Orin 蓝图使用 WebRTC 做移动或读传感器
- **使用 LLM 回答开放问题或生成讲解词**（与笔记本版策略一致，保持 `llm_enabled=False`）
- 让 LLM 编造未录入的地标位置
- 标点时只记坐标、不记 `intro_script`
- 破坏现有三个笔记本 greeter 蓝图
- 大范围重构 `nav_onboard` 或 `SpeakSkill`

---

## 12. 相关文档

- [greeter.md](./greeter.md) — 笔记本无移动迎宾（当前可用）
- [index.md](./index.md) — G1 SSH、Sport 模式、导航栈
- [greeter_review.md](./greeter_review.md) — 迎宾代码审查记录

---

## 13. Troubleshooting（真机）

| 现象 | 排查 |
|------|------|
| 手势/舞蹈不动作 | 手臂动作仅在 FSM 状态 500/501/801（Walk/Run）下生效；先确认 G1 处于 Sport 平衡模式（手柄 **R2 + A**，见 [index.md](./index.md) §3）。日志看 `G1 arm action client initialized`；`publish_request` 返回 `code != 0` 表示服务拒绝（多为 FSM 状态不对）。 |
| `greet_guest` 有声音但不挥手 | 说明 TTS 正常但 `G1ArmActionClient` 未连上：确认 DDS 网卡 `network_interface`（默认 `eth0`）与 `ChannelFactoryInitialize` 成功；检查 `rt/api/arm/request` 是否被占用。 |
| 跳舞只挥手不全身 | 全身舞需 Orin 机载（检测 `/etc/nv_tegra_release`）；非 Orin 会退化为 `webrtc_arms`。确认在机器人 Orin 上运行，且 `dance_names` 为有效 UniStore 动作名（`list_actions` 查询）。 |
| 建图无输出 / 无 `corrected_odometry` | 确认 `LIDAR_HOST_IP` / `LIDAR_IP` 环境变量、Mid360 上电与网络可达（`ping 192.168.123.120`）；用 `dimos-viewer` 连 Rerun 看 FastLIO2 点云。 |
| 标点报「尚未收到里程计数据」 | 导航栈（PGO）尚未发布 `corrected_odometry`；等建图起来、机器人移动几步后再 `tag_location`。 |
| 「带我去X」不导航 | ① 该地标是否已 `tag_location`（`list_landmarks` 查）；② 句子需含带路关键词（带我去/带路/领我…），否则只播讲解词；③ 路径被占据/不可达时 `SimplePlanner` 不会前进。 |
| 到站不播讲解词 | 平面距离需 ≤ `arrival_threshold_m`（默认 0.8 m）；若机器人停在阈值外（被障碍挡住），不会触发。标点坐标与导航目标同用 `corrected_odometry`/世界系——若 PGO 回环跳变较大可能偏移。 |
| 声音不从 G1 喇叭出 | 把 Orin 默认音频输出指到 G1 喇叭（ALSA/PulseAudio），见 §6.4；配 `DASHSCOPE_API_KEY` 用 CosyVoice 中文音色。 |
| 麦克风不灵敏 / 误触发 | `VadVoiceInput` 默认输入设备需为机载麦；必要时调 VAD 阈值或换 `whisper_model`。 |
| 标点丢失 | 地标表持久化在 `~/.local/state/dimos/greeter_landmarks.json`（可配 `GreeterTourSkillConfig.store_path`）；换用户/机器需迁移该文件。 |
