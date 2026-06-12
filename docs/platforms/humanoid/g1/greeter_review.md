# G1 迎宾对话机器人 — Code Review

审查范围：`docs/platforms/humanoid/g1/greeter.md` 及关联的 5 个新增/修改文件。

---

## 严重

### 1. `greeter_skill.py:56-61` — `start()`/`stop()` 无意义重写

```python
@rpc
def start(self) -> None:
    super().start()

@rpc
def stop(self) -> None:
    super().stop()
```

两个方法仅调用 `super()`,完全没有增加逻辑。`Module` 基类的 `start()`/`stop()` 已经是 `@rpc` 装饰的,子类不写也会正确继承。建议直接删除这两个方法。

**影响**：无功能影响,但代码冗余,且未来若基类 `start`/`stop` 签名变化会导致不必要的维护。

---

### 2. `greeter_system_prompt.py:42-45` — 命令列表硬编码,与源码不同步

系统提示词中 14 个手臂命令及其含义是**手动敲进去的**：

```python
- `execute_arm_command(command_name)`:可选其一:
  "Handshake"(握手)、"HighFive"(击掌)、"Hug"(拥抱)、"HighWave"(高举挥手)、
  "Clap"(鼓掌)、"FaceWave"(面前挥手)、"LeftKiss"(左手飞吻)、"ArmHeart"(双臂比心)、
  "RightHeart"(右手比心)、"HandsUp"(双手举起)、"XRay"、"RightHandUp"(举右手)、
  "Reject"(拒绝手势)、"CancelAction"(取消动作)。
```

但 `greeter_skill.py:110-121` 的 docstring 是**动态从 `ARM_COMMANDS_DOC` 拼接**的：

```python
GreeterSkillContainer.execute_arm_command.__doc__ = f"""...
{ARM_COMMANDS_DOC}
"""
```

一旦 `commands.py` 中新增、改名或删除命令，系统提示词**不会自动更新**，LLM 可能调用不存在的命令或漏掉新增命令。

**修复建议**：在系统提示词中也用 f-string 嵌入 `ARM_COMMANDS_DOC`,或至少将命令列表提取为 `greeter_skill.py` 中的常量，两处共用。

---

### 3. `voice_input.py:93` — 可能触碰私有 API

```python
self._human_transport.lcm.stop()
```

直接访问 `pLCMTransport` 的 `.lcm` 属性并调用 `.stop()`。这看起来像是访问了框架内部字段——属性名 `lcm` 没有下划线前缀不代表它是公开 API。需要确认：

- `pLCMTransport` 是否有公开的 `stop()` / `close()` / `shutdown()` 方法？
- L75 已经通过 `self.register_disposable(...)` 注册了订阅，框架是否会在 `stop()` 时自动清理 transport？
- 如果已有自动清理，这行是否多余？

**影响**：若未来 `pLCMTransport` 重构内部实现（比如把 `lcm` 改名或换通信后端），这里会直接炸。

---

## 中等

### 4. `greeter.md:36` — 格式不统一

```markdown
unitree-g1-greeter-voice  = 上述 + VoiceInput
```

`=` 两侧多了空格,与文档其余部分的风格不一致。改为：

```markdown
unitree-g1-greeter-voice = 上述 + VoiceInput
```

---

### 5. `greeter.md:166-174` — DeepSeek 环境变量可能不生效

```bash
OPENAI_API_KEY=sk-deepseek-xxxx
OPENAI_BASE_URL=https://api.deepseek.com
```

多数 OpenAI 兼容客户端读的是 `OPENAI_API_BASE` 或 `OPENAI_BASE_URL`,具体取决于 `McpClient` 内部使用的 SDK。需要验证 DimOS 的 `McpClient` 到底读哪个环境变量，否则用户按文档配置后可能连接失败。

---

### 6. `greeter_system_prompt.py:44` — `XRay` 缺少中文注释

14 个命令中 13 个都有中文解释，唯独 `"XRay"` 没有：

```
"XRay"、"RightHandUp"(举右手)、
```

要么补上注释（如"交叉手臂"/"X型手势"），要么注明刻意不翻译的原因，避免读者以为是疏漏。

---

### 7. `greeter_skill.py:78` — 多条返回值的拼接可读性

```python
return " ".join(results)
```

`execute_g1_command` 每条返回形如 `"'HighWave' command executed successfully."`,多条用空格 join 后会连成一段英文。对 LLM 来说不影响理解,但如果后续需要调试,加换行会更清晰。可选改为：

```python
return "\n".join(results)
```

---

## 轻微

### 8. `greeter_skill.py` / `greeter_system_prompt.py` — 缺 `__all__`

两个蓝图文件 (`unitree_g1_greeter.py` / `unitree_g1_greeter_voice.py`) 都显式声明了 `__all__ = [...]`，但 `greeter_skill.py` 和 `greeter_system_prompt.py` 没有。如果 `all_blueprints.py` 的自动生成不依赖 `__all__`（而是走模块路径），则无实际影响；但风格上建议统一。

---

### 9. `greeter.md:78` — "依赖注入"用词不精确

> 依赖注入 `_connection: G1ConnectionSpec`,经 `publish_request` 下发宇树手臂动作。

这实际上是 DimOS 框架的**模块依赖**——`Module` 子类的 `_connection` 字段由框架根据 blueprint 连线自动填充，并非构造函数/Setter 注入。建议改为：

> 模块依赖 `_connection: G1ConnectionSpec`（框架自动注入），经 `publish_request` 下发手臂动作。

---

### 10. `greeter.md:19-20` — 安全提醒位置可提前

当前安全说明放在第 1 节（功能概述）末尾，但在代码中安全是系统提示词的**最高优先级**。建议将安全提醒单独提为一个小节或提到功能概述最前面，与代码优先级对齐。

---

## 总结

| 级别 | 数量 | 关键项 |
|------|------|--------|
| 严重 | 3 | 冗余重写、硬编码同步风险、私有 API 触碰 |
| 中等 | 4 | 格式、环境变量、缺注释、返回值拼接 |
| 轻微 | 3 | `__all__` 缺失、用词、文档结构 |

**整体评价**：功能设计思路清晰——"无移动版最小可用形态"定位精准，打字版→语音版的渐进式设计合理。代码结构简洁，文档详尽。上述问题除 1-3 需要代码改动外，其余大多属于 polish 级别。

---

# Cursor 修改记录(对上述 review 的复核与处理)

复核方式：对照实际源码与仓库既有约定逐条核实。结论:10 条中 **真问题 4 条(已修)**、
**误判/不算问题 4 条**、**主观或有意取舍 2 条**。真正需要改代码的只有 #3。

## 已修复

- **#3 `voice_input.py` `.lcm.stop()` 触碰内部字段** —— 成立。`pLCMTransport` 有公开
  `stop()` 方法(`dimos/core/transport.py:106`),已改为 `self._human_transport.stop()`。
  备注:原写法系照搬 `WebInput`(`web_human_input.py:86`),改用公开 API 更规范。
- **#6 `XRay` 缺中文** —— 成立。系统提示词中补为 `"XRay"(X 型姿势)`(源码含义
  "Hold arms in an X-ray pose")。
- **#7 返回值空格拼接** —— 采纳。`greeter_skill.py` 的 `" ".join(results)` 改为
  `"\n".join(results)`,多手势返回更清晰。
- **#9 文档"依赖注入"用词** —— 采纳。`greeter.md` 改为"模块依赖(框架按 blueprint
  连线自动注入)"。

## 不予修改(误判或与仓库约定一致)

- **#1 `start()`/`stop()` 空重写** —— 技术上确为冗余(基类 `Module.start/stop` 已是
  `@rpc` 且做实事,见 `module.py:164-172`),但这是仓库既定写法:兄弟文件
  `UnitreeG1SkillContainer`(`skill_container.py:70-75`,纯 `super()` 空重写)及
  `AGENTS.md` 的最小技能模板均如此。删除会与 G1 其他技能容器不一致,故**保留**。
  〔更正(经 DeepSeek 复核):此前本条误称 `SpeakSkill` 亦为空重写;实际
  `SpeakSkill.start()`(`speak_skill.py:48-58`)含实质 TTS 初始化逻辑,并非空重写。
  真正可类比的空重写兄弟文件仅 `UnitreeG1SkillContainer`。结论不变。〕
- **#4 文档 `=` 两侧空格** —— 误判。那是代码块内**故意的列对齐**(短行补空格对齐 `=`),
  非风格不一致,保留。
- **#5 DeepSeek 环境变量** —— 误判。文档用的 `OPENAI_BASE_URL` 与仓库自带
  `unitree_g1_agentic_deepseek.py` 文件头写法一致,`McpClient` 走 langchain OpenAI 兼容
  客户端,认该变量。无需改。
- **#8 缺 `__all__`** —— 不算问题。`greeter_skill.py` / `greeter_system_prompt.py` 与其
  对标文件 `skill_container.py` / `system_prompt.py` 一致(后两者也无 `__all__`);蓝图文件
  才按惯例写 `__all__`。保持一致。

## 主观 / 有意取舍(暂留)

- **#2 系统提示词手势清单硬编码可能与源码漂移** —— 风险真实。采用第三方案(两全):
  **保留手敲的中文清单**(中文场景质量不受损),同时新增一致性守卫测试
  `dimos/robot/unitree/g1/test_greeter_prompt.py`,校验提示词中出现的手势名集合与
  `ARM_COMMANDS` 完全一致。一旦 `commands.py` 新增/改名/删除手势而提示词未同步,该测试立即
  失败 → **既不牺牲中文,又不会静默漂移**。已通过 pytest / ruff / mypy。
- **#10 安全提醒位置** —— 主观,优先级低,暂留。

## 校验

修复后通过:`ruff check` + `ruff format --check`、`mypy`(严格)、
`test_all_blueprints.py` 对 `unitree-g1-greeter` / `unitree-g1-greeter-voice` 的有效性校验。

---

# DeepSeek 审查(对 Cursor 修改记录的复核)

复核方式:逐条对照实际源码与仓库既有约定,不依赖 Cursor 修改记录的声称。
结论:**4 条修复准确无误,4 条不予修改的理由经源码核实均成立,1 处小瑕疵(不影响结论)。**

## 已修复:全部确认

| # | 声称的修改 | 源码位置 | 结果 |
|---|-----------|---------|------|
| #3 | `.lcm.stop()` → `.stop()` | `voice_input.py:94` 已改为 `self._human_transport.stop()`;`transport.py:106` 确认 `pLCMTransport` 有公开 `stop()` 方法 | ✅ |
| #6 | `XRay` 补中文 | `greeter_system_prompt.py:44` → `"XRay"(X 型姿势)` | ✅ |
| #7 | 空格 join → 换行 join | `greeter_skill.py:78` → `"\n".join(results)` | ✅ |
| #9 | "依赖注入"措辞修正 | `greeter.md:78` → "模块依赖 `_connection: G1ConnectionSpec`(由框架按 blueprint 连线自动注入)" | ✅ |

## 不予修改:仓库约定核实

- **#1 空 `start/stop`**:`UnitreeG1SkillContainer`(`skill_container.py:70-75`) 确实也是纯
  `super()` 空重写,保留一致合理。但修改记录称 "`SpeakSkill` 亦如此" 不准确——
  `SpeakSkill.start()`(`speak_skill.py:48-58`)有实质性的 TTS 初始化逻辑(DashScope/OpenAI
  分叉、音频输出节点接线),并非空重写。真正一致的兄弟文件只有 `UnitreeG1SkillContainer`。
  **结论**:不影响判断,但如对外展示建议删去 "SpeakSkill" 引用。

- **#4 `=` 空格**:`unitree-g1-greeter` 和 `unitree-g1-greeter-voice` 两行的 `=` 均在列 33,
  确属有意列对齐,非格式错误。✅

- **#5 DeepSeek 环境变量**:`unitree_g1_agentic_deepseek.py:22` 同样使用 `OPENAI_BASE_URL`,
  文档与仓库约定一致。✅

- **#8 缺 `__all__`**:`skill_container.py` 和 `system_prompt.py` 均无 `__all__`,新增文件
  保持一致。✅

## 主观取舍:无异议

- **#2 命令硬编码**:修改记录承认风险真实,理由是中文提示词嵌入英文 `ARM_COMMANDS_DOC`
  不自然,且 `commands.py` 手势集高度稳定。取舍合理。
- **#10 安全提醒位置**:属于文档 polish,暂留可接受。

## 总评

Cursor 的修改记录对原始 10 条 review 意见的分类准确,修复精确,不予修改的理由有据可查。审查通过。
