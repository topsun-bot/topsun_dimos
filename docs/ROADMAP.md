# Topsun × DimOS 路线图

本仓北极星：**Agent 控机器人**，终点是细粒度灵巧控制（极限）。  
[dimensionalOS/dimos](https://github.com/dimensionalOS/dimos) 是基线 OS，不是产品口号。

README 短版：[README.md](../README.md)。

## 合并原则

**按能力合并，不 dump 第三方整仓。**

| 做 | 不做 |
|----|------|
| 薄 HTTP / `@skill` / catalog / 配置钩子 | 把相关 GitHub 仓整树拷进 `dimos/` |
| 复用 DimOS 已有模块、MCP、蓝图 | 为「看起来集成了」重复造导航 / AgentOS |
| 外链上游与候选栈 | 把未开源运行时（如 MHS）vendor 进来 |
| 保留已有空间记忆 shim 与导航大模块 | 借「清理厨房水槽」删掉 Topsun 空间记忆 / 导航 |

## 阶段

### 1. Baseline — DimOS

**状态：** 已跟上游，持续合入 `dimensionalOS/dimos` `main`。

模块、蓝图、`In`/`Out` 流、LCM / Zenoh / SHM、导航与感知、Go2 / G1 真机与仿真。这是操作系统层，不是 slogan。

### 2. Agent / MCP skills

**状态：** 已在本仓。

`McpServer` + `McpClient`，`@skill` 暴露给 LLM。当前「Agent 控机器人」主路径是技能编排（走、看、说、导航），还不是关节级灵巧策略。

见 [docs/capabilities/agents/index.md](capabilities/agents/index.md)、[AGENTS.md](../AGENTS.md)。

### 3. HoloAgent `robot_bridge`

**状态：** 进行中 — [PR #118](https://github.com/topsun-bot/topsun_dimos/pull/118)。

Horizon [HoloAgent](https://github.com/HorizonRobotics/HoloAgent) 已承认 DimOS。本仓只包一层对**正在运行的** `robot_bridge`（语义目标 / 相对导航）的 skill。不 vendor FSR-VLN / FAST-LIVO / Nav2 树。默认 `unitree-*-agentic` 蓝图不变。

### 4. Sim assets — EmbodiedGen

**状态：** 进行中 — [PR #125](https://github.com/topsun-bot/topsun_dimos/pull/125)。

[EmbodiedGen](https://github.com/HorizonRobotics/EmbodiedGen)（Topsun fork：[topsun-bot/EmbodiedGen](https://github.com/topsun-bot/EmbodiedGen)）在仓外生成 URDF / MJCF。本仓只做发现、注册、挂到已有 MuJoCo 蓝图。不 vendor 生成器、checkpoint、CUDA 权重。

### 5. MHS（预览 only）

**状态：** 笔记仓，**未**接入代码。

[topsun-bot/model-hardware-standard](https://github.com/topsun-bot/model-hardware-standard) 跟踪 Anthropic [Model Hardware Standard](https://www.anthropic.com/news/model-hardware-standard-research-preview)（2026-08-27 研究预览）。官方尚未开源。

**不要：** vendor 未公开运行时；在获得预览/规范前宣称「已对接 MHS」。

DimOS MCP 继续做机器人语义技能；MHS 若开放，再评估设备发现与设备安全限制（行程、功率上限等），另开 PR。

### 6. 灵巧 VLA（候选，未合入）

**状态：** 评估对象，不是已合并栈。

| 候选 | 角色 |
|------|------|
| [LeRobot](https://github.com/huggingface/lerobot) | 数据集 / 策略训练与评估框架。本仓已有 LeRobot v3 **数据写出**（`dimos/imitation`），不是整仓训练运行时 |
| [OpenPI / π0](https://github.com/Physical-Intelligence/openpi) | 通用机器人 VLA（π0）推理栈，待评估是否用薄适配接入 |

合入标准与 HoloAgent / EmbodiedGen 相同：只收 DimOS 缺的能力面（数据格式、策略服务、控制接口），不拷训练框架本体。

## 灵巧「极限」怎么说

方向是细粒度灵巧控制，**不是**已交付产品。

**不要**把未核实的病毒式叙事（所谓 GPT-6 Astra、斯坦福五指魔方等）写成路线依据或本仓结果。

可核的公开基线（仅参考）：

- OpenAI (2019)，Shadow Dexterous Hand 解魔方 — [Solving Rubik’s Cube with a Robot Hand](https://openai.com/index/solving-rubiks-cube/) · [arXiv:1910.07113](https://arxiv.org/abs/1910.07113)
- Hugging Face [LeRobot](https://github.com/huggingface/lerobot)
- Physical Intelligence [OpenPI / π0](https://github.com/Physical-Intelligence/openpi)
