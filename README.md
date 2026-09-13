<div align="center">

<h1>TOPSUN DimOS</h1>

<p><strong>中坚科技 · 机器人 Agent 应用与工程集成</strong></p>
<p>让机器人理解任务、记住环境，并把导航、跟随与迎宾接成可验证的应用。</p>

<p>
  <img alt="面向 Go2 与 G1" src="https://img.shields.io/badge/Go2_%26_G1-E30920?style=flat-square" />
  <a href="LICENSE"><img alt="Apache 2.0 许可证" src="https://img.shields.io/badge/License-Apache_2.0-333333?style=flat-square" /></a>
</p>

<p>
  <a href="#features">自研功能</a> ·
  <a href="#quickstart">开始使用</a> ·
  <a href="#history">开发历程</a> ·
  <a href="#contributing">协作与文档</a>
</p>

</div>

TOPSUN DimOS 是基于 [dimensionalOS/dimos](https://github.com/dimensionalOS/dimos) 持续开发的机器人应用仓库。上游提供模块、数据流、硬件适配、Agent/MCP、感知和控制基础；TOPSUN 在此之上扩展面向 Go2、G1 的任务技能、空间记忆、导航与迎宾应用。

功能说明以 **2026-09-13 核对的 `main@f3bbe0e3`** 为依据。下面区分已合并实现与仍在开发的提案；代码存在、测试通过和实机验收是不同阶段。

<a id="features"></a>
<h2 align="center">我们在做什么</h2>

| TOPSUN 扩展 | 解决的问题 | 代码与合并记录 |
| :--- | :--- | :--- |
| **语义导航与到达复核** | 从位置名称、地标和记忆中寻找目标，加入分步导航、重定位与视觉复核流程 | [导航技能](/dimos/agents/skills/navigation.py) · [#85](https://github.com/topsun-bot/topsun_dimos/pull/85)、[#91](https://github.com/topsun-bot/topsun_dimos/pull/91)、[#99](https://github.com/topsun-bot/topsun_dimos/pull/99) |
| **空间记忆与盲区探索** | 保存场景位置与观察记录，结合地图寻找尚未充分观察的区域，减少重复探索 | [空间记忆入口](/dimos/perception/spatial_perception.py) · [探索说明](/docs/capabilities/memory/spatial_memory_blindspot_explorer.md) · [#90](https://github.com/topsun-bot/topsun_dimos/pull/90)、[#101](https://github.com/topsun-bot/topsun_dimos/pull/101) |
| **ReID 人员跟随** | 用人物外观特征关联跟随目标，并提供丢失后的等待与报告逻辑 | [follow_me](/dimos/agents/skills/follow_me.py) · [Qwen 跟随蓝图](/dimos/robot/unitree/go2/blueprints/agentic/unitree_go2_qwen_follow.py) · [#86](https://github.com/topsun-bot/topsun_dimos/pull/86) |
| **LiDAR 目标绕行** | 从代价地图提取障碍边缘，生成保持距离的切向绕行控制 | [orbit_object](/dimos/agents/skills/orbit_object.py) · [使用说明](/docs/development/orbit_object.md) · [#80](https://github.com/topsun-bot/topsun_dimos/pull/80) |
| **Landmark Pack 地标包** | 将场景地标打包、导入和导出，通过 CLI 管理可复用的地点信息 | [地标包](/dimos/landmark/landmark_pack.py) · [CLI](/dimos/cli/landmarks.py) · [#100](https://github.com/topsun-bot/topsun_dimos/pull/100) |
| **Go2 视觉导航实验** | 将 NoMaD 候选轨迹、代价地图评分、多航点选择和跟随控制接成实验链路 | [nav-go2 示例](/examples/nav-go2/README.md) · [#72](https://github.com/topsun-bot/topsun_dimos/pull/72) |
| **G1 迎宾与机载导览** | 提供原地迎宾、语音与手势；Orin 版本进一步接入 DDS 导航、标点和到站讲解 | [迎宾蓝图](/dimos/robot/unitree/g1/blueprints/agentic/unitree_g1_greeter.py) · [机载蓝图](/dimos/robot/unitree/g1/blueprints/agentic/unitree_g1_greeter_onboard.py) · [#111](https://github.com/topsun-bot/topsun_dimos/pull/111) |
| **运行与工程适配** | 为长运行记忆增加容量边界，整合 Go2 语音输出，并调整 fork 仓库的 CI 与依赖流程 | [#69](https://github.com/topsun-bot/topsun_dimos/pull/69)、[#73](https://github.com/topsun-bot/topsun_dimos/pull/73)、[#87](https://github.com/topsun-bot/topsun_dimos/pull/87) |

**使用边界：** NoMaD 示例需要额外模型与依赖；跟随和绕行需要相应感知、控制与硬件配置。当前 G1 迎宾默认采用模板路由，`llm_enabled=False`，不将模板外输入交给 LLM 自由回答。机载导览属于开发中的集成链路，不代表完整实机验收。

<details>
<summary><strong>仍在开发的提案</strong></summary>

截至上述核对时间，以下内容尚未合并到 main，不列为当前版本已交付功能：

- [#137](https://github.com/topsun-bot/topsun_dimos/pull/137)：Truth Capture GUI、VLX 风格航点跟随与代码生成轨迹评估。
- [#118](https://github.com/topsun-bot/topsun_dimos/pull/118)：HoloAgent robot bridge 接口适配。
- [#125](https://github.com/topsun-bot/topsun_dimos/pull/125)：EmbodiedGen 仿真资源桥接。
- [#132](https://github.com/topsun-bot/topsun_dimos/pull/132)：在 #120 之后继续同步上游更新。

以各 PR 的最新状态、评审意见和测试结果为准。

</details>

<a id="quickstart"></a>
<h2 align="center">开始使用</h2>

需要 Git、Git LFS、Python 3.12 和 uv。先按系统准备依赖：[Ubuntu](/docs/installation/ubuntu.md) · [macOS](/docs/installation/osx.md) · [Nix/Linux](/docs/installation/nix.md)。

```bash
GIT_LFS_SKIP_SMUDGE=1 git clone https://github.com/topsun-bot/topsun_dimos.git
cd topsun_dimos
uv sync --group tests --frozen
uv run dimos list
```

这里安装的是本仓库源码及测试依赖。模型、录制数据和硬件专项依赖按所选蓝图补齐，详见[系统要求](/docs/requirements.md)与[蓝图指南](/docs/usage/blueprints.md)。

| 想做什么 | 从哪里开始 |
| :--- | :--- |
| 不接机器人，先看录制回放 | `uv run dimos --replay run unitree-go2`，参见 [Go2 指南](/docs/platforms/quadruped/go2/index.md) |
| 运行 G1 仿真 | [G1 平台指南](/docs/platforms/humanoid/g1/index.md)，安装仿真依赖后选择相应蓝图 |
| 做 Go2 视觉导航实验 | [NoMaD 导航示例](/examples/nav-go2/README.md)，按说明准备模型权重 |
| 接入 Agent 与 MCP | [Agent 指南](/docs/capabilities/agents/index.md)，在包含 McpServer 的蓝图启动后查询技能 |
| 配置 G1 机载迎宾 | [Orin 迎宾部署](/docs/platforms/humanoid/g1/greeter_onboard.md) |
| 查看运行状态和日志 | `uv run dimos status` · `uv run dimos log` · [CLI 文档](/docs/usage/cli.md) |

回放用于观察录制数据，不会形成实体运动闭环。实体机器人运行前应完成对应平台的网络、限位、急停与场地检查。

<a id="history"></a>
<h2 align="center">从上游基础到 TOPSUN 应用</h2>

这个仓库保留了上游提交历史，因此贡献记录里既有 Dimensional 社区开发者，也有 TOPSUN 仓库贡献者，以及依赖更新、格式修复等自动化账号。**作者数量不等于本团队人数，提交数量也不等于新增功能数量。**

<details>
<summary><strong>第 1、3、10、100 次提交分别做了什么？</strong></summary>

按 `git log --first-parent --reverse f3bbe0e3` 从旧到新编号，只沿主线计算，避免把并行分支的先后混在一起。这些早期提交属于上游历史；“第 100 次提交”与“PR #100”不是一回事。

| 主线序号 | 提交 | 日期 | 当时做的事情 |
| :---: | :--- | :--- | :--- |
| 1 | [437ca4c9](https://github.com/topsun-bot/topsun_dimos/commit/437ca4c9) | 2025-10-18 | 创建最初 Python 包与构建产物 |
| 2 | [e361a94d](https://github.com/topsun-bot/topsun_dimos/commit/e361a94d) | 2025-10-18 | 导入项目内容 |
| 3 | [99ddcb0f](https://github.com/topsun-bot/topsun_dimos/commit/99ddcb0f) | 2025-10-18 | 增加 Agent、数据、环境和操作模块的空骨架 |
| 10 | [1bddc1a8](https://github.com/topsun-bot/topsun_dimos/commit/1bddc1a8) | 2025-10-18 | 接入 COLMAP 子模块 |
| 100 | [a4900b3f](https://github.com/topsun-bot/topsun_dimos/commit/a4900b3f) | 2025-11-30 | 清理构建产物、调整开发脚本与目录 |

该快照包含 373 个主线提交、1378 个可达提交；合并提交可以一次带入多人的历史。数字仅用于解释这份快照，不作为研发绩效指标。

</details>

<details>
<summary><strong>TOPSUN 功能里程碑与贡献来源</strong></summary>

下表列出代表性合并 PR 的作者账号，不替代完整贡献名单，也不将合并操作者当作全部代码的作者。

| 阶段 | 代表性工作 | PR 作者与证据 |
| :--- | :--- | :--- |
| 2026-05-22 | 目标绕行、长运行内存控制 | mundodr [#80](https://github.com/topsun-bot/topsun_dimos/pull/80) · feipeng1234 [#69](https://github.com/topsun-bot/topsun_dimos/pull/69) |
| 2026-05-25 | Go2 NoMaD 导航、传输配置扩展 | sguanke [#72](https://github.com/topsun-bot/topsun_dimos/pull/72) · hualdds [#84](https://github.com/topsun-bot/topsun_dimos/pull/84) |
| 2026-05-26 至 05-28 | 语义导航与视觉复核 | di-jiarong [#85](https://github.com/topsun-bot/topsun_dimos/pull/85) · feipeng1234 [#91](https://github.com/topsun-bot/topsun_dimos/pull/91) · guoyan88 [#99](https://github.com/topsun-bot/topsun_dimos/pull/99) |
| 2026-05-27 至 05-28 | ReID 跟随、空间记忆盲区探索 | Yaocheng-yan [#86](https://github.com/topsun-bot/topsun_dimos/pull/86) · mundodr [#90](https://github.com/topsun-bot/topsun_dimos/pull/90)、[#101](https://github.com/topsun-bot/topsun_dimos/pull/101) |
| 2026-05-28 | Landmark Pack 导入、导出与 CLI | dingyi0219 [#100](https://github.com/topsun-bot/topsun_dimos/pull/100) |
| 2026-06-23 | G1 迎宾、Orin 机载导览集成 | dingyi0219 [#111](https://github.com/topsun-bot/topsun_dimos/pull/111) |
| 2026-09-11 | 同步上游，保留 TOPSUN 导航与空间记忆兼容入口 | yixinzhangagent [#120](https://github.com/topsun-bot/topsun_dimos/pull/120) |

工程与文档贡献同样保留在历史中：例如 lihuawei-topsun 的 [CI 数据下载优化 #73](https://github.com/topsun-bot/topsun_dimos/pull/73)、jiangtao-huazhijian 的 [Go2 音频整合 #87](https://github.com/topsun-bot/topsun_dimos/pull/87)，以及 Yanmengxueecho 的 [阶段提交总结 #109](https://github.com/topsun-bot/topsun_dimos/pull/109)。

查看[完整提交历史](https://github.com/topsun-bot/topsun_dimos/commits/main/)、[贡献者记录](https://github.com/topsun-bot/topsun_dimos/graphs/contributors)，或阅读[2026 年 5 月的 100 个提交总结](/docs/development/main_100_commits_summary.md)。旧总结反映当时结构，当前文件路径以上方源码链接为准。

</details>

**最近一个月的主要更新：** 上游带来了 Zenoh 默认传输、Cockpit 视频/地图/聊天/按键语音、SQLite/MCAP 录制与云数据、cuVSLAM/DimSLAM、双臂遥操作和操作规划增强；TOPSUN 在 #120 中完成同步并恢复自身接口兼容。这些应记为上游能力与本仓库集成成果，而非全部归为 TOPSUN 原创算法。

<a id="contributing"></a>
<h2 align="center">协作与文档</h2>

| 开发入口 | 工程入口 |
| :--- | :--- |
| [模块](/docs/usage/modules.md) · [蓝图](/docs/usage/blueprints.md) · [配置](/docs/usage/configuration.md) | [测试](/docs/development/testing.md) · [Docker](/docs/development/docker.md) · [LFS 数据](/docs/development/large_file_management.md) |
| [导航](/docs/capabilities/navigation/index.md) · [空间记忆](/docs/capabilities/memory/index.md) · [机械臂](/docs/capabilities/manipulation/index.md) | [贡献指南](/CONTRIBUTING.md) · [Agent 开发约定](/AGENTS.md) · [CI 结果](https://github.com/topsun-bot/topsun_dimos/actions/workflows/ci.yml) |

从 `main` 创建功能分支，提交前运行相关检查，通过 PR 评审后合并。README、功能代码和验证证据应一起更新；默认主分支是开发版本，最新 CI 状态请查看实际运行记录。

---

<div align="center">
<p><strong>TOPSUN · 中坚科技</strong></p>
<p>基于 <a href="https://github.com/dimensionalOS/dimos">Dimensional 开源项目</a> 持续开发 · <a href="LICENSE">Apache-2.0</a></p>
<p><a href="https://www.topsunpower.cc/?lang=zh-cn">中坚科技官网</a> · <a href="https://github.com/topsun-bot/topsun_dimos/issues">问题反馈</a> · <a href="https://github.com/topsun-bot/topsun_dimos/pulls">参与开发</a></p>
</div>
