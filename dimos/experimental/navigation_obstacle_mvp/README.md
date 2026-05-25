# Navigation Obstacle MVP（实验 · 操作手册）

**方案、数据流、模块清单、PRD 对照与已知限制**见主方案文档  
[**§12 MVP 实现状态（2026-05）**](../../../docs/development/ai_navigation_refactor_master_plan.md#12-mvp-实现状态2026-05)。  
周会/PRD 全文：[`docs/development/ai_navigation_refactor_master_plan.md`](../../../docs/development/ai_navigation_refactor_master_plan.md)。

本文只保留 **怎么跑、怎么测、怎么排障**；不重复 §12 中的架构叙述。

## 行为（速查）

| `obstacle_crossing_mvp` | 遇障时 |
|-------------------------|--------|
| `False`（默认） | 一律 `obstacle_found` → 重规划（与改动前相同） |
| `True` | 按高度三态：`≤10 cross` / `10–50 detour` / `>50 stop`；详见主方案 §12.2 |

MuJoCo office1 走廊内高度由位姿推断，优先于 CLI；未知高度保守 `detour`。

## 开启方式

`unitree-go2-agentic`（及所有含 `GO2Connection` 的 Go2 蓝图）**必须**指定连接方式，否则会因缺少
`--robot-ip` 在 worker 中报错。任选其一：

| 模式 | 说明 |
|------|------|
| 真机 | `--robot-ip <IP>`（WebRTC） |
| MuJoCo 仿真 | `--simulation` 或 `--simulation mujoco` |
| 录包回放 | `--replay`（可选 `--replay-db`） |

CLI 示例：

```bash
# MuJoCo 仿真 + MVP（本地开发 / 无真机；默认启动 DimOS viewer + MuJoCo 弹窗）
uv run dimos --simulation mujoco --obstacle-crossing-mvp --obstacle-crossing-mvp-height-cm 8 \
  run unitree-go2-agentic

# 真机 + MVP（短距、低速、有人监护）
uv run dimos --robot-ip 192.168.123.161 --obstacle-crossing-mvp --obstacle-crossing-mvp-height-cm 8 \
  run unitree-go2-agentic

# 录包回放 + MVP（无硬件、无 MuJoCo）
uv run dimos --replay --obstacle-crossing-mvp --obstacle-crossing-mvp-height-cm 8 run unitree-go2-agentic
```

常见笔误 `--obstacle-crossing-mvp-height-cm8`（缺空格或 `=`）会在启动前自动拆成
`--obstacle-crossing-mvp-height-cm 8`；若未修复则 Typer 报错且 `height_cm` 保持 `None`（遇障走
`detour` 而非 `cross`）。

### EdgeTAM / CUDA（`unitree-go2-agentic`）

`unitree-go2-agentic` 基于 `unitree_go2_spatial`，内含 `SecurityModule`（EdgeTAM 视觉跟踪）。
**无可用 CUDA 时蓝图仍可 deploy**：EdgeTAM 会降级为 stub，日志 `warning`，巡逻/检测仍可用，但
**不会**进入 EdgeTAM 跟随（`start_security_patrol` / `follow_person` 的跟踪段被跳过）。

| 场景 | 说明 |
|------|------|
| 有 NVIDIA GPU + 正常 PyTorch CUDA | EdgeTAM 全功能（推荐真机安保巡逻） |
| `CUDA_VISIBLE_DEVICES=""` 或 CPU-only | 可跑 MuJoCo + MVP + agent；安保仅 YOLO 检测，无跟踪跟随 |
| PyTorch CUDA 初始化失败（如 Error 804） | 同 CPU-only：deploy 成功，EdgeTAM 禁用 |

MuJoCo 弹窗/headless 常用 `CUDA_VISIBLE_DEVICES=""` 避免与 GLFW 冲突；此时 agentic 栈仍应正常启动。

### OpenAI API Key（`unitree-go2-agentic`）

部分 agentic 能力依赖 **`OPENAI_API_KEY`**（进程环境或 `.env`）。**未设置时蓝图仍可完整启动**（MuJoCo + MVP + MCP + 导航），对应模块会降级并打 `warning`：

| 模块 | 无 `OPENAI_API_KEY` 时 | 仍可用 |
|------|------------------------|--------|
| `SpeakSkill`（TTS） | 跳过初始化；`speak()` 返回 *TTS disabled* | MCP 直接调其他 skill |
| `McpClient`（LLM Agent） | 跳过 LangGraph 初始化；`agent-send` 返回提示 | `dimos mcp call` 调用全部 skill |
| `SecurityModule`（EdgeTAM） | 见上节 CUDA 降级 | YOLO 检测、巡逻（无跟踪跟随） |
| `WebInput`（Whisper STT） | 模型未缓存且无法从 HuggingFace 下载时跳过 STT；`--no-web-input-stt-enabled` 强制仅文本 | 网页文本框、`dimos agent-send`、MCP |

本地 MuJoCo + MVP 人工测试导航时，通常**不需要** API key：

```bash
unset OPENAI_API_KEY
uv run dimos --simulation mujoco --obstacle-crossing-mvp --obstacle-crossing-mvp-height-cm 8 \
  run unitree-go2-agentic
# 导航 / MCP：dimos mcp call go_to --arg x=1.0 --arg y=2.0
# LLM / 语音：设置 export OPENAI_API_KEY=sk-... 后重启
```

代码中构造配置：

```python
from dimos.core.global_config import GlobalConfig

config = GlobalConfig(
    obstacle_crossing_mvp=True,
    obstacle_crossing_mvp_height_cm=8.0,
)
```

高度单位为厘米。走廊内推断与 preempt / creep 规则见主方案 [§12.2–§12.5](../../../docs/development/ai_navigation_refactor_master_plan.md#12-mvp-实现状态2026-05)。

### 简化场景 + 三门槛（office1）

几何与决策对照见主方案 [§12.5](../../../docs/development/ai_navigation_refactor_master_plan.md#125-mujoco-验证场景office1-北走廊)。本地速查：

| 几何名 | 中心 (x, y) | 决策高度 | 期望 |
|--------|-------------|----------|------|
| `mvp_threshold_low` | (-1.12, 1.45) | 5 cm | `cross` |
| `mvp_threshold_medium` | (-1.12, 3.15) | 20 cm | `detour` / preempt |
| `mvp_threshold_high` | (-1.12, 4.85) | 58 cm | `stop` + `obstacle_abort` |

从 spawn `(-1,1)` 向北依次过槛，或在 viewer 点击对应 goal。跨槛 creep / MockController 限幅见 §12.2；真机须短距、监护。

### 界面设起点 / 终点（7779 与 Rerun）

| 方式 | 说明 |
|------|------|
| **Command Center（7779）** | 需 `--viewer rerun-web` 时在浏览器打开 [http://localhost:7779](http://localhost:7779)，在地图上点击 → `goal_request`（`PoseStamped`） |
| **Rerun 3D** | 默认 `viewer=rerun`（原生 dimos-viewer）：在 Rerun 3D 视图点击 → `clicked_point` → `ReplanningAStarPlanner` |
| **CLI 目标** | `dimos topic send /goal_request 'PoseStamped(position=Vector3(x, y, 0.0))'` |
| **起点** | 默认 `mujoco_start_pos=-1,1`；可 `--mujoco-start-pos "-1.0, 0.95"` 略南于低门槛；MuJoCo 被动 viewer 中可拖拽机器人（若已开窗口） |

**点击后狗不动时检查**：`dimos log` 应依次出现 `Click goal published`（7779）或 `Got new goal`（Rerun `clicked_point` / CLI）→ `Publishing nav_cmd_vel`（非零 `angular_z` 或 `linear_x`）→ MuJoCo 窗口中机器人转向/前进。若只有前两步没有 `Publishing nav_cmd_vel`，看重规划是否因 `Robot is stuck` 循环（spawn 附近 MVP 原地转向曾误判卡住，已跳过 rotation 阶段的 stuck 检测）；若有三步仍不动，用 `dimos topic echo /cmd_vel` 确认 MovementManager 是否在转发。

推荐一行（弹窗 + MVP + 简化场景 + DimOS viewer；无需手写 `--viewer`）：

```bash
GDK_BACKEND=x11 MUJOCO_GL=glfw CUDA_VISIBLE_DEVICES="" uv run dimos --simulation mujoco \
  --obstacle-crossing-mvp --mujoco-offscreen-gl off run unitree-go2
```

界面测试步骤（cross / detour / stop）：

1. 启动上式命令；默认会弹出原生 dimos-viewer。需要地图侧栏时用 `--viewer rerun-web` 并打开 http://localhost:7779 。
2. **低门槛 cross**：点击 `(-1.12, 1.55)` 或更北；日志应含 `action=cross obstacle_height_cm=5`（或接近时 pose 预判降速），机器人低速继续前进。
3. **中门槛 detour**：点击 `(-1.12, 3.25)` 或 `(-1.12, 3.15)`；日志应含 `action=detour obstacle_height_cm=20`（或 `MVP detour preempt`）并触发重规划，**不应**长时间 `action=cross obstacle_height_cm=8`。
4. **高门槛 stop**：点击 `(-1.12, 4.95)` 或 `(-1.12, 4.85)`；距高槛 **≤0.4 m** 时应 `action=stop obstacle_height_cm=58` 且 `obstacle_abort`、取消目标。

仅 headless + fake LiDAR 时加 `--no-mujoco-viewer --viewer none --mujoco-offscreen-gl off`。

| 开关 | CLI |
|------|-----|
| 与 MVP 一并注入（推荐） | `--obstacle-crossing-mvp` |
| 仅加障碍、不改导航决策 | `--mujoco-navigation-test-obstacles` |

换房间（仍注入 MVP 障碍，若开启上表开关）：

```bash
uv run dimos --simulation mujoco --mujoco-room office1 --mujoco-navigation-test-obstacles \
  run unitree-go2
```

`--mujoco-room-from-occupancy <grid.pkl>` 会从占据栅格生成 MJCF，同样会在合并 Go2 模型时注入 MVP 障碍。

### 弹窗 vs headless 命令对照

| 目标 | 命令要点 | 说明 |
|------|----------|------|
| **弹出 MuJoCo 窗口（推荐）** | 默认 `mujoco_viewer=True`；**勿**加 `--no-mujoco-viewer` | `--viewer none` 只关 DimOS viewer，**不会**关 MuJoCo 窗口 |
| **成功弹窗（本机验证）** | `GDK_BACKEND=x11 MUJOCO_GL=glfw` + `--mujoco-offscreen-gl off` | 见下方「成功弹窗命令」；日志应含 `MuJoCo passive viewer opened successfully` |
| **headless（无窗口）** | `--no-mujoco-viewer` 或 `--mujoco-headless` | 纯物理 + odom；可配合 fake LiDAR |
| **DimOS 可视化** | `--simulation mujoco` 默认 `rerun`（原生）；`--viewer rerun-web` 浏览器；`--viewer none` 关闭 | 与 MuJoCo 被动 viewer **无关** |
| **离屏相机/LiDAR** | `--mujoco-offscreen-gl {auto,egl,osmesa,off}` | 与 passive viewer **独立**；弹窗优先用 `off` |

**不要**加 `--no-mujoco-viewer` 或 `--mujoco-headless`，除非明确只要无窗口 headless 仿真。

#### 成功弹窗命令（推荐前缀）

Wayland / NVIDIA 栈上，仅 `CUDA_VISIBLE_DEVICES=""` 而无 `MUJOCO_GL=glfw` 时 GLFW 可能仍 FAIL。
弹窗前会先跑 **GLX preflight** 子进程（`viewer_probe`）；通过后主进程 `launch_passive`，避免
`ERROR: could not create window` 无限挂起。

```bash
# 推荐：X11 + GLFW 弹窗 + 关闭离屏 GL + DimOS viewer（默认 rerun 原生）
GDK_BACKEND=x11 MUJOCO_GL=glfw CUDA_VISIBLE_DEVICES="" uv run dimos --simulation mujoco \
  --mujoco-offscreen-gl off \
  --obstacle-crossing-mvp --obstacle-crossing-mvp-height-cm 8 run unitree-go2

# 弹窗 + 离屏 EGL（viewer 与离屏 GL 隔离；离屏失败仍尝试 viewer，并降级 fake LiDAR）
GDK_BACKEND=x11 MUJOCO_GL=glfw CUDA_VISIBLE_DEVICES="" uv run dimos --simulation mujoco \
  --mujoco-offscreen-gl auto \
  --obstacle-crossing-mvp --obstacle-crossing-mvp-height-cm 8 run unitree-go2
```

成功时在 `dimos log` 或终端 stderr 应看到：`MuJoCo passive viewer opened successfully`。
诊断：`uv run python -m dimos.simulation.mujoco.diagnose`（加 `--shell` 跑 glxinfo / nvidia-smi）。

仅验证障碍是否出现在场景中（不启用 MVP 决策）：

```bash
uv run dimos --simulation mujoco --mujoco-navigation-test-obstacles run unitree-go2
```

数据流与绕槛重规划见主方案 [§12.3](../../../docs/development/ai_navigation_refactor_master_plan.md#123-端到端数据流)。

## 测试 / 仿真验证

### 仿真式单元测试（默认，无 Mujoco / LCM / 硬件）

`dimos/experimental/navigation_obstacle_mvp/tests/` 下的测试为纯 Python 单元测试：
`conftest.py` 会覆盖会话级 LCM autoconf，不依赖 root multicast、蓝图或真机。

| 文件 | 覆盖 |
|------|------|
| `test_obstacle_decision.py` | `decide_obstacle_action`：高度 `None`、≤10 `cross`、10–50 `detour`、>50 `stop` 及自定义阈值 |
| `test_local_planner_obstacle_mvp.py` | `LocalPlanner._handle_obstacle_ahead()` / detour preempt（0.6 m、≥18 cm） |
| `test_forward_obstacle_height.py` | 走廊内按位姿推断低/中/高门槛高度 |
| `test_mvp_costmap.py` | 中/高槛 lethal 膨胀、绕槛 waypoint、穿槛检测 |
| `test_global_planner_mvp_detour.py` | `obstacle_found` 重规划绕槛路径、不穿过槛心 |
| `test_global_planner_obstacle_abort.py` | `GlobalPlanner` 收到 `obstacle_abort` 时 `cancel_goal`，不调用 `_replan_path` |

遇障占用由调用方假定（测试直接调用 `_handle_obstacle_ahead()`，等价于
`PathClearance.is_obstacle_ahead()` 已为真时的分支）。完整规划循环与代价图更新不在此套件内。

运行：

```bash
uv run pytest dimos/experimental/navigation_obstacle_mvp/ -q
```

带用例名：

```bash
uv run pytest dimos/experimental/navigation_obstacle_mvp/ -v
```

### 导航栈 self_hosted / rosbag（可选，非本 MVP 专用）

仓库内 `dimos/navigation/` 另有标记为 `self_hosted` 的集成测试（rosbag、跨墙规划等），
**不包含** obstacle MVP 开关；仅在自托管 runner 或已配置数据的环境运行，例如：

```bash
uv run pytest dimos/navigation/replanning_a_star/test_min_cost_astar.py -m self_hosted --no-cov -q
uv run pytest dimos/navigation/nav_stack/modules/local_planner/test_local_planner_rosbag.py -m self_hosted --no-cov -q
```

全量 self_hosted（慢，需数据与 runner）：

```bash
./bin/pytest-slow
```

### MuJoCo 仿真测试（`@pytest.mark.mujoco`，默认 fast 套件不运行）

`dimos/experimental/navigation_obstacle_mvp/tests/test_mujoco_obstacle_mvp.py` 在显式选择
`mujoco` marker 时运行，覆盖：

| 测试 | 内容 |
|------|------|
| `test_unitree_go2_blueprint_accepts_obstacle_mvp_sim_overrides` | `unitree-go2` 蓝图可合并 `obstacle_crossing_mvp` + `simulation=mujoco` |
| `test_mujoco_scene_includes_navigation_test_obstacles` | `--obstacle-crossing-mvp` 时 office XML 含 MVP 几何名 |
| `test_mujoco_scene_and_planner_honor_obstacle_mvp_config` | 加载场景与 Go1 模型、geom 存在；`GlobalPlanner`/`LocalPlanner` 读到 MVP 配置 |
| `test_mujoco_obstacles_visible_in_path_clearance_costmap` | MVP geom 表面点云 + `height_cost` 代价图在路径走廊上检测到障碍（无 GL/Renderer） |
| `test_unitree_go2_mujoco_navigation_obstacle_mvp_smoke` | `dimos --simulation mujoco` 启动 `unitree-go2` 并开启 MVP，等待 `/odom`（无 LLM） |

**前置条件**

- 安装仿真依赖：`uv sync --extra sim`（或 `--all-groups`）
- LFS / 数据：`mujoco_sim`、menagerie、ONNX policy（与现有 Go2 MuJoCo 一致）
- 集成 smoke 需本机可启动 MuJoCo 子进程（Linux 上通常需显示或 EGL/OSMesa；CI 中该 smoke 带 `@pytest.mark.skipif_in_ci`）
- 无 MuJoCo 时相关用例会 `pytest.skip`，不会让默认 fast 套件失败

**运行**

```bash
# 默认 fast（不含 mujoco）
uv run pytest dimos/experimental/navigation_obstacle_mvp/ -q

# 仅 MuJoCo / 仿真相关
uv run pytest dimos/experimental/navigation_obstacle_mvp/tests/test_mujoco_obstacle_mvp.py -m mujoco -v

# 只跑场景 + planner 配置（不启动完整 dimos 子进程）
uv run pytest dimos/experimental/navigation_obstacle_mvp/tests/test_mujoco_obstacle_mvp.py::test_mujoco_scene_and_planner_honor_obstacle_mvp_config -m mujoco -v
```

**预期**：前两个测试通过即说明配置与 MuJoCo 资产、规划器初始化兼容；smoke 在 180s 内收到 `/odom` 表示
`unitree-go2` + MVP 在仿真下可正常拉起。若本机缺 MuJoCo/显示/数据，对应用例会 `pytest.skip`；
缺 LCM 多播（离线机、沙箱网络）时蓝图/smoke 用例会 skip，不会让默认 fast 套件失败。

**最近一次本机 MuJoCo 验证（2026-05-21，Linux，`uv sync --extra sim`）**

| 命令 | 结果 | 耗时（约） |
|------|------|------------|
| `uv run pytest dimos/experimental/navigation_obstacle_mvp/tests/test_mujoco_obstacle_mvp.py -m mujoco -v` | **5 passed**（含障碍/代价图；代价图测试不依赖 OpenGL） | 视机器 |
| `uv run pytest dimos/experimental/navigation_obstacle_mvp/ -q` | fast 套件 **21 passed**，`mujoco` 需 `-m mujoco` | **~0.6s** |

分项（`-m mujoco -v`）：`test_mujoco_scene_includes_navigation_test_obstacles`、
`test_mujoco_obstacles_visible_in_path_clearance_costmap` 验证障碍注入与 `height_cost` 占用；
`test_unitree_go2_mujoco_navigation_obstacle_mvp_smoke` 为完整 `dimos` 子进程 smoke（CI 跳过）。

**环境限制（常见）**

| 问题 | 表现 | 处理 |
|------|------|------|
| 未装仿真 extra | `pytest.skip: MuJoCo not installed` | `uv sync --extra sim` |
| LFS / `mujoco_sim` / menagerie 缺失 | skip，消息含资产路径 | `git lfs pull`；按 Go2 MuJoCo 文档拉数据 |
| LCM 多播不可用 | 蓝图 / smoke **skip**（`sim_test_deps.require_lcm_multicast`） | Linux 离线：`sudo ifconfig lo multicast` 与 `sudo route add -net 224.0.0.0 netmask 240.0.0.0 dev lo`（见 [LCM multicast 文档](https://lcm-proj.github.io/lcm/content/multicast-setup.html)） |
| 受限网络沙箱（如 IDE 默认 sandbox） | 同上 skip；仅 `test_mujoco_scene_and_planner_*` 可 pass | 在本机终端或授予完整网络权限后重跑 `-m mujoco` |
| 无显示 / headless | `ERROR: could not create window`（MuJoCo GLFW 窗口）；`--viewer none` **不会**关闭 MuJoCo 被动 viewer | 见 FAQ「MuJoCo 窗口 vs DimOS viewer」 |
| GitHub Actions | smoke 带 `@pytest.mark.skipif_in_ci` | CI 不跑完整 dimos 子进程 smoke |
| PyTorch `CUDA Error 804`（forward compatibility） | 启动时出现 `UserWarning: cudaGetDeviceCount() ... Error 804`；通常**不阻塞** `dimos run`，各模块回退 CPU | 见下「常见告警」 |

手动仿真见上文「带障碍的 office 仿真」。

### 已知非阻塞日志（已抑制或缓解）

| 日志 | 性质 | 处理 |
|------|------|------|
| PyTorch `CUDA Error 804` / `cudaGetDeviceCount()` | `UserWarning`；进程继续，回退 CPU | CLI/worker 启动时 `configure_torch_cuda_warning_filters()`：若已设 `CUDA_VISIBLE_DEVICES` 或首次 804 后，抑制重复打印 |
| `GLFWError: GLX: Failed to create context: BadValue` | 离屏 GL 首次失败后的 cosmetic 警告 | 首次 backend 失败后 `suppress_repeat_glfw_gl_errors()` 过滤重复 |
| `Exception ignored in: Renderer.__del__` / `AttributeError: '_mjr_context'` | MuJoCo 在 `Renderer.__init__` 失败对象上析构 | `patch_mujoco_renderer_del()` 在 `mujoco_process` 启动时安装 |
| `All offscreen MuJoCo GL backends failed` | 预期（本机无 GL）；fake LiDAR 兜底 | 加 `--obstacle-crossing-mvp` 后自动启用 fake LiDAR |

### 端到端复现（fake LiDAR → costmap → goal → PathClearance → MVP 决策）

```bash
bash scripts/demo-mvp.sh
```

脚本会：后台 `--daemon` 启动 headless dimos → 等待 fake LiDAR 与 `/global_costmap` →
`dimos topic send /goal_request 'PoseStamped(position=Vector3(-0.95, 1.25, 0.0))'` 朝 MVP 低台阶方向下发 goal → 等待 MVP 决策日志 → `dimos stop` → 打印 PASS/FAIL。

**成功时应 grep 命中的关键字：**

- `FAKE OBSTACLE LIDAR enabled`
- `Published first fake obstacle lidar frame`
- `/global_costmap`（LCM 上有消息；或 `dimos log` 中 transport 已连接）
- `Got new goal`
- `Found path`（全局规划成功）
- `Obstacle detected ahead, applying MVP decision`（含 `action=cross` 当 `--obstacle-crossing-mvp-height-cm 8`）

### 常见告警（FAQ）

**MuJoCo 窗口 vs DimOS viewer**

- **`--simulation mujoco`**（或任意 simulator）：未显式传 `--viewer` 时，CLI 默认 **`viewer=rerun`**（原生 dimos-viewer），便于看代价图/路径/点云。需要浏览器时用 **`--viewer rerun-web`**（Command Center http://localhost:7779）。
- **`--viewer none`**：只关闭 DimOS 侧可视化（Rerun / Foxglove 等），**不会**关闭 MuJoCo 子进程里的被动 viewer（`viewer.launch_passive` / GLFW 窗口）。
- **`--mujoco-viewer` / `--no-mujoco-viewer`**：显式开关 MuJoCo 被动 viewer（默认 **开**）。`--mujoco-headless` 与 `--no-mujoco-viewer` 等价。
- **`--mujoco-offscreen-gl {auto,egl,osmesa,glfw,off}`**（默认 **`auto`**）：仅控制离屏 `mujoco.Renderer`（RGB 相机 + 深度 LiDAR），与 passive viewer **独立**（viewer 优先打开，离屏在独立 GL 后端创建）。`auto` 依次尝试 `egl` → `osmesa` → `glfw`；全部失败则禁用离屏输出并启用 fake LiDAR（MVP 开启时），**仿真与 odom 仍继续**。`off` 完全跳过离屏渲染。
- **`ERROR: could not create window`**：MuJoCo 试图用 GLFW 打开交互窗口，但本机无可用显示或 OpenGL 上下文（SSH 无 X11、Wayland 权限等）。与 PyTorch CUDA 804 无关。修复后子进程 stderr 会继承到终端，可看到 `Opening MuJoCo passive viewer` 或 `Failed to open MuJoCo passive viewer` 日志。
- **`GLX: Failed to create context: BadValue` / `gladLoadGL error`**：多见于 **NVIDIA 驱动与 CUDA/PyTorch runtime 不匹配**（常与 CUDA Error 804 同机），或 X server 不支持请求的 OpenGL profile。被动 viewer 走 GLX/GLFW；离屏 `mujoco.Renderer` 在 viewer 打开后于独立 EGL/OSMesa 上下文创建，**不再因离屏失败而跳过 viewer**。
  - **GLX preflight（代码层）**：打开 viewer 前先子进程探测 GLFW/GLX（`viewer_probe`）；失败则 10s 内降级 headless，不再无限挂起；成功路径用 `os._exit(0)` 跳过部分 NVIDIA 栈 teardown SIGSEGV。
  - 弹窗优先：不要 `--no-mujoco-viewer`；推荐 `GDK_BACKEND=x11 MUJOCO_GL=glfw` + `--mujoco-offscreen-gl off`；
  - 诊断 OpenGL：`glxinfo | grep "OpenGL version"`（需 `mesa-utils`）；EGL：`eglinfo -B`（需 `mesa-utils-extra`）；
  - 仍失败：`LIBGL_ALWAYS_SOFTWARE=1` + Mesa、`--mujoco-offscreen-gl osmesa`，或 `--no-mujoco-viewer` headless + fake LiDAR。
- **诊断**（打印 DISPLAY / WAYLAND / OpenGL 环境变量；加 `--shell` 跑 glxinfo / eglinfo / ldconfig / nvidia-smi）：

```bash
uv run python -m dimos.simulation.mujoco.diagnose
uv run python -m dimos.simulation.mujoco.diagnose --shell
```

#### OpenGL 完全不可用

当 **EGL / OSMesa / GLFW 三种离屏后端 + passive viewer 全部失败**（日志含 `gladLoadGL error`、`GLX: BadValue`、`ERROR: could not create window`），根因通常是 **本机 NVIDIA 驱动 / OpenGL stack 当前不可用**（常与 PyTorch `CUDA Error 804` 同机：驱动与 CUDA runtime forward compat 不匹配）。DimOS 代码层多后端尝试无法修复系统层 GL；请按下面三档选择启动方式。

**1. 首选：OSMesa 软件渲染**（需系统包 + 环境变量；DimOS 在 `osmesa` 后端尝试时会自动设置 `LIBGL_ALWAYS_SOFTWARE=1` 等，但系统仍需安装 OSMesa）

```bash
sudo apt install libosmesa6 mesa-utils mesa-utils-extra libgl1-mesa-glx

LIBGL_ALWAYS_SOFTWARE=1 MUJOCO_GL=osmesa uv run dimos --simulation mujoco \
  --no-mujoco-viewer --mujoco-offscreen-gl osmesa --viewer none \
  --obstacle-crossing-mvp --obstacle-crossing-mvp-height-cm 8 run unitree-go2
```

**2. 完全无 GL 路径**（只要物理仿真 + odom；无相机 RGB、无 LiDAR 点云；`VoxelGridMapper` 无 `/lidar` 输入）

```bash
uv run dimos --simulation mujoco --no-mujoco-viewer --mujoco-offscreen-gl off \
  --viewer none --obstacle-crossing-mvp run unitree-go2
```

**2b. Fake LiDAR 兜底（GL 全失败 / `--mujoco-offscreen-gl off` + MVP 开启）**

当离屏 GL 不可用且 `--obstacle-crossing-mvp`（或 `--mujoco-navigation-test-obstacles`）开启时，
MuJoCo 子进程会自动从 MVP 障碍 geom 采样合成点云写入 `/lidar`（日志含 `FAKE OBSTACLE LIDAR enabled`）。
无需真实 OpenGL，代价图与 `PathClearance.is_obstacle_ahead()` 可继续工作。

```bash
# 推荐：headless + 显式关闭离屏 GL（最快验证 fake 路径）
CUDA_VISIBLE_DEVICES="" uv run dimos --simulation mujoco \
  --no-mujoco-viewer --mujoco-offscreen-gl off --viewer none \
  --obstacle-crossing-mvp --obstacle-crossing-mvp-height-cm 8 run unitree-go2

# 或保持 auto（EGL/OSMesa/GLFW 全失败后自动启用 fake）
CUDA_VISIBLE_DEVICES="" uv run dimos --simulation mujoco \
  --no-mujoco-viewer --mujoco-offscreen-gl auto --viewer none \
  --obstacle-crossing-mvp --obstacle-crossing-mvp-height-cm 8 run unitree-go2
```

独立 LCM 发布器（不跑 MuJoCo，仅注入 `/lidar`）：

```bash
uv run python -m dimos.experimental.navigation_obstacle_mvp.fake_obstacle_lidar
```

一键 smoke（端到端 MVP 决策，约 2–3 分钟）：

```bash
bash scripts/demo-mvp.sh
# 等价：uv run python -m dimos.experimental.navigation_obstacle_mvp.tests.test_demo_smoke
```

仅验证 fake LiDAR 帧（60s 内 grep，不发送 goal）：

```bash
uv run pytest dimos/experimental/navigation_obstacle_mvp/tests/test_demo_smoke.py::test_demo_smoke_fake_lidar_headless_subprocess -m mujoco -v
```

**3. 修复 NVIDIA 驱动**（需本机管理员；DimOS 不改系统）

- `CUDA Error 804` + `GLX BadValue` 一般表示 `nvidia-driver` 版本与 PyTorch/CUDA runtime **不匹配**（过旧或过新）。
- 若 `nvidia-smi` 报 **`Driver/library version mismatch`** 或 **`Failed to initialize NVML`**：内核模块与用户态库版本不一致（常见于驱动升级后未重启）。**必须先 `sudo reboot`**（勿用 `modprobe -r nvidia*` 卸载模块，易黑屏/会话崩溃），重启后再试「成功弹窗命令」；代码层无法绕过。可选：`bash scripts/fix-nvidia-driver.sh` 打印提示。
- 建议：`nvidia-smi` 正常后核对驱动版本 → 必要时重装与 CUDA 12.x wheel 匹配的 `nvidia-driver-XXX`。
- 驱动正常后，默认可恢复 `--mujoco-offscreen-gl auto`（EGL 优先）+ 可选 passive viewer。
- 验证弹窗日志应含：`MuJoCo passive viewer opened successfully`（`dimos log` 或终端 stderr）。

**自动兜底**：若 `mujoco_offscreen_gl` 非 `off` 且所有离屏后端失败，MuJoCo 子进程会 **继续尝试 passive viewer**（viewer 与离屏 GL 隔离）；MVP 开启时自动启用 fake LiDAR。仅当 viewer 也失败时才 headless 运行。

- **推荐 headless 启动**（无 MuJoCo 窗口，物理 + 离屏相机仍运行）：

```bash
# 显式关闭 MuJoCo viewer
uv run dimos --simulation mujoco --no-mujoco-viewer --viewer none \
  --obstacle-crossing-mvp --obstacle-crossing-mvp-height-cm 8 run unitree-go2

# 等价别名
uv run dimos --simulation mujoco --mujoco-headless --viewer none \
  --obstacle-crossing-mvp --obstacle-crossing-mvp-height-cm 8 run unitree-go2

# 或环境变量（离屏渲染需 EGL/OSMesa；会关闭 passive viewer）
MUJOCO_GL=egl uv run dimos --simulation mujoco --viewer none run unitree-go2

# 弹窗 + 离屏 EGL（viewer 仍用 DISPLAY/GLX，相机走 EGL）
uv run dimos --simulation mujoco --mujoco-offscreen-gl egl --viewer none run unitree-go2

# 无 DISPLAY / WAYLAND_DISPLAY 的 Linux 会自动 headless，无需额外 flag
CUDA_VISIBLE_DEVICES="" uv run dimos --simulation mujoco --no-mujoco-viewer --viewer none run unitree-go2
```

- **`MUJOCO_HEADLESS=1`** 与 CLI `--mujoco-headless` 等价（GlobalConfig 字段 `mujoco_headless`）。

**PyTorch：`CUDA initialization ... Error 804: forward compatibility was attempted on non supported HW`**

- **性质**：`UserWarning`，不是异常；`torch.cuda.is_available()` 返回 `False` 后进程继续。
- **根因**：本机 NVIDIA 驱动版本低于 PyTorch/ONNX 所带的 CUDA runtime（或容器启用了 forward compat 但 GPU/驱动不支持）。属系统层不匹配，不是 DimOS 业务逻辑错误。
- **对 `unitree-go2` + `--simulation mujoco` 的影响**：导航 MVP 栈（`VoxelGridMapper`、规划器、MuJoCo policy）可正常拉起；体素图会记 `VoxelGrid using device: CPU:0`。若出现 `ERROR: could not create window`，加 `--mujoco-headless` 或 `MUJOCO_GL=egl`（见上）。
- **抑制/缓解（任选，示例勿当作必须改系统）**：

```bash
# 推荐：启动前隐藏 GPU，避免 PyTorch 探测 CUDA（导航仿真通常够用）
CUDA_VISIBLE_DEVICES="" uv run dimos --simulation mujoco --viewer none run unitree-go2

# 或：用 NVML 做可用性检查，有时可减少 804 告警（仍可能无 CUDA 加速）
PYTORCH_NVML_BASED_CUDA_CHECK=1 uv run dimos --simulation mujoco run unitree-go2
```

- **系统级（需本机管理员操作）**：升级 NVIDIA 驱动至与 CUDA 12.x wheel 匹配；或改装 CPU-only 的 `torch`；对齐 `nvidia-smi` 与 `torch.version.cuda`。
- **agentic 蓝图额外说明**：YOLO 经 `dimos.utils.gpu_utils.is_cuda_available()`（pycuda）选设备；CLIP ONNX 会尝试 `CUDAExecutionProvider` 后回退 `CPUExecutionProvider`——804 下多为 CPU，仅变慢。

**高度参数建议**：MuJoCo 走廊内优先用 pose 推断（上表物理高度）；CLI 回退：`cross` 用
`--obstacle-crossing-mvp-height-cm 8`（低于 10 cm 阈值）；`detour` 用 `20`；`stop` 用 `58`（高于 50 cm
决策上限）。未知高度会保守 `detour`。

### 规划慢 / 仿真性能

MuJoCo MVP 栈里常见瓶颈：**global_map 点云过密 → CostMapper 每帧算 height-cost 栅格 → CPU 占满**；其次是 **initial_rotation 久**（MockController 全局角速度限幅会拖慢转向）与 **MVP creep 过慢触发 stuck 重规划**（日志里 `Robot is stuck. Replanning.` 约每 8–20 s 一次）。

默认已在 **simulation 模式**自动做轻量优化（无需额外 flag），在性能与规划新鲜度之间折中：

| 组件 | 仿真默认 | 作用 |
|------|----------|------|
| `VoxelGridMapper` | `emit_every=2`，`voxel_size≥0.08` | 降低 global_map 发布频率与点数 |
| `CostMapper` | 栅格 `resolution≥0.08`，更新节流 ~6.7 Hz（0.15 s） | 减轻 A* 输入 costmap 构建 |
| `LocalPlanner` | 控制 15 Hz，朝向容差 0.45 rad，仿真角速度下限 1.2 rad/s | 缩短原地转向时间 |
| `GlobalPlanner` | stuck 窗口 15 s（MVP 20 s），位移阈值 1.0–1.5 m | 减少 creep 穿越时的误重规划 |
| MVP creep | 仅 `path_following` 且距低槛 **≤0.15 m**、高度 ≤10 cm | 近槛短暂降速，其余路段全速 `_speed=0.55` |
| `MockController` | 默认不限幅；距低槛 **<0.2 m** 时限幅 | 避免全局拖慢 initial_rotation / path_following |

**推荐启动（性能优先，仍可 Rerun）**：

```bash
CUDA_VISIBLE_DEVICES="" uv run dimos --simulation mujoco --no-mujoco-viewer --viewer rerun \
  --obstacle-crossing-mvp --obstacle-crossing-mvp-height-cm 8 run unitree-go2-agentic
```

- 不需要 Rerun 时用 `--viewer none` 可再省 CPU/GPU。
- 日志中 `Found path` 应在点目标后 **<100 ms** 出现；`initial_rotation` 应在 **<20 s** 内完成（`Publishing nav_cmd_vel angular_z=±1.2`）；`path_following` 线速度应接近 **0.5 m/s**（`_speed=0.55`）。
- `Robot is stuck. Replanning.` 在 MVP 穿越低槛时应 **明显少于** 优化前（原 ~8 s 周期）。

### Lint

```bash
uv run ruff check \
  dimos/experimental/navigation_obstacle_mvp \
  dimos/simulation/mujoco/model.py \
  dimos/navigation/replanning_a_star/local_planner.py \
  dimos/navigation/replanning_a_star/global_planner.py \
  dimos/core/global_config.py
uv run ruff format --check \
  dimos/experimental/navigation_obstacle_mvp \
  dimos/simulation/mujoco/model.py \
  dimos/navigation/replanning_a_star/local_planner.py \
  dimos/navigation/replanning_a_star/global_planner.py \
  dimos/core/global_config.py
```

全仓快速验证（环境允许时）：

```bash
bash scripts/verify.sh
```

## 真机 / 仿真注意事项

- 默认关闭，真机只应在短距、低速、有人监护和可急停条件下开启。
- `cross` 目前只表示“不立即触发重规划/硬停”，不会静默提高 `Twist` 速度，也不调用额外抬腿 API。
- 未知高度保守处理为 `detour`，不得默认跨越。
- 遇到高障碍 `stop` 时会取消目标，不进入重规划循环。
