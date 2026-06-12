> （opus开发）

# G1 Orin 导览迎宾 — 开发记录与代码清单

本文件记录依据 [`greeter_onboard.md`](./greeter_onboard.md) 完成的「G1 Orin 导览迎宾」（任务 4，阶段 A–E）的全部实现内容、新增/修改的代码，以及离线自测结果与真机待验证项。**未提交 git**。

---

## 1. 总览

| 阶段 | 内容 | 状态 |
|------|------|------|
| A | 空壳蓝图 `unitree-g1-greeter-onboard`（合并 `nav_onboard` + greeter 模块，DDS 注入） | ✅ 代码完成，离线通过 |
| B | DDS 下手势/全身舞可用（`G1ArmActionClient` 接入 `publish_request`） | ✅ 代码完成（真机待测） |
| C | 标点 + 讲解词持久化/重载 + 预缓存 + 问路/带路模板（不经 LLM） | ✅ 代码完成 |
| D | 到站讲解（订阅里程计距离触发 `speak(intro_script)`） | ✅ 代码完成（真机待测） |
| E | 单元测试 + 文档（验收实测 + Troubleshooting） | ✅ 完成 |

设计原则严格遵循文档：**`llm_enabled=False`，模板-only，讲解词只来自标点录入，绝不经 LLM**；移动/手臂全部走 DDS，不使用 WebRTC `G1Connection`。

---

## 2. 文件清单

### 新增

| 文件 | 作用 |
|------|------|
| `dimos/robot/unitree/g1/blueprints/agentic/unitree_g1_greeter_onboard.py` | Orin 导览迎宾蓝图 |
| `dimos/robot/unitree/g1/greeter_landmark_store.py` | 地标表（坐标 + 讲解词）持久化/重载/匹配（纯逻辑） |
| `dimos/robot/unitree/g1/greeter_tour_skill.py` | 标点 / 导航带路 / 到站讲解技能容器 |
| `dimos/robot/unitree/g1/greeter_tour_skill_spec.py` | 导览带路 Spec（供 Router 注入） |
| `dimos/robot/unitree/g1/test_greeter_landmark_store.py` | 地标表单元测试 |
| `dimos/robot/unitree/g1/test_greeter_tour_skill.py` | 导览技能纯逻辑单元测试 |

### 修改

| 文件 | 改动 |
|------|------|
| `dimos/robot/unitree/g1/effectors/high_level/dds_sdk.py` | `publish_request` 接入 `G1ArmActionClient`，使 DDS 下手势/全身舞可用 |
| `dimos/robot/unitree/g1/greeter_intent_router.py` | 新增**可选** `_tour` 注入与问路/带路转发分支（`_tour=None` 时行为不变） |
| `dimos/robot/unitree/g1/blueprints/agentic/_greeter_stack.py` | 收紧 `GREETER_REMAPPINGS` 类型注解（顺带修掉 4 个 greeter 蓝图共有的 mypy 报错） |
| `pyproject.toml` | 为 g1 greeter 中文模板文件加 `RUF001` per-file-ignore |
| `dimos/robot/all_blueprints.py` | 自动生成：注册 `unitree-g1-greeter-onboard` 与 `greeter-tour-skill-container` |
| `docs/platforms/humanoid/g1/greeter_onboard.md` | 勾选任务清单、新增 §9.1 自测结果、§9.2 实现要点、§13 Troubleshooting |

---

## 3. 新增代码

### 3.1 蓝图 `unitree_g1_greeter_onboard.py`

```python
"""G1 Orin 机载导览迎宾蓝图。

在 unitree_g1_nav_onboard(FastLIO2 + nav_stack + MovementManager + G1HighLevelDdsSdk)
之上叠加迎宾链路;移动与手臂手势全部走 DDS(无 WebRTC G1Connection)。
对话策略与笔记本版一致:llm_enabled=False,仅模板短路,模板外固定拒答。
"""

from dimos.agents.mcp.mcp_client import McpClient
from dimos.agents.mcp.mcp_server import McpServer
from dimos.agents.skills.speak_skill import SpeakSkill
from dimos.agents.voice_input import VadVoiceInput
from dimos.core.coordination.blueprints import autoconnect
from dimos.robot.unitree.g1.blueprints.agentic._greeter_stack import (
    GREETER_REMAPPINGS,
    GREETER_ROUTER_KWARGS,
    MCP_CLIENT_KWARGS,
)
from dimos.robot.unitree.g1.blueprints.navigation.unitree_g1_nav_onboard import (
    unitree_g1_nav_onboard,
)
from dimos.robot.unitree.g1.greeter_intent_router import GreeterIntentRouter
from dimos.robot.unitree.g1.greeter_skill import GreeterSkillContainer
from dimos.robot.unitree.g1.greeter_tour_skill import GreeterTourSkillContainer

# 与笔记本版相同的模板-only 策略(llm_enabled=False),额外开启地标问路/带路:
# 已标点 → 走地标表 + 导航;未标点名称仍固定拒答。讲解词只来自标点录入,不经 LLM。
GREETER_ONBOARD_ROUTER_KWARGS = {**GREETER_ROUTER_KWARGS, "location_landmarks_available": True}

unitree_g1_greeter_onboard = (
    autoconnect(
        unitree_g1_nav_onboard,
        GreeterSkillContainer.blueprint(),
        SpeakSkill.blueprint(),
        VadVoiceInput.blueprint(whisper_model="tiny"),
        GreeterIntentRouter.blueprint(**GREETER_ONBOARD_ROUTER_KWARGS),
        GreeterTourSkillContainer.blueprint(),
        McpServer.blueprint(),
        McpClient.blueprint(**MCP_CLIENT_KWARGS),
    )
    .remappings(GREETER_REMAPPINGS)
    .global_config(n_workers=20, robot_model="unitree_g1")
)

__all__ = ["unitree_g1_greeter_onboard"]
```

**关键点**：`GreeterSkillContainer._connection: G1ConnectionSpec` 由 `nav_onboard` 里的 `G1HighLevelDdsSdk`（结构化满足 `move` + `publish_request`，唯一提供者）自动注入；`GreeterTourSkillContainer.goal: Out[PointStamped]` 自动接到 `SimplePlanner.goal: In[PointStamped]`。

### 3.2 地标表 `greeter_landmark_store.py`（纯逻辑，可单测）

```python
"""导览地标的持久化存储:名称 + 坐标 + 到站讲解词(intro_script)。纯逻辑 + JSON。"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import json
from pathlib import Path
import threading
from typing import Any

from dimos.utils.logging_config import setup_logger

logger = setup_logger()


def normalize_landmark_text(text: str) -> str:
    """小写化并去除空白,用于地标名称/同义词的子串匹配。"""
    return "".join(text.strip().lower().split())


@dataclass(frozen=True)
class Landmark:
    name: str
    x: float
    y: float
    z: float
    yaw: float = 0.0
    intro_script: str = ""
    synonyms: tuple[str, ...] = field(default_factory=tuple)

    def keywords(self) -> tuple[str, ...]:
        return (self.name, *self.synonyms)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["synonyms"] = list(self.synonyms)
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Landmark:
        return cls(
            name=str(data["name"]),
            x=float(data.get("x", 0.0)),
            y=float(data.get("y", 0.0)),
            z=float(data.get("z", 0.0)),
            yaw=float(data.get("yaw", 0.0)),
            intro_script=str(data.get("intro_script", "")),
            synonyms=tuple(str(s) for s in data.get("synonyms", [])),
        )


class GreeterLandmarkStore:
    """线程安全的地标表,持久化到单个 JSON 文件,可在重启后重载。"""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._lock = threading.Lock()
        self._landmarks: dict[str, Landmark] = {}

    def load(self) -> None: ...        # 读 JSON → dict(按归一化名称)
    def save(self) -> None: ...        # 写 JSON
    def upsert(self, landmark: Landmark) -> None: ...   # 新增/覆盖 + 立即持久化
    def all(self) -> list[Landmark]: ...
    def names(self) -> list[str]: ...
    def intro_scripts(self) -> list[str]: ...           # 去重非空讲解词(预缓存用)
    def match(self, text: str) -> Landmark | None: ...  # 最长关键词(名称/同义词)子串匹配
    def __len__(self) -> int: ...
```

> 完整实现见仓库文件；此处省略方法体。`match()` 按关键词长度优先（如「展厅A」优先于「厅」）。

### 3.3 导览技能 `greeter_tour_skill.py`

纯函数（可单测）：

```python
def parse_synonyms(raw: str) -> tuple[str, ...]: ...          # 逗号/顿号/空格分隔去重
def is_guide_request(text: str, guide_keywords: tuple[str, ...]) -> bool: ...  # 带路 vs 问路
def distance_2d(ax, ay, bx, by) -> float: ...
def within_arrival(px, py, landmark, threshold_m) -> bool: ...  # 平面到站判定
```

`GreeterTourSkillContainer(Module)`：

```python
class GreeterTourSkillConfig(ModuleConfig):
    store_path: str = ""                 # 空 → ~/.local/state/dimos/greeter_landmarks.json
    arrival_threshold_m: float = 0.8
    arrival_gesture: str = "HighWave"    # 到站后可选手势
    guide_keywords: tuple[str, ...] = ("带我去", "带我到", "带我", "带路", "领我", "送我", "去一下")
    guide_speak_template: str = "好的，请跟我来，我带您去{name}。"
    unknown_template: str = "抱歉，这个地点还没录入地图，请咨询工作人员。"


class GreeterTourSkillContainer(Module):
    config: GreeterTourSkillConfig
    corrected_odometry: In[Odometry]     # PGO 校正后里程计(世界系),与导航目标同参考系
    goal: Out[PointStamped]              # 自动接 SimplePlanner.goal
    _speak: SpeakSkillSpec
    _greeter: GreeterSkillSpec | None = None

    # start(): 加载地标表 → 订阅 corrected_odometry → 预缓存所有 intro_script + unknown_template

    @skill
    def tag_location(self, name: str, intro_script: str, synonyms: str = "") -> str:
        """标点:记录当前里程计坐标 + 讲解词,持久化并预缓存讲解词 TTS。"""

    @skill
    def list_landmarks(self) -> str: ...

    @rpc
    def landmark_count(self) -> int: ...

    @rpc
    def handle_location_query(self, text: str) -> str:
        """命中地标→带路(发布 goal)或播讲解词;未命中→播固定「未录入」模板。全程不经 LLM。"""

    # _on_odom(): 与 pending 目标平面距离 ≤ 阈值 → 线程内 speak(intro_script) + 可选手势
```

> `tag_location` / `list_landmarks` 为 `@skill`，可经 `dimos mcp call tag_location ...` 由工作人员现场标点。

### 3.4 Spec `greeter_tour_skill_spec.py`

```python
from typing import Protocol
from dimos.spec.utils import Spec


class TourGuideSpec(Spec, Protocol):
    """导览带路接口,供 GreeterIntentRouter 在已标点时转发问路/带路请求。"""

    def handle_location_query(self, text: str) -> str: ...
    def landmark_count(self) -> int: ...
```

---

## 4. 修改代码

### 4.1 `dds_sdk.py` — DDS 手势/全身舞（阶段 B）

`G1HighLevelDdsSdk.publish_request` 原先只支持 loco 的 7101/7105，手臂命令（7106）落到 `unsupported_api`。改为：`start()` 初始化 `G1ArmActionClient`，`publish_request` 在 `topic == rt/api/arm/request` 时分派到 arm 服务。

```python
# import
from unitree_sdk2py.g1.arm.g1_arm_action_client import G1ArmActionClient  # type: ignore[import-not-found]

# 常量
_ARM_GET_ACTION_LIST_API_ID = 7107
_ARM_EXECUTE_CUSTOM_ACTION_API_ID = 7108
_ARM_STOP_CUSTOM_ACTION_API_ID = 7113

# start() 内:
self.arm_action_client = G1ArmActionClient()
self.arm_action_client.SetTimeout(self.config.arm_action_timeout)
self.arm_action_client.Init()
self.arm_action_client._RegistApi(_ARM_EXECUTE_CUSTOM_ACTION_API_ID, 0)
self.arm_action_client._RegistApi(_ARM_STOP_CUSTOM_ACTION_API_ID, 0)

# publish_request() 顶部:
if topic == ARM_TOPIC:
    return self._handle_arm_request(api_id, parameter)

# 新增 helper:
def _handle_arm_request(self, api_id, parameter):
    if self.arm_action_client is None:
        return {"code": -1, "error": "arm_action_client_not_initialized"}
    try:
        if api_id == ARM_API_ID:                       # 7106 预设手势
            return {"code": self.arm_action_client.ExecuteAction(int(parameter.get("data", 0)))}
        if api_id == _ARM_GET_ACTION_LIST_API_ID:       # 7107
            code, action_data = self.arm_action_client.GetActionList()
            return {"code": code, "data": action_data}
        if api_id in (_ARM_EXECUTE_CUSTOM_ACTION_API_ID, _ARM_STOP_CUSTOM_ACTION_API_ID):  # 7108/7113 全身舞
            code, _ = self.arm_action_client._Call(api_id, json.dumps(parameter))
            return {"code": code}
        return {"code": -1, "error": "unsupported_arm_api"}
    except Exception as e:
        return {"code": -1, "error": str(e)}
```

`G1ArmActionClient.action_map` 的动作 ID 与仓库 `ARM_COMMANDS` 完全一致（HighWave=26、Clap=17、ArmHeart=20…），故 `greet_guest` / `execute_arm_command` / `perform_dance` 在 DDS 下直接可用。

### 4.2 `greeter_intent_router.py` — 可选导览转发（阶段 C/D）

新增可选 `_tour` 注入与一个分支；`_tour=None`（笔记本三蓝图）时行为完全不变。

```python
from dimos.robot.unitree.g1.greeter_tour_skill_spec import TourGuideSpec

class GreeterIntentRouter(Module):
    ...
    _tour: TourGuideSpec | None = None    # 仅 Orin 版注入

    def _on_human_input(self, text):
        ...
        intent = match_greeter_intent(cleaned, self.config)
        gesture = None if intent is not None else match_gesture_shortcut(cleaned, self.config)

        # 已接入导览带路时,问路/带路交给地标表 + 导航(命中→带路/讲解,未命中→固定拒答),不经 LLM。
        if (
            intent is None
            and gesture is None
            and self._tour is not None
            and is_location_query(cleaned, self.config.location_query_max_chars)
        ):
            self._dispatch_tour_query(cleaned)
            return
        ...   # 其余逻辑不变

    def _dispatch_tour_query(self, text):
        lock = self._busy_lock
        if not lock.acquire(blocking=False):
            return
        def _run():
            self._set_agent_busy(True)
            try:
                assert self._tour is not None
                self._tour.handle_location_query(text)
            finally:
                self._set_agent_busy(False)
                lock.release()
        threading.Thread(target=_run, name="greeter-tour-query", daemon=True).start()
```

### 4.3 `_greeter_stack.py` — 类型注解收紧

```python
from dimos.core.module import ModuleBase
from dimos.spec.utils import Spec

GREETER_REMAPPINGS: list[
    tuple[type[ModuleBase], str, str | type[ModuleBase] | type[Spec]]
] = [
    (McpClient, "human_input", "llm_human_input"),
]
```

修复了 4 个 greeter 蓝图共有的一处 mypy `arg-type` 报错（纯类型注解，运行时无影响）。

### 4.4 `pyproject.toml`

```toml
# Chinese greeter templates/prompts intentionally use full-width punctuation.
"dimos/robot/unitree/g1/*greeter*.py" = ["RUF001"]
```

---

## 5. 数据流

```
工作人员标点(MCP):
  dimos mcp call tag_location --arg name=前台 --arg intro_script="这里是前台，请登记。" --arg synonyms="接待处"
    → GreeterTourSkillContainer.tag_location
    → 记录当前 corrected_odometry 坐标 + 讲解词 → JSON 持久化 → speak.prewarm_texts([intro_script])

客人语音:
  麦克风 → VadVoiceInput(Whisper tiny) → /human_input → GreeterIntentRouter
    ├─ 你好/再见      → greet_guest / farewell_guest(DDS 挥手)
    ├─ 挥手/比心/跳舞  → 短台词 + 手势 / 全身舞(DDS ExecuteAction / 7108)
    ├─ 你是谁         → FAQ 固定回答
    ├─ 问路/带路       → _tour.handle_location_query
    │     ├─ 命中 + 带路意图 → 发布 goal(→SimplePlanner) + speak(带路语)
    │     │       → 到站(corrected_odometry 距离≤阈值) → speak(intro_script) + 可选手势
    │     ├─ 命中 + 仅问路   → speak(intro_script)
    │     └─ 未命中         → speak(unknown_template)  # 不编造
    └─ 其它任意       → 固定拒答(llm_fallback_template)  # 不走 LLM
```

---

## 6. 离线自测结果（无真机）

| 检查 | 结果 |
|------|------|
| `pytest test_greeter_landmark_store.py + test_greeter_tour_skill.py + test_greeter_intent_router.py + test_all_blueprints_generation.py`（CI 模式） | ✅ 30 passed |
| ruff check（整个 `dimos/robot/unitree/g1/`） | ✅ All checks passed |
| mypy（7 个新增/改动文件） | ✅ no issues found |
| `dimos list` | ✅ 可见 `unitree-g1-greeter-onboard` |
| 新模块离线导入（store / tour / spec / router） | ✅ OK |

> 本机未安装 `unitree_sdk2py`（Orin 专用依赖），无法完整 `.build()` 或导入蓝图。

---

## 7. 真机待验证（Orin）

需在 Orin（`192.168.123.164`）SSH 会话中按 `greeter_onboard.md` §9 验收表与 §13 Troubleshooting 验证：

- DDS 下「你好」迎宾挥手、「挥挥手/比心」手势、「跳个舞」全身舞，声音从 G1 喇叭出。
- Mid360 + FastLIO2 建图；移动后 `tag_location` 能记录坐标。
- 「带我去前台」→ 发布导航目标 → 到站播标点录入的讲解词。
- 「厕所在哪」（已标点）→ 讲解词；（未标点）→ 固定拒答；「今天天气怎么样」→ 固定拒答（不走 LLM）。

**已知需真机调参/确认**：

- 手臂动作仅在 FSM 状态 500/501/801（Walk/Run）下生效，需先进入 Sport 平衡模式。
- 到站阈值 `arrival_threshold_m`（默认 0.8 m）与 PGO 回环跳变对带路精度的影响。
- 标点坐标与导航目标统一用 `corrected_odometry`（世界系）以保持参考系一致。

---

## 8. 设计取舍

- 文档蓝图草图中的 `navigate_with_text` 用 G1 专用封装 `GreeterTourSkillContainer.handle_location_query` 替代（文档明确允许「或 G1 专用封装」）。原因：`nav_onboard` 用的是 `SimplePlanner`（`PointStamped` 目标、无相机/spatial-memory），与重量级 `NavigationSkillContainer`（3000+ 行，依赖相机/VLM/spatial memory）不兼容。
- `_tour` 用**可选 Spec 注入**，保证现有三个笔记本 greeter 蓝图（无 tour 模块）行为零变化。
- 全程 `llm_enabled=False`，讲解词只来自标点录入，符合文档「禁止 LLM 编造位置/讲解词」。

---

## 9. deepseek 审查

> 审查时间：2026-06-12  
> 审查范围：全部新增/修改的 20+ 文件（见 git status），包括 4 个新增蓝图、greeter 模块链、TTS 缓存、VAD/PTT 语音输入、DDS 手臂控制、tell CLI 修复、SOCKS 代理处理。

### 9.1 🔴 严重 — `speak_skill.py:_play_cached_audio()` 缓存播放阻塞失效

**文件:** `dimos/agents/skills/speak_skill.py:175-179`

**问题:** 缓存 TTS 播放使用 `sd.play()` + `time.sleep(0.3)` 作为"阻塞等待"，但 `sd.play()` 默认非阻塞，`time.sleep(0.3)` 远短于任意语音（最短问候语也在 1~2 秒）。

```python
def _play_cached_audio(self, audio: np.ndarray, text: str, t0: float) -> str:
    import sounddevice as sd
    sd.play(audio, samplerate=_SPEECH_SAMPLE_RATE)
    time.sleep(0.3)  # ← 无论音频多长,0.3s后即返回
    ...
```

**影响范围:** 所有预缓存模板文本（welcome、farewell、FAQ 回答、landmark intro_script、unknown_template 等）的 `speak(text, blocking=True)` 调用在音频刚开头就返回。

**受害者链路:**
- `GreeterTourSkillContainer._announce_arrival()` → `self._speak.speak(script, blocking=True)` 返回后立即触发 `execute_arm_command(gesture)` → **手势与讲解语音重叠**，客人听不清 intro_script。
- `GreeterIntentRouter._run_gesture_shortcut()` → `speak(line, blocking=True)` 提前返回 → 手势在"挥手"台词播放期间就开始。
- `_run_greeting_shortcut()` → `greet_guest(welcome_template)` 内部 `_speak_skill.speak(text, blocking=True)` 提前返回 → 挥手在欢迎词没说完时启动。

**修复建议:**
```python
def _play_cached_audio(self, audio: np.ndarray, text: str, t0: float) -> str:
    import sounddevice as sd
    sd.play(audio, samplerate=_SPEECH_SAMPLE_RATE)
    sd.wait()  # 正确阻塞到播放结束
    logger.info("SpeakSkill 缓存播放,耗时 %.1fs, text=%s", time.monotonic() - t0, text[:40])
    return f"Spoke (cached): {text}"
```

或用音频长度精确计算等待时间。

---

### 9.2 🟡 中等 — `_audio_lock` 在缓存路径下锁覆盖不足，可能音频重叠

**文件:** `dimos/agents/skills/speak_skill.py`

`_speak_blocking()` 持有 `self._audio_lock`，但缓存路径 `_play_cached_audio()` 在 0.3s 后释放锁返回。此时第二个 `speak()` 调用可能获取锁并开始播放新音频——而第一个 `sd.play()` 的音频仍在硬件上播放。两个音频流**重叠输出**，客人听到混杂语音。

**根因:** `_audio_lock` 的保护范围与 `sd.play()` 异步播放生命周期不匹配。修完 9.1 后此问题自然消失（因锁持有时间 = 实际播放时长）。

---

### 9.3 🟡 中等 — 手臂 API 常量在 `greeter_skill.py` 与 `dds_sdk.py` 重复定义

**文件:** `dimos/robot/unitree/g1/greeter_skill.py:44-46` 与 `dimos/robot/unitree/g1/effectors/high_level/dds_sdk.py:68-70`

```python
# greeter_skill.py (WebRTC 路径)
_ARM_GET_ACTION_LIST_API_ID = 7107
_ARM_EXECUTE_CUSTOM_ACTION_API_ID = 7108
_ARM_STOP_CUSTOM_ACTION_API_ID = 7113

# dds_sdk.py (DDS 路径)
_ARM_GET_ACTION_LIST_API_ID = 7107
_ARM_EXECUTE_CUSTOM_ACTION_API_ID = 7108
_ARM_STOP_CUSTOM_ACTION_API_ID = 7113
```

**风险:** 两处独立维护，SDK 升级致 API ID 变更时容易只改一处。与 `ARM_API_ID`、`ARM_COMMANDS`、`ARM_TOPIC`（统一定义在 `commands.py`）的风格不一致。

**建议:** 移到 `dimos/robot/unitree/g1/effectors/high_level/commands.py` 统一导出，两个模块从同一源导入。

---

### 9.4 🟡 中等 — `node_key_recorder.py` 输入监听启动时机变更，其他调用方可能受影响

**文件:** `dimos/stream/audio/node_key_recorder.py`

**变更:** `__init__()` 不再启动 `_input_thread`，改为延迟到 `consume_audio()` 被调用时在 `_ensure_input_monitor()` 中启动。`_running` 初始值从 `True` 变为 `False`。

**风险:** 如果有其他调用方创建 `KeyRecorder` 后未调用 `consume_audio()`，或依赖 `__init__` 后就绪的行为，现在不会收到按键输入。目前可见调用方（`VoiceInput.start()`）正确调用了 `consume_audio()`，但需确认无其他调用点。

**建议:** `git grep KeyRecorder` 确认所有调用点，或保留 `__init__` 向后兼容（在 `__init__` 接收 `ptt_topic` 时仅延迟线程、无 `ptt_topic` 时保持旧行为）。

---

### 9.5 🟢 低 — 非 PTT 模式下 stdin 不可读时静默失败

**文件:** `dimos/stream/audio/node_key_recorder.py:109-115`

```python
if not sys.stdin.isatty():
    logger.error(
        "KeyRecorder 无法读取终端键盘(模块在 worker 子进程运行)。"
        "请使用带 ptt_topic 的蓝图,或改用免提版 greeter-hands-free。"
    )
    return  # ← 静默返回,不抛异常
```

**问题:** 在 worker 子进程中创建非 PTT 模式的 `KeyRecorder` 时，只打 error 日志但不抛异常，调用方不知道录音功能根本没启动。用户按 Enter 无效且没有任何提示（除非看日志）。

**建议:** 至少在模块级设置一个 `self._input_available = False` 并在 `emit_recording()` 或 `stop()` 中再警告一次。

---

### 9.6 🟢 低 — `greeter_tour_skill.py:tag_location()` 不检查机器人是否静止

**文件:** `dimos/robot/unitree/g1/greeter_tour_skill.py:172-206`

`tag_location()` 直接取当前里程计坐标存为地标。如果工作人员标点时机器人正在移动（例如上一个 `goto` 命令的惯性滑动），记录的坐标与实际目标点可能有偏差。

**建议:** 可选增强：记录最近 N 帧里程计位姿，在方差足够小（机器人已静止）时才允许标点；或在 UI 提示"请等待机器人完全停止后标点"。

---

### 9.7 🟢 低 — PGO 回环跳变可能跳过到站阈值

**文件:** `dimos/robot/unitree/g1/greeter_tour_skill.py:143-153`

`_on_odom()` 每帧检查 `within_arrival(current_pose, pending, threshold_m)`。如果 PGO 回环修正导致里程计位姿跳变（例如从距目标 0.5m 瞬间跳到 10m），`_pending` 已被清为 None（已在某帧锁定），arrival 事件永远不会触发——机器人继续走向旧的 goal，永不播讲解词。

**已在文档 §7 列为已知待验证项**，但代码层面可加防御：超时机制（超过 N 秒未到站则强制播报）、或对 PGO 跳变幅度做限幅滤波。

---

### 9.8 🟢 低 — LLM 开启后 system prompt 与 onboard 蓝图能力不一致

**文件:** `dimos/robot/unitree/g1/greeter_system_prompt.py:26`

```python
# 最高优先级:不可移动
你处于"原地迎宾"模式,无法行走、转身或导航,也没有任何移动/导航技能...
```

该 prompt 由 `_greeter_stack.py:MCP_CLIENT_KWARGS` 注入所有 4 个 greeter 蓝图。当前 `llm_enabled=False` 不实际发送给 LLM，但一旦恢复 `llm_enabled=True`，**onboard 蓝图**（有完整导航能力）的 LLM 会被告知"不可移动"——LLM 将拒绝一切导航请求，与蓝图实际能力矛盾。

**建议:** 拆分为两个 prompt 变量（`GREETER_SYSTEM_PROMPT_STATIONARY` / `GREETER_SYSTEM_PROMPT_MOBILE`），或在 blueprint 层按蓝图名选择 prompt。

---

### 9.9 🟢 低 — ruff I001（import 排序）在 2 个文件

**文件:**
- `dimos/agents/voice_input.py:39` — 第三方 import `sounddevice` 插在 dimos 导入中间
- `dimos/stream/audio/node_key_recorder.py:16` — 标准库/第三方/dimos 导入顺序混乱

**修复:** `uv run ruff check --fix <file>` 即可自动修复。两个文件都有 `# type: ignore[import-untyped]` 注释，需确认自动修复后注释仍在正确行。

---

### 9.10 ✅ 通过项

以下项目审查通过，无问题：

| 项目 | 结论 |
|------|------|
| `test_greeter_prompt.py` — 手势名同步守卫测试 | ✅ 自动化保障 prompt 与 ARM_COMMANDS 一致性，设计好 |
| `tell.py` idle/busy 状态机修复（清队列+saw_busy） | ✅ 解决了 `dimos tell` 提前返回的竞态条件 |
| `mcp_client.py` 异常处理（空消息丢弃、process 错误兜底） | ✅ 防御性编程到位 |
| `VadVoiceInput` agent_idle gating + cooldown timer | ✅ 防止自问自答的逻辑正确，timer 取消也正确 |
| `proxy_env.strip_socks_proxy_env()` | ✅ 调用在 `build()` 之前，时序正确 |
| `_greeter_stack.py` 类型注解收紧 | ✅ 修复 4 个蓝图的 mypy 报错 |
| `GreeterLandmarkStore` 线程安全 + 最长关键词匹配 | ✅ 实现完整，8 个单测覆盖 |
| `greeter_intent_router.py` tour 分支不影响笔记本蓝图 | ✅ `_tour=None` 时行为完全不变 |
| `dds_sdk.py` G1ArmActionClient 初始化 | ✅ 7108/7113 注册正确，使 DDS 下手势/舞蹈可用 |

---

### 9.11 审查总结

| 严重度 | 数量 | 关键项 |
|--------|------|--------|
| 🔴 严重 | 1 | TTS 缓存播放阻塞失效 → 语音/手势时序混乱 |
| 🟡 中等 | 3 | 音频锁覆盖不足、API 常量重复、KeyRecorder 兼容 |
| 🟢 低 | 4 | 静默失败、标点不检查静止、PGO 跳变、prompt 不一致 |

**建议修复优先级:**
1. **立即:** `_play_cached_audio` 的 `time.sleep(0.3)` → `sd.wait()`（9.1）
2. **本迭代:** arm API 常量去重到 `commands.py`（9.3）
3. **下次 PR:** KeyRecorder 兼容确认 + prompt 拆分 + 标点静止检查

---

## 10. （opus开发）针对审查的处理

> 处理时间：2026-06-12  
> 逐条核对 §9 的审查项，**确定有问题的已修复并验证**；**存疑/不修的记录原因**。

### 10.1 已修复（确定问题）

| 项 | 处理 | 文件 |
|----|------|------|
| **9.1 🔴 缓存播放阻塞失效** | `time.sleep(0.3)` → `sd.wait()`，阻塞到音频实际播放结束。修复后讲解词/欢迎词不再被后续手势打断。 | `dimos/agents/skills/speak_skill.py` `_play_cached_audio()` |
| **9.2 🟡 音频锁覆盖不足** | **随 9.1 一并解决**：`_play_cached_audio` 在持 `_audio_lock` 期间 `sd.wait()` 到放完才返回释放锁，第二段语音无法在前一段未放完时插入，消除重叠。无需额外改动。 | 同上 |
| **9.3 🟡 arm API 常量重复** | 将 `ARM_GET_ACTION_LIST_API_ID`(7107)/`ARM_EXECUTE_CUSTOM_ACTION_API_ID`(7108)/`ARM_STOP_CUSTOM_ACTION_API_ID`(7113) 统一移到 `commands.py` 并导出；`greeter_skill.py`（WebRTC）与 `dds_sdk.py`（DDS）改为从同一处导入，删除各自的本地 `_ARM_*` 定义。 | `commands.py` / `greeter_skill.py` / `dds_sdk.py` |
| **9.9 🟢 ruff I001 import 排序** | `ruff check --fix` 自动修复；已确认 `sounddevice` 的 `# type: ignore[import-untyped]` 注释仍在正确行（`voice_input.py:40`）。 | `voice_input.py` / `node_key_recorder.py` |

修复后验证：

```
ruff check (g1 目录 + speak_skill + voice_input + node_key_recorder) → All checks passed
mypy (commands / dds_sdk / greeter_skill / speak_skill)             → no issues
pytest (landmark_store + tour_skill + intent_router)               → 29 passed
shared arm const import 校验                                        → 7107/7108/7113 OK
```

### 10.2 存疑 / 暂不修复（记录原因）

| 项 | 结论 | 理由 |
|----|------|------|
| **9.4 🟡 KeyRecorder 监听时机变更** | **核实通过，不需改** | `git grep KeyRecorder` 全部调用点（`voice_input.py`、`pipelines.py:stt()`、`node_whisper.py` demo、`__main__`）均在创建后调用 `consume_audio()`，新逻辑在 `_ensure_input_monitor()` 中启动监听，行为正确。无遗漏调用点。 |
| **9.5 🟢 非 PTT stdin 不可读静默失败** | **暂不修** | 已有 `logger.error(...)` 明确提示「用 ptt_topic 蓝图或 hands-free」。Orin 版用 `VadVoiceInput`（免提，不走 KeyRecorder PTT），不触及该路径；再加二次告警价值有限。 |
| **9.6 🟢 tag_location 不检查机器人静止** | **暂不修（记为真机增强）** | 属精度增强而非缺陷。标点是工作人员手动、低频操作，通常机器人已停稳。「最近 N 帧方差判静止」需真机数据调参，留待真机阶段评估。 |
| **9.7 🟢 PGO 回环跳变可能跳过到站阈值** | **暂不修（已列真机待验证）** | 已在文档 §7 列为已知项。代码层防御（超时强制播报）在无真机时无法验证阈值，且"强制播报"可能在错误位置播讲解词，反而更糟；倾向真机实测后再决定加超时/限幅。 |
| **9.8 🟢 LLM prompt 与 onboard 能力不一致** | **暂不修（当前不触发）** | onboard 蓝图 `llm_enabled=False`，prompt 不发给 LLM；且文档 §11「禁止事项」明确要求 onboard **不得**启用 LLM。仅当未来违规启用时才需拆分 `_STATIONARY`/`_MOBILE` 两个 prompt——届时再做。已在此记录为前置条件。 |

### 10.3 小结

- 🔴/🟡 中可立即处理的全部修复：**9.1、9.2、9.3、9.9**（9.2 由 9.1 连带解决）。
- 🟢 低优先 4 项中，**9.4 核实为无需修改**；**9.5/9.6/9.7/9.8** 记录为「真机阶段评估」或「当前不触发」，均不影响当前离线交付与 onboard 模板-only 行为。
- 所有修改通过 ruff / mypy / 单元测试；未提交 git。

---

## 11. deepseek 二次审查（修复后验证）

> 审查时间：2026-06-12  
> 范围：§10 中 4 项代码修复的逐条验证 + 全量回归。

### 11.1 修复验证

#### 9.1 🔴 `_play_cached_audio()` — `time.sleep(0.3)` → `sd.wait()`

**验证文件:** `dimos/agents/skills/speak_skill.py:181-190`

```python
def _play_cached_audio(self, audio: np.ndarray, text: str, t0: float) -> str:
    import sounddevice as sd  # type: ignore[import-untyped]
    sd.play(audio, samplerate=_SPEECH_SAMPLE_RATE)
    # Block until playback actually finishes — sd.play() is non-blocking, so a
    # fixed sleep would return mid-utterance and let a follow-up gesture/speak
    # overlap the audio (and breaks the _audio_lock's serialization guarantee).
    sd.wait()
    logger.info("SpeakSkill 缓存播放,耗时 %.1fs, text=%s", time.monotonic() - t0, text[:40])
    return f"Spoke (cached): {text}"
```

✅ **通过。** `sd.wait()` 正确阻塞到音频硬件播放结束才返回。注释清楚说明了为什么不能用 `time.sleep()`。`_audio_lock` 在整个播放期间持有 → 第二段语音无法插入 → 音频重叠问题（9.2）一并消除。

**影响链修复确认：**
- `_announce_arrival()` → `speak(script, blocking=True)` → 讲解词完整播放完 → `execute_arm_command(gesture)` 才启动 ✅
- `_run_gesture_shortcut()` → `speak(line, blocking=True)` → 台词说完 → 手势才动 ✅
- `_run_greeting_shortcut()` → `greet_guest()` 内部 `speak(text, blocking=True)` → 欢迎词说完 → 挥手 ✅

#### 9.2 🟡 音频锁覆盖不足

✅ **随 9.1 一并通过。** 修复后 `_play_cached_audio()` 在持 `_audio_lock` 期间 `sd.wait()` 到播放完毕才释放锁，两个并发的 `speak()` 被正确串行化。无需额外代码改动。

#### 9.3 🟡 arm API 常量去重

**验证文件:** `dimos/robot/unitree/g1/effectors/high_level/commands.py:26-31`

```python
# G1 arm action service api_ids (see unitree_sdk2 g1_arm_action_api.hpp).
# Defined here so the WebRTC (greeter_skill.py) and DDS (dds_sdk.py) paths share
# a single source of truth.
ARM_GET_ACTION_LIST_API_ID = 7107
ARM_EXECUTE_CUSTOM_ACTION_API_ID = 7108
ARM_STOP_CUSTOM_ACTION_API_ID = 7113
```

**验证检查：**
- `commands.py` 导出三个新常量 + `__all__` 包含 ✅
- `greeter_skill.py` 从 `commands.py` 导入，旧 `_ARM_*` 已删除 ✅
- `dds_sdk.py` 从 `commands.py` 导入，旧 `_ARM_*` 已删除 ✅
- `grep "^_ARM_" dds_sdk.py` → 无结果 ✅
- 运行时 import 验证：`ARM_GET_ACTION_LIST_API_ID=7107, ARM_EXECUTE_CUSTOM_ACTION_API_ID=7108, ARM_STOP_CUSTOM_ACTION_API_ID=7113` ✅

#### 9.9 🟢 ruff I001 import 排序

**验证文件:**
- `dimos/agents/voice_input.py:27-50` — 顺序：`__future__` → stdlib → TYPE_CHECKING → reactivex → sounddevice → dimos ✅
- `dimos/stream/audio/node_key_recorder.py:16-29` — 顺序：stdlib → numpy → reactivex → dimos ✅

ruff check 全量：`All checks passed!` ✅

### 11.2 全量回归

| 检查项 | 命令 | 结果 |
|--------|------|------|
| ruff（g1 目录 + 所有改动文件） | `ruff check ...` | ✅ All checks passed |
| mypy（commands/dds_sdk/greeter_skill/speak_skill/voice_input/key_recorder） | `mypy ...` | ✅ no issues found |
| 单元测试（landmark_store + tour_skill + intent_router + prompt + dance） | `pytest ... -v` | ✅ 33 passed in 0.04s |
| 共享常量 import 链 | `python -c "from commands import ARM_*; from greeter_skill import GreeterSkillContainer"` | ✅ 7107/7108/7113 正确 |
| greeter_skill.py 残留 `_ARM_` | `grep "_ARM_" greeter_skill.py` | ✅ 仅 `_WEBRTC_ARM_DANCE_SEQUENCE`（无关变量名） |
| dds_sdk.py 残留 `_ARM_` | `grep "^_ARM_" dds_sdk.py` | ✅ 无结果，旧私有常量已清理 |

### 11.3 审查结论

**修复项 4/4 全部通过，无新问题引入：**

| 项 | 严重度 | 状态 |
|----|--------|------|
| 9.1 TTS 缓存阻塞失效 | 🔴 严重 | ✅ 已修复（`sd.wait()` + 注释） |
| 9.2 音频锁覆盖不足 | 🟡 中等 | ✅ 随 9.1 解决 |
| 9.3 arm API 常量重复 | 🟡 中等 | ✅ 已去重到 `commands.py` |
| 9.9 ruff import 排序 | 🟢 低 | ✅ 已修复 |

**未修改项（4 项，理由已记录在 §10.2）：**
- 9.4 KeyRecorder 兼容 → 核实无遗漏调用点，不需改
- 9.5 stdin 不可读静默失败 → Orin 用 VAD，不触及该路径
- 9.6 tag_location 静止检查 → 真机阶段增强
- 9.7 PGO 跳变 → 已列真机待验证
- 9.8 prompt 不一致 → `llm_enabled=False` 当前不触发

**整体结论：代码质量良好，关键 bug 已修复，可以放心交付。**
