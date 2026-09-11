<div align="center">

# TOPSUN DimOS

**面向物理空间的智能体操作系统**  
*Agentive OS for physical space*

[![Code](https://img.shields.io/badge/Code-topsun--bot%2Ftopsun__dimos-181717?logo=github&logoColor=white)](https://github.com/topsun-bot/topsun_dimos)
![Website](https://img.shields.io/badge/Website-TBD-lightgrey)
![Paper](https://img.shields.io/badge/Paper-TBD-lightgrey)
![Dataset](https://img.shields.io/badge/Dataset-TBD-lightgrey)
[![Docker](https://img.shields.io/badge/Docker-docs-2496ED?logo=docker&logoColor=white)](docs/development/docker.md)
[![License](https://img.shields.io/badge/License-Apache%202.0-blue.svg)](LICENSE)

[新闻](#news) · [为何选择](#why-topsun-dimos) · [总体计划](#总体计划约一个月) · [Plugin](#量产可集成-plugin) · [交付物](#交付物) · [快速开始](#快速开始)

</div>

本仓库是 **TOPSUN 的 DimOS 工作线**，基于上游 [dimensionalOS/dimos](https://github.com/dimensionalOS/dimos) 演进。模块通过类型化流在 LCM / ROS2 / DDS 等传输层上通信；Blueprint 把模块组装成可运行的机器人栈；Skill 把 `grab()`、`follow_object()` 等能力交给智能体调用。出处与致谢见文末。

# News

- [2026.09.11] README 按 HoloMotion 结构重排：亮点、计划模板、Plugin 面与交付物清单（空项标 TBD，不编造链接）。
- [2026.06.23] `main` 合入 [PR #111](https://github.com/topsun-bot/topsun_dimos/pull/111)（G1 迎宾 / Orin 导览相关蓝图）。
- [2026.06.12] 新增 G1 迎宾与 Orin 导览迎宾蓝图（`unitree-g1-greeter*`）。
- [2026.05.29] 记录 `main` 上一批合入：语义导航、空间记忆、Landmark Pack、`follow_me` / `orbit_object`（见 [docs/development/main_100_commits_summary.md](docs/development/main_100_commits_summary.md)）。
- [2026.05.28] Landmark Pack 与语义导航稳健循环合入 `main`。

# Why TOPSUN DimOS

## 面向物理空间的智能体操作系统

TOPSUN DimOS 把感知、导航、空间记忆和 LLM 智能体做成可组合的模块栈：同一套 Blueprint / Skill / MCP 接口，覆盖回放、仿真和真机。目标是让「自然语言指挥机器人」成为可复用的工程面，而不是一次性脚本。

<table>
  <tr>
    <td align="center" width="50%">
      <a href="docs/capabilities/navigation/native/index.md"><img src="assets/readme/navigation.gif" alt="Navigation" width="100%"></a>
    </td>
    <td align="center" width="50%">
      <img src="assets/readme/perception.png" alt="Perception" width="100%">
    </td>
  </tr>
  <tr>
    <td align="center" width="50%">
      <h3><a href="docs/capabilities/navigation/native/index.md">导航与建图</a></h3>
      SLAM、动态避障、路径规划、自主探索（原生栈与 ROS 均可）
    </td>
    <td align="center" width="50%">
      <h3><a href="docs/capabilities/perception/readme.md">感知</a></h3>
      检测、三维投影、VLM、音频
    </td>
  </tr>
  <tr>
    <td align="center" width="50%">
      <a href="docs/capabilities/agents/readme.md"><img src="assets/readme/agentic_control.gif" alt="Agents" width="100%"></a>
    </td>
    <td align="center" width="50%">
      <img src="assets/readme/spatial_memory.gif" alt="Spatial Memory" width="100%">
    </td>
  </tr>
  <tr>
    <td align="center" width="50%">
      <h3><a href="docs/capabilities/agents/readme.md">智能体控制与 MCP</a></h3>
      自然语言下发技能；`McpServer` 在 `localhost:9990/mcp` 暴露 `@skill`
    </td>
    <td align="center" width="50%">
      <h3>空间记忆</h3>
      时空 RAG、动态记忆、物体定位与持久化
    </td>
  </tr>
</table>

## 亮点与特色

这些能力来自本仓库已实现的模块与蓝图（含上游 DimOS 与 TOPSUN 增量），不是未发表论文的承诺。

| 亮点 | 仓库中的落点 | 解决什么问题 |
|------|----------------|--------------|
| **模块 + 类型化流** | `In[T]` / `Out[T]`，LCM / SHM / ROS / DDS | 感知、规划、控制在进程间按名字与类型自动接线 |
| **Blueprint 组合** | `autoconnect(...)`，`dimos run <blueprint>` | 同一套栈可回放 / 仿真 / 真机切换 |
| **Skill 与 MCP** | `@skill`、`McpServer` + `McpClient` | 智能体与外部工具用同一套 RPC 调物理技能 |
| **四足 / 人形参考栈** | Go2、G1（含迎宾与 Orin 机载导览蓝图） | 办公室导航、迎宾对话、机载带路等场景 |
| **导航与语义闭环** | 原生导航、拓扑 / 语义导航、视觉到达复核 | 「去门口 / 找物体」而不仅是坐标点 |
| **空间记忆与地标包** | Spatial memory、`dimos landmarks`、demo office pack | 地标导入导出、跨次运行记住门与物体 |
| **跟随 / 绕行技能** | `follow_me`、`orbit_object` | 跟人走、绕障碍或展品一圈 |
| **操作臂与遥操作** | xArm / Piper / Quest / 手机遥操作蓝图 | 规划、感知抓取、VR / 手机遥控 |

完整蓝图列表：`dimos list` 或 [`dimos/robot/all_blueprints.py`](dimos/robot/all_blueprints.py)。

## 论文点

### 已发表论文

暂无。本仓库没有 CITATION 文件，也没有已登记的 arXiv / 会议论文链接。

### 开放研究点

下列是可从现有代码追问的候选方向，**不是已发表贡献**，也未绑定作者或实验结论。

- 语义导航稳健闭环：拓扑路点 + VLM 到达复核，如何量化失败与恢复。
- 空间记忆 / Embodied RAG：门与地标的持久化、去重、跨 session 召回。
- Landmark Pack：场景地标的导入导出与多机复用。
- 机载迎宾（G1 Orin）：模板对话 + 建图带路 + 到站讲解，不用开放域 LLM 现场编造。
- 技能层可组合性：`@skill` / MCP 作为量产集成面，与模块流如何解耦。
- Replay / 仿真 / 真机同一 Blueprint 的评测可重复性。

# 总体计划（约一个月）

仓库内没有人员花名册或已签字的排期文档。下表是空模板，**请勿把空单元格当成已分配任务**。

> 人员与排期待填

## 人员分工

| Role | Owner | Feature | Scenario | Week |
|------|-------|---------|----------|------|
| | | | | |
| | | | | |
| | | | | |
| | | | | |

## 功能与场景

| 功能 | 场景 | 要解决的问题 | Owner | 周次 |
|------|------|--------------|-------|------|
| | | | | |
| | | | | |
| | | | | |

## 里程碑

| 周次 | 目标 | 交付 | 状态 |
|------|------|------|------|
| W1 | | | 待填 |
| W2 | | | 待填 |
| W3 | | | 待填 |
| W4 | | | 待填 |

# 量产可集成 Plugin

量产集成面是仓库里已经存在的扩展点，不是单独的插件商店。新能力按下列方式挂入，无需改核心运行时协议。

| 集成面 | 怎么挂 | 量产含义 |
|--------|--------|----------|
| **Blueprint** | 模块级变量 + `autoconnect()`，`dimos run <name>` | 交付一条可启动的机器人栈 |
| **Module** | `In[T]` / `Out[T]` + `@rpc` | 可替换的感知 / 规划 / 连接子系统 |
| **Skill** | `@skill`（含 docstring 与类型标注） | 对 LLM / MCP 暴露的物理动作 |
| **MCP** | `McpServer.blueprint()` + `McpClient.blueprint()` | HTTP 工具面，默认 `http://localhost:9990/mcp` |

未单独评估过产线 SLA 的条目一律标 **状态待评估**。文档里写明「开发期最小可用 / 真机待验证」的，按文档转述，不当成已量产。

## 已知 Blueprint（摘自 AGENTS.md 与本仓库注册表）

| Blueprint | 形态 | Agent / MCP | 说明 | 状态 |
|-----------|------|-------------|------|------|
| `unitree-go2` | Go2 · 回放 / 真机 | — | 感知 + 建图导航 | 状态待评估 |
| `unitree-go2-agentic` | Go2 · 真机 / 回放 | McpClient + McpServer | 智能体 + 技能 | 状态待评估 |
| `unitree-g1-agentic` | G1 · 真机 | 是 | 人形智能体栈 | 状态待评估 |
| `unitree-g1-agentic-sim` | G1 · MuJoCo | GPT-4o（G1 prompt） | 无真机的完整智能体仿真 | 状态待评估 |
| `unitree-g1-greeter` | G1 · 笔记本 WebRTC | McpServer + McpClient | 原地迎宾：对话 + 语音 + 手臂手势，不含移动 | 文档称开发期最小可用；状态待评估 |
| `unitree-g1-greeter-voice` | 同上 + 麦克风 | 同上 | 语音输入迎宾 | 状态待评估 |
| `unitree-g1-greeter-onboard` | G1 · Jetson Orin | 模板路由（默认可关 LLM） | 机载建图 / 标点带路 / 到站讲解 | 文档：代码阶段完成，真机行为待 Orin 验证 |
| `xarm-perception-agent` | xArm · 真机 | GPT-4o | 操作 + 感知 + 智能体 | 状态待评估 |
| `xarm-perception-sim-agent` | xArm · 仿真 | GPT-4o | 同上，仿真 | 状态待评估 |
| `xarm7-planner-coordinator` | xArm7 · 真机 | — | 轨迹规划协调 | 状态待评估 |
| `teleop-quest-xarm7` | xArm7 · 真机 | — | Quest VR 遥操作 | 状态待评估 |
| `dual-xarm6-planner` | xArm6×2 · 真机 | — | 双臂运动规划 | 状态待评估 |

`dimos list` 列出全部非 demo 蓝图。平台文档：[Go2](docs/platforms/quadruped/go2/index.md) · [G1](docs/platforms/humanoid/g1/index.md) · [G1 迎宾](docs/platforms/humanoid/g1/greeter.md)。

## 本仓库已覆盖的硬件方向

| 四足 | 人形 | 臂 | 无人机 | 其他 |
|------|------|----|--------|------|
| Unitree Go2 Pro/Air | Unitree G1 | xArm、AgileX Piper | MAVLink、DJI Mavic | Unitree B1 |

成熟度（稳定 / beta / 实验）未在本仓库做独立签核，**状态待评估**。上游 README 曾给出色标，此处不沿用为 TOPSUN 产线结论。

# 交付物

只链接着陆在本仓库或已核对的公开地址。没有的条目保持 TBD，不编造 arXiv、数据集主页或 DockerHub 宣传 tag。

| 交付物 | 状态 | 链接 / 路径 |
|--------|------|-------------|
| 网站 | 无 | TBD |
| Code | 有 | [topsun-bot/topsun_dimos](https://github.com/topsun-bot/topsun_dimos) |
| 论文 | 无 | TBD（无 CITATION，无已发表论文条目） |
| 数据集 | 无公开发布集 | TBD。仓库内有回放 / LFS 测试数据（见 `data/` 与 [large_file_management.md](docs/development/large_file_management.md)），不是对外数据集主页 |
| Docker | 建设中（源码 + 文档 + CI 目标仓库） | 源码 [`docker/`](docker/)、[`.dockerignore`](.dockerignore)、[`.devcontainer`](.devcontainer)；说明 [docs/development/docker.md](docs/development/docker.md) |

### Docker 实际存在什么

| 路径 | 作用 |
|------|------|
| `docker/python/` | 非 ROS Python 运行时镜像 |
| `docker/dev/` | 开发镜像（git、tmux、pre-commit 等）+ compose |
| `docker/ros/` | ROS2 Humble 基础镜像 |
| `.devcontainer/devcontainer.json` | 指向文档中的 `ghcr.io/topsun-bot/dev:dev` |
| `.github/workflows/docker-build.yml` | 向 `ghcr.io/topsun-bot/` 推送的 CI |

文档记录的镜像名：`python`、`dev`、`ros`、`ros-python`、`ros-dev`；tag 约定 `main` → `latest`，`dev` → `dev`。本地构建：

```bash
./bin/dockerbuild python
./bin/dockerbuild dev
./bin/dockerbuild ros
```

文档示例：

```bash
docker run -it ghcr.io/topsun-bot/dev:latest bash
docker run -it ghcr.io/topsun-bot/ros-dev:latest bash
```

未在此宣称镜像一定对匿名用户可拉。若 GHCR 包为私有，需组织权限。不要把未核验的 DockerHub 宣传 tag 当成已发布制品。

# 快速开始

## 安装

不要使用上游 `dimensionalOS/dimos` 的 `curl \| bash` 安装脚本当作本仓库安装入口。从本仓库开发：

```bash
# 大文件按需拉取
export GIT_LFS_SKIP_SMUDGE=1
git clone https://github.com/topsun-bot/topsun_dimos.git
cd topsun_dimos

uv sync --extra all
```

系统依赖：[Ubuntu 22.04 / 24.04](docs/installation/ubuntu.md) · [NixOS / Linux](docs/installation/nix.md) · [macOS](docs/installation/osx.md)（macOS 文档标为较早期支持）。

仅作库使用时：

```bash
uv venv --python "3.12"
source .venv/bin/activate
uv pip install 'dimos[base,unitree]'
```

## 运行

```bash
dimos list

# Go2：感知 + 建图（回放，无需真机）
dimos --replay run unitree-go2
dimos --replay run unitree-go2 --daemon

# Go2：智能体 + 技能 + MCP
dimos --replay run unitree-go2-agentic
dimos run unitree-go2-agentic --robot-ip 192.168.123.161

# G1：仿真智能体 / 真机
dimos --simulation run unitree-g1-agentic-sim
dimos run unitree-g1-agentic --robot-ip 192.168.123.161
```

| 命令 | 做什么 |
|------|--------|
| `dimos --replay run unitree-go2` | 四足导航回放（SLAM、costmap、规划） |
| `dimos --replay run unitree-go2-agentic` | 四足智能体 + MCP（回放） |
| `dimos --simulation run unitree-go2` | Go2 MuJoCo 仿真 |
| `dimos --simulation run unitree-g1-agentic-sim` | G1 仿真 + 智能体 + 技能 |
| `dimos run unitree-g1-greeter` | G1 原地迎宾（笔记本 + WebRTC） |
| `dimos run demo-camera` | 摄像头 demo，无需机器人 |

回放首次运行会从 LFS 拉数据，窗口可能短暂全黑。完整蓝图说明：[docs/usage/blueprints.md](docs/usage/blueprints.md)。

## 进程与 MCP

```bash
dimos run unitree-go2-agentic --daemon
dimos status
dimos log -f
dimos agent-send "explore the room"
dimos mcp list-tools
dimos mcp call relative_move --arg forward=0.5
dimos stop
```

MCP 仅在蓝图包含 `McpServer` 时可用。CLI：[docs/usage/cli.md](docs/usage/cli.md)；给编码智能体看的入口：[AGENTS.md](AGENTS.md)。

## 组合一个 Blueprint

```python
from dimos.core.coordination.blueprints import autoconnect
from dimos.agents.mcp.mcp_client import McpClient
from dimos.agents.mcp.mcp_server import McpServer

# 与仓库内 Go2 agentic 蓝图相同的挂载方式
unitree_go2_agentic = autoconnect(
    unitree_go2_spatial,    # 机器人栈
    McpServer.blueprint(),  # HTTP MCP，暴露全部 @skill
    McpClient.blueprint(),  # LLM 智能体从 McpServer 拉工具
    _common_agentic,        # 技能容器
)
```

参考：`dimos/robot/unitree/go2/blueprints/agentic/unitree_go2_agentic.py`。

## 文档

- [模块](docs/usage/modules.md) · [Blueprint](docs/usage/blueprints.md) · [配置](docs/usage/configuration.md)
- [传输层](docs/usage/transports/index.md) · [可视化](docs/usage/visualization.md)
- [测试](docs/development/testing.md) · [CLI](docs/development/dimos_run.md)
- [智能体](docs/agents/) · [导航](docs/capabilities/navigation/readme.md)

# Citation

暂无正式 citation。没有 `CITATION.cff` / `CITATION.bib`，也没有可引用的论文编号。需要引用本仓库时，请使用 Git URL 与 commit，待正式 bib 发布后再替换。

# Acknowledgements

TOPSUN DimOS 基于并致谢上游：

- [dimensionalOS/dimos](https://github.com/dimensionalOS/dimos) — DimOS 模块系统、Blueprint、Skill / MCP、Go2 / G1 / 导航与感知栈。本仓库是 TOPSUN 在该线上的工作，不是 Dimensional 官方发行版。上游的 Discord / Trendshift / star 数不代表本仓库指标。

本仓库还依赖（不完全列表）：[LCM](https://lcm-proj.github.io/)、ROS 2、[uv](https://github.com/astral-sh/uv)、Unitree 机器人与 SDK、以及文档中写明的导航 / 感知 / 操作臂组件。

许可： [Apache License 2.0](LICENSE)（版权声明沿用上游 Dimensional Inc. 文本）。
