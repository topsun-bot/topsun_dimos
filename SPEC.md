# SPEC: `dimos run unitree-go2` 汪汪叫功能 — MiniMax TTS 版

## 变更历史

| 版本 | 日期 | 变更内容 | 作者 |
|------|------|----------|------|
| v1.0 | 2026-05-13 | 初始规格：OpenAI TTS 方案 | manager001 |
| v2.0 | 2026-05-13 | **迁移至 MiniMax TTS**（OpenAI API Key 额度耗尽） | manager001 |

---

## 1. 背景

### 1.1 原始需求
运行 `dimos run unitree-go2` 时，在机器人启动完成后自动播放一段狗叫声，确认连接成功。

### 1.2 变更原因
- **OpenAI API Key 额度已耗尽**，无法继续使用 OpenAI TTS 服务。
- `cr/MINIMAX_TTS_GUIDE.md` 中已有已验证的 MiniMax TTS 方案，需更新规格和实现。
- MiniMax TTS 提供中文优化（`language_boost: Chinese`），"汪汪"发音更自然。

---

## 2. 功能需求（不变）

### 2.1 触发时机
- **启动完成后自动播放**：在蓝图所有模块 `start()` 完成后、进入主循环前触发
- 仅播放一次，不循环

### 2.2 声音内容
- **文本**：`"汪汪 汪汪 汪汪 汪汪汪"`（5声狗叫，活泼感更强）
- **语速**：`speed=1.0`（MiniMax 默认，语速适中）

> **变更说明**：v1.0 使用 `"汪汪 汪汪"` + `speed=1.3`（OpenAI）。v2.0 改用 MiniMax `female-tianmei` 音色，5声更自然。

### 2.3 运行模式差异（不变）

| 模式 | 行为 |
|------|------|
| 实机运行 (`--robot-ip`) | 通过 WebRTC AUDIO_HUB API 上传到机器人扬声器播放 |
| Replay 模式 (`--replay`) | 通过本地 sounddevice 播放 |
| Simulation 模式 | 通过本地 sounddevice 播放 |

---

## 3. 技术方案变更

### 3.1 变更概览

| 项目 | v1.0 (OpenAI) | v2.0 (MiniMax) |
|------|---------------|----------------|
| TTS 服务 | OpenAI `tts-1` | MiniMax `speech-2.8-hd` |
| API Endpoint | `api.openai.com` | `api.minimaxi.com/v1/t2a_v2` |
| 音色 | `echo` / `onyx` | `female-tianmei`（甜美女性音色） |
| 返回格式 | raw MP3 bytes | hex-encoded MP3 JSON |
| 认证方式 | `OPENAI_API_KEY` | `MINIMAX_API_KEY` |
| 文本内容 | `"汪汪 汪汪"` | `"汪汪 汪汪 汪汪 汪汪汪"` |
| 语速 | `speed=1.3` | `speed=1.0` |
| 采样率 | 24000 Hz | 32000 Hz |

### 3.2 MiniMax TTS API 详情

**Endpoint**：`POST https://api.minimaxi.com/v1/t2a_v2`

**Headers**：
```text
Authorization: Bearer $MINIMAX_API_KEY
Content-Type: application/json
```

**Payload**：
```json
{
  "model": "speech-2.8-hd",
  "text": "汪汪 汪汪 汪汪 汪汪汪",
  "stream": false,
  "voice_setting": {
    "voice_id": "female-tianmei",
    "speed": 1.0,
    "vol": 1.0,
    "pitch": 0,
    "text_normalization": true
  },
  "audio_setting": {
    "sample_rate": 32000,
    "bitrate": 256000,
    "format": "mp3",
    "channel": 1
  },
  "language_boost": "Chinese"
}
```

**响应处理**：
```python
response = requests.post(...)
data = response.json()
base = data.get("base_resp") or {}
if base.get("status_code") != 0:
    raise RuntimeError(f"MiniMax error: {base}")

audio_hex = data["data"]["audio"].strip().replace(" ", "")
audio_bytes = bytes.fromhex(audio_hex)  # MP3 bytes
```

### 3.3 模块设计（变更后）

```python
# dimos/robot/unitree/go2/modules/startup_bark.py

class StartupBarkModule(Module):
    """Plays a bark sound on Go2 robot startup using MiniMax TTS."""

    _tts_node: MiniMaxTTSNode | None = None   # <-- 变更：MiniMaxTTSNode
    _audio_output: SounddeviceAudioOutput | None = None
    _timer: threading.Timer | None = None

    @rpc
    def start(self) -> None:
        super().start()
        self._timer = threading.Timer(2.0, self._bark)
        self._timer.start()

    def _bark(self) -> None:
        try:
            if global_config.replay or global_config.simulation:
                self._bark_local()
            else:
                self._bark_on_robot()
        except Exception as e:
            logger.error(f"Error during bark: {e}")

    def _bark_local(self) -> None:
        """Replay/Sim 模式：本地播放"""
        if self._tts_node is None:
            self._tts_node = MiniMaxTTSNode()   # <-- 变更
        if self._audio_output is None:
            self._audio_output = SounddeviceAudioOutput(sample_rate=32000)  # <-- 变更
        ...

    def _bark_on_robot(self) -> None:
        """实机模式：生成音频并上传到机器人"""
        audio_bytes = self._generate_audio("汪汪 汪汪 汪汪 汪汪汪")
        # MP3 -> WAV (22050Hz PCM_16) -> WebRTC upload  <-- 保持现有流程
        ...
```

### 3.4 新增 MiniMaxTTSNode（适配音频管道）

新增 `dimos/stream/audio/tts/node_minimax.py`，实现 `AbstractTextConsumer` + `AbstractAudioEmitter` + `AbstractTextEmitter` 接口，与现有 `OpenAITTSNode` 保持相同接口：

```python
class MiniMaxTTSNode(AbstractTextConsumer, AbstractAudioEmitter, AbstractTextEmitter):
    def __init__(self, api_key: str | None = None, ...):
        self.api_key = api_key or os.environ["MINIMAX_API_KEY"]
        ...

    def consume_text(self, text_observable: Observable) -> "MiniMaxTTSNode":
        ...

    def emit_audio(self) -> Observable:
        ...

    def emit_text(self) -> Observable:
        ...

    def _synthesize_speech(self, text: str) -> None:
        # 调用 MiniMax API
        # hex decode -> MP3 bytes -> soundfile read -> AudioEvent
        # sample_rate=32000
        ...
```

### 3.5 真实机器人模式音频处理流程

```
MiniMax API
    ↓
hex-encoded MP3 (response.data.audio)
    ↓
bytes.fromhex() → MP3 bytes
    ↓
soundfile.read() → numpy array @ 32000Hz
    ↓
resample to 22050Hz (np.interp)
    ↓
normalize → WAV PCM_16
    ↓
base64 encode → WebRTC chunk upload
    ↓
SELECT_START_PLAY
```

> **说明**：保持 v1.0 的 MP3→WAV→WebRTC 流程不变。MiniMax 返回 32000Hz MP3，需要重采样到 22050Hz 以匹配 Unitree 音频系统。

### 3.6 仿真/回放模式音频处理流程

```
MiniMax API
    ↓
hex-encoded MP3
    ↓
bytes.fromhex() → MP3 bytes
    ↓
soundfile.read() → numpy array @ 32000Hz
    ↓
AudioEvent(sample_rate=32000) → SounddeviceAudioOutput
    ↓
本地播放
```

> **说明**：sounddevice 支持 32000Hz 直接播放，无需重采样。

---

## 4. 文件变更清单

| 文件 | 操作 | 说明 |
|------|------|------|
| `dimos/robot/unitree/go2/modules/startup_bark.py` | **修改** | 替换 OpenAI TTS 为 MiniMax TTS |
| `dimos/stream/audio/tts/node_minimax.py` | **新增** | MiniMax TTS Node，适配音频管道 |
| `dimos/stream/audio/pipelines.py` | **修改** | tts() 函数改用 MiniMaxTTSNode（可选） |
| `dimos/robot/unitree/go2/modules/test_startup_bark.py` | **修改** | 更新测试：MiniMax API mock、hex decode、32000Hz |

> **范围决策**：本次迁移**仅修改 `startup_bark.py`** 及其测试。`SpeakSkill`、`UnitreeSpeak`、`pipelines.py` 中的 OpenAI TTS 保留不变，待后续统一迁移。

---

## 5. 环境变量变更

| 变量 | v1.0 | v2.0 |
|------|------|------|
| `OPENAI_API_KEY` | 必需（TTS） | 不再需要（本功能） |
| `MINIMAX_API_KEY` | 不需要 | **必需** |

---

## 6. 验收标准

### 6.1 功能验收
- [ ] 运行 `dimos run unitree-go2 --robot-ip <ip>` 时，机器人启动 2 秒后从扬声器发出 5 声狗叫
- [ ] 运行 `dimos --replay run unitree-go2` 时，本地电脑播放 5 声狗叫
- [ ] 声音只播放一次，不重复
- [ ] 无 `OPENAI_API_KEY` 时功能正常（仅依赖 `MINIMAX_API_KEY`）

### 6.2 代码验收
- [ ] `MiniMaxTTSNode` 实现 `AbstractTextConsumer` + `AbstractAudioEmitter` + `AbstractTextEmitter`
- [ ] `startup_bark.py` 所有 OpenAI 引用替换为 MiniMax
- [ ] 代码通过 `pre-commit` 检查（ruff, yapf, mypy）
- [ ] 新增单元测试覆盖 MiniMax API 调用、hex decode、错误处理
- [ ] 蓝图注册测试通过：`pytest dimos/robot/test_all_blueprints_generation.py`

### 6.3 非功能性要求
- [ ] MiniMax API 调用超时 60 秒
- [ ] API 失败时降级为日志输出（不崩溃）
- [ ] 模块失败不影响其他功能

---

## 7. 风险与缓解

| 风险 | 缓解措施 |
|------|----------|
| MiniMax API 不稳定 | 增加 try/except，失败时降级为日志输出 |
| `MINIMAX_API_KEY` 未设置 | 启动时检查，缺失时跳过 bark 并记录 warning |
| hex decode 失败 | 验证 hex 字符串格式，失败时抛出清晰错误 |
| 32000Hz 与现有音频管道不兼容 | `MiniMaxTTSNode` 在 `AudioEvent` 中正确标注 sample_rate |

---

## 8. 任务拆分

1. **实现 MiniMaxTTSNode** — `dimos/stream/audio/tts/node_minimax.py`
2. **修改 StartupBarkModule** — 替换 OpenAI 为 MiniMax，更新音频参数
3. **更新测试** — 单元测试适配 MiniMax API mock
4. **验证** — 本地运行 `pytest` + 可选实机测试
