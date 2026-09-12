# Topsun DimOS

**北极星：Agent 控机器人**  
*North star: an agent that controls robots — eventually fine-grained dexterity (极限).*

[dimensionalOS/dimos](https://github.com/dimensionalOS/dimos) 是 **基线 OS**（模块、蓝图、传输、`@skill` / MCP），不是本仓的产品口号。  
本仓是 Topsun 的集成枢纽：在 DimOS 之上，按**能力**接入外部栈，走出 **Baseline → 极限灵巧控制**。

> **按能力合并，不 dump 第三方整仓。**  
> We merge **by capability** (thin bridges / shims). We do **not** vendor full third-party trees.

上游基线：[dimensionalOS/dimos](https://github.com/dimensionalOS/dimos) · 文档：[docs.dimensionalos.com](https://docs.dimensionalos.com) · 本仓路线图：[docs/ROADMAP.md](docs/ROADMAP.md)

---

## 路线图

| 阶段 | 状态 | 做什么 |
|------|------|--------|
| **Baseline** — DimOS | 已跟上游 `main` | 模块 / 蓝图 / LCM·Zenoh / 导航感知 / Go2·G1 |
| **Agent / MCP skills** | 已在本仓 | `McpServer` + `McpClient`，自然语言调 `@skill` |
| **HoloAgent `robot_bridge`** | 进行中 [#118](https://github.com/topsun-bot/topsun_dimos/pull/118) | HTTP skill shim（语义/相对导航）。不 vendor HoloAgent ROS 树 |
| **Sim assets（EmbodiedGen）** | 进行中 [#125](https://github.com/topsun-bot/topsun_dimos/pull/125) | 薄 catalog / MuJoCo 挂载。不 vendor 生成器与权重 |
| **MHS** | 预览笔记 only | [topsun-bot/model-hardware-standard](https://github.com/topsun-bot/model-hardware-standard)。**不**宣称已对接，**不** vendor 未开源运行时 |
| **灵巧 VLA** | 候选评估，未合入 | [LeRobot](https://github.com/huggingface/lerobot)、[OpenPI / π0](https://github.com/Physical-Intelligence/openpi) —— 评估对象，不是 vendor dump |

空间记忆 public shim 与现有导航大模块**保留**，不在集成清理中删除。

---

## 安装（本仓）

系统依赖：[Ubuntu](docs/installation/ubuntu.md) · [Nix](docs/installation/nix.md) · [macOS](docs/installation/osx.md) · [需求](docs/requirements.md)

```bash skip
export GIT_LFS_SKIP_SMUDGE=1
git clone https://github.com/topsun-bot/topsun_dimos.git
cd topsun_dimos
uv venv --python "3.12"
source .venv/bin/activate
uv pip install -e '.[base,unitree]'   # 本仓可编辑安装，不是 PyPI 上的 dimos
```

`--simulation` 再装仿真 extra：`uv pip install -e '.[sim]'`。  
开发全量：`uv sync --extra all`（需 [uv](https://docs.astral.sh/uv/) ≥ 0.9.25）。同样装的是当前 checkout。

只要上游 DimOS、不要本仓改动时，可用上游安装脚本。交互默认 **Library** 模式是 `uv pip install dimos[...]`（PyPI）；**Developer** 模式才 clone `dimensionalOS/dimos`：

```bash skip
curl -fsSL https://raw.githubusercontent.com/dimensionalOS/dimos/main/scripts/install.sh | bash
```

---

## 运行

| 命令 | 作用 |
|------|------|
| `dimos --replay run unitree-go2` | Go2 导航回放（`.[base,unitree]` 即可；首次约 200 MB LFS） |
| `dimos --simulation run unitree-go2-agentic` | 再装 `.[sim]` + `OPENAI_API_KEY` |
| `dimos --simulation run unitree-g1-agentic-sim` | 同上 |
| `dimos run unitree-go2-agentic --robot-ip <IP>` | 真机 Go2（`OPENAI_API_KEY`；不必装 `sim`） |
| `dimos --simulation run unitree-go2-agentic-ollama` | `.[sim]` + `ollama serve` + `ollama pull qwen3:8b` |
| `dimos list` | 全部蓝图 |

```bash skip
# 回放（无硬件）
dimos --replay run unitree-go2

# 仿真 + 默认 OpenAI agent
uv pip install -e '.[base,unitree,sim]'
export OPENAI_API_KEY=<YOUR_KEY>
dimos --simulation run unitree-go2-agentic

# 真机
export ROBOT_IP=<YOUR_ROBOT_IP>
dimos run unitree-go2-agentic

# 本地 Ollama（https://ollama.com ；daemon 不会自动 pull）
# ollama serve
# ollama pull qwen3:8b
# dimos --simulation run unitree-go2-agentic-ollama
```

更多蓝图：[docs/usage/blueprints.md](docs/usage/blueprints.md) · CLI：[docs/usage/cli.md](docs/usage/cli.md) · Agent 约定：[AGENTS.md](AGENTS.md)

---

## Agent / MCP

Agent 控机器人的当前路径：蓝图里同时放 `McpServer` + `McpClient`，LLM 发现并调用 `@skill`。默认 `unitree-go2-agentic` 用 `gpt-5.6-luna`，需 `OPENAI_API_KEY`。Go2 位移技能是 `move_to`（不是 `move`）。

```bash skip
export OPENAI_API_KEY=<YOUR_KEY>
dimos --replay run unitree-go2-agentic --daemon
dimos status
dimos agent-send "walk forward then stop"
dimos mcp list-tools
dimos mcp call move_to --arg x=0.5 --arg relative=true
dimos stop
```

MCP：`http://localhost:9990/mcp`（`GlobalConfig.mcp_port`）。

---

## 硬件（基线已支持）

| | 稳定 | Beta | Alpha / 实验 |
|---|------|------|----------------|
| 四足 | [Go2](docs/platforms/quadruped/go2/index.md) | | [B1](dimos/robot/unitree/b1) |
| 人形 | | [G1](docs/platforms/humanoid/g1/index.md) | |
| 臂 | | [xArm](docs/capabilities/manipulation/index.md)、[Piper](docs/capabilities/manipulation/index.md) | |
| 无人机 | | | [MAVLink / DJI](dimos/robot/drone/README.md) |

---

## 开发

```bash skip
export GIT_LFS_SKIP_SMUDGE=1
git clone https://github.com/topsun-bot/topsun_dimos.git
cd topsun_dimos
# uv run 会按需同步 default-groups=tests（含 pytest-xdist）
uv run pytest --numprocesses=auto dimos
```

模块 / 蓝图 / 传输：[docs/usage/modules.md](docs/usage/modules.md) · [docs/usage/blueprints.md](docs/usage/blueprints.md) · [docs/usage/transports/index.md](docs/usage/transports/index.md)

---

## 不会做什么

- 不把 DimOS 营销文案当成 Topsun 产品定位。
- 不把 HoloAgent、EmbodiedGen、LeRobot、OpenPI、MHS **整仓**拷进本仓库。
- **不宣称**已对接 Anthropic MHS（研究预览、尚未开源）。笔记：[model-hardware-standard](https://github.com/topsun-bot/model-hardware-standard)。
- **不引用未核实的病毒式演示**（例如所谓 GPT-6 Astra / 斯坦福五指魔方）作为本仓能力或路线依据。

灵巧「极限」只作方向；公开可核基线仅作参考，不是本仓结果：

- OpenAI (2019)，Shadow Dexterous Hand 解魔方 — [Solving Rubik’s Cube with a Robot Hand](https://openai.com/index/solving-rubiks-cube/) · [arXiv:1910.07113](https://arxiv.org/abs/1910.07113)
- [LeRobot](https://github.com/huggingface/lerobot)（Hugging Face）
- [OpenPI / π0](https://github.com/Physical-Intelligence/openpi)（Physical Intelligence）
