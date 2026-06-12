# G1 迎宾对话机器人(无移动版)

一台**原地接待/迎宾**机器人:由 LLM 智能体驱动,跟客人对话并做手臂手势(挥手、比心等)。
刻意**不含移动、导航、相机、激光雷达**,因此可以在**连接真机 G1 的笔记本**上直接开发与运行,
是上机部署前验证"对话 + 语音 + 手势"链路的最小可用形态。

---

## 1. 功能概述

- **对话**:LLM 智能体(默认 `gpt-4o`)接收文字或语音提问,理解后用语音回答。
- **语音输出(TTS)**:通过 `SpeakSkill` 把回答说出来,声音从运行 DimOS 的机器发出
  (开发期是笔记本音箱;上机后指向 G1 喇叭即可)。
- **手臂手势**:迎宾挥手、告别挥手,以及握手/比心/鼓掌等 14 种单独手势,全部经 WebRTC 下发。
- **两种输入方式**:
  - **打字版**:`dimos agent-send "..."` 发文字。
  - **语音版**:客人对麦克风说话 → Whisper 识别 → 智能体。

> 安全:本功能**不移动机器人**,但手臂会动。系统提示词中明确禁止移动/导航,避免智能体
> 幻想出做不到的动作。

---

## 2. 提供的蓝图

| 蓝图名 | 输入方式 | 说明 |
|---|---|---|
| `unitree-g1-greeter` | 文字(`agent-send`) | 最小迎宾,开发期最稳 |
| `unitree-g1-greeter-voice` | 语音(麦克风→Whisper) | 在打字版基础上加 `VoiceInput` |

两个蓝图的组成:

```
unitree-g1-greeter        = G1Connection + GreeterSkillContainer + SpeakSkill
                            + McpServer + McpClient
unitree-g1-greeter-voice  = 上述 + VoiceInput
```

---

## 3. 数据流

**打字版**

```
你打字 (dimos agent-send) → /human_input → McpClient(智能体)
        → 调用技能(speak / welcome_gesture / ...) → 语音 + 手势
```

**语音版**

```
客人说话 → 麦克风 → Whisper 识别 → /human_input → McpClient(智能体)
        → speak(TTS) + 手势(WebRTC) → 笔记本音箱出声 + 机器人挥手
```

---

## 4. 暴露给智能体的技能

| 技能 | 作用 |
|---|---|
| `speak(text)` | 把文字说出来(每次回应都用) |
| `welcome_gesture()` | 迎宾挥手 |
| `goodbye_gesture()` | 告别挥手 |
| `execute_arm_command(command_name)` | 单个手势:Handshake / HighFive / Hug / HighWave / Clap / FaceWave / LeftKiss / ArmHeart / RightHeart / HandsUp / XRay / RightHandUp / Reject / CancelAction |

> 故意**没有**任何移动/导航技能。

---

## 5. 新增的代码

### 5.1 `dimos/robot/unitree/g1/greeter_skill.py`(新增)

迎宾手势技能容器 `GreeterSkillContainer`(无移动)。

- 模块依赖 `_connection: G1ConnectionSpec`(由框架按 blueprint 连线自动注入),经
  `publish_request` 下发宇树手臂动作。
- 复用 `effectors/high_level/commands.py` 的 `execute_g1_command` / `ARM_COMMANDS` 等。
- 手势序列以 `(命令名, 停顿秒数)` 定义,停顿用于等上一个预设动作播放完。
- 暴露技能:`welcome_gesture()`、`goodbye_gesture()`、`execute_arm_command(command_name)`。
  其中 `execute_arm_command` 的 docstring 在类定义后动态拼接出全部命令清单(供 LLM 选择)。

### 5.2 `dimos/robot/unitree/g1/greeter_system_prompt.py`(新增)

无移动版迎宾系统提示词 `GREETER_SYSTEM_PROMPT`(中文)。

- 强约束:**不可移动/导航**、安全第一、用客人语言、简洁一两句。
- 列出可用技能,指导智能体"到访即迎接、结束即道别"的行为。

### 5.3 `dimos/robot/unitree/g1/blueprints/agentic/unitree_g1_greeter.py`(新增)

打字版最小蓝图 `unitree_g1_greeter`。

- 组合 `G1Connection`(WebRTC)+ `GreeterSkillContainer` + `SpeakSkill` + `McpServer` + `McpClient`。
- `McpClient` 使用 `GREETER_SYSTEM_PROMPT`,默认模型 `gpt-4o`。
- `global_config(n_workers=4, robot_model="unitree_g1")`。

### 5.4 `dimos/agents/voice_input.py`(新增)

本地麦克风语音输入模块 `VoiceInput`。

- 链路:`SounddeviceAudioSource`(麦克风)→ `AudioNormalizer` → `KeyRecorder` →
  `WhisperNode`(识别)→ 发布到 `/human_input`(与 `agent-send` 同话题)。
- 配置 `VoiceInputConfig`:`whisper_model`(默认 `base`)、`language`(默认 `zh`)、
  `device_index`、`always_listen`。
- **按键说话**:终端按 Enter 开始录音、再按 Enter 停止。需**前台运行**(读 stdin)。
- Whisper 为**延迟导入**(在 `start()` 内),未安装也不影响蓝图导入。

### 5.5 `dimos/robot/unitree/g1/blueprints/agentic/unitree_g1_greeter_voice.py`(新增)

语音版蓝图 `unitree_g1_greeter_voice`。

- 在打字版基础上加入 `VoiceInput`。
- `global_config(n_workers=6, robot_model="unitree_g1")`。

---

## 6. 修改的代码

### `dimos/robot/all_blueprints.py`(自动生成,非手改)

由 `pytest dimos/robot/test_all_blueprints_generation.py` 重新生成,新增了两条蓝图
和两个模块的注册项:

- 蓝图:`unitree-g1-greeter`、`unitree-g1-greeter-voice`
- 模块:`greeter-skill-container`、`voice-input`

> 该文件是自动生成的,新增/重命名蓝图后需重跑上面的生成测试。

---

## 7. 启动与使用

### 打字版

```bash
source .venv/bin/activate
dimos run unitree-g1-greeter --robot-ip <你的G1_IP>

# 另开终端
dimos agent-send "你好"            # 智能体回复欢迎语 + 挥手
dimos mcp list-tools               # 查看可用技能
dimos mcp call welcome_gesture     # 直接触发挥手(不经过 LLM)
dimos mcp call speak --arg text="测试"
```

### 语音版(前台运行)

```bash
source .venv/bin/activate
dimos run unitree-g1-greeter-voice --robot-ip <你的G1_IP>
# 按 Enter 开始说话 → 说"你好,你能做什么" → 再按 Enter 结束
```

---

## 8. 前置条件 / 依赖

- **模型 Key**:`.env` 配 `OPENAI_API_KEY`(默认 gpt-4o);如需国内方案见下方。
- **TTS**:OpenAI 或 DashScope(配 `DASHSCOPE_API_KEY` 时 `SpeakSkill` 自动切中文音色 CosyVoice)。
- **语音版额外需要**:Whisper 后端(`uv sync --extra whisper`,本仓库已含 `openai-whisper`)、
  可用麦克风。
- **音频输出**:开发期=笔记本音箱;上机=把 Orin 默认音频输出指向 G1 喇叭(无需改代码)。

### 切换 DeepSeek(可选)

给 `McpClient.blueprint(...)` 传 `model="deepseek-v4-pro", model_provider="openai"`
(参考 `unitree_g1_agentic_deepseek.py`),并设置:

```bash
OPENAI_API_KEY=sk-deepseek-xxxx
OPENAI_BASE_URL=https://api.deepseek.com
```

---

## 9. 已知限制 / 后续

- **欢迎语非固定**:由 LLM 现场生成(受系统提示词约束),且**被触发才说**(收到输入才回应),
  启动后不会自动喊欢迎。需要固定欢迎语或自动迎宾可再加。
- **语音是按键触发**:目前为 push-to-talk(按 Enter)。免提需接入 VAD/音量门控。
- **手势在仿真中不动作**:MuJoCo 仿真连接的 `publish_request` 为空操作,手势只在真机生效。
- **无移动/感知**:带路、人脸/人体检测、建图等不在本功能范围,属于后续"导览"阶段(需 Orin + 雷达/相机)。见 [greeter_onboard.md](./greeter_onboard.md)。

---

## 10. 本地自测结果

- 蓝图注册:✅(`dimos list` 可见两条)
- 蓝图导入+组装为 Blueprint:✅(`test_all_blueprints.py`)
- mypy 严格类型:✅
- ruff lint + format:✅

> 完整 `.build()`(部署进 worker + 连真机 WebRTC)需连上 G1 才能验证。
