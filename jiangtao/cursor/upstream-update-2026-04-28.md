# Upstream `dev` 分支更新汇总（2026-04-28）

> 来源：`upstream` (`https://github.com/dimensionalOS/dimos.git`) `dev` 分支
> 更新前 HEAD：`0611ab41f` (`ci(macos): add macos_bug marker and skip known-crashing worker tests, #1786`)
> 更新后 HEAD：`d4b9ce094` (`feat(go2): go2 SDK adapter + nix cyclonedds setup, #1885`)
> 共 **15 个新 commit**，**246 个文件**变更，约 **+11,922 / -1,542** 行代码
> 时间区间：2026-04-17 ~ 2026-04-27

本地操作记录：
- `git fetch upstream dev:dev`，本地 `dev` 已快进到 `d4b9ce094`，无任何冲突
- 当前所在的工作分支 `feat/jiangtao` 未做任何变动
- 本次更新尚未推送到 `origin (gitlab.topsun)`，如需同步私服请执行 `git push origin dev`

---

## 一、本次更新一览（按提交时间倒序）

| # | SHA | 标题 | 类型 | 影响面 |
|---|-----|------|------|--------|
| 1 | `d4b9ce094` | feat(go2): go2 SDK adapter + nix cyclonedds setup (#1885) | **重大功能** | Go2 控制 |
| 2 | `d1af9c8ee` | fix(types): resolve mypy 3.10 errors (#1921) | 类型修复 | 全局 |
| 3 | `50b3f7c45` | Jeff/fix/rconnect2 (#1784) | **重大重构** | 可视化、导航、CLI |
| 4 | `beec592b9` | fix attributes (#1918) | 小修 | git LFS |
| 5 | `1d0d507a8` | Feat/memory2 — plotting, examples, recorder, semantic search (#1769) | **重大功能** | Memory 子系统 |
| 6 | `a1b5cc850` | feat: rust native modules (#1794) | **重大功能** | 跨语言模块 |
| 7 | `b6b94461a` | feat(go2): rage mode via webrtc (#1903) | 新功能 | Go2 WebRTC |
| 8 | `1e9208e99` | fix(tests): fix flakey porcelain test (#1900) | 测试修复 | porcelain |
| 9 | `8a3c790b9` | Remove sim from base install (#1878) | 依赖瘦身 | 安装 |
| 10 | `0b55e8d12` | Fix mac UDP (#1789) | 平台修复 | macOS |
| 11 | `2eb12b8b8` | feat: never leave child processes alive after the parent (#1886) | **重大功能** | 进程生命周期 |
| 12 | `a0eb4a950` | Task: Add meaningful manipulation tests (#1765) | 测试增强 | manipulation |
| 13 | `6aaa284f9` | fix(rerun): dimos-viewer version upgrade (#1785) | 依赖升级 | 可视化 |
| 14 | `c645d0dbb` | feat(api): add porcelain api with connect (#1779) | **重大功能** | Python API |
| 15 | `5532e8b60` | perf(rerun): render voxel maps as Points3D spheres (#1793) | 性能优化 | rerun |

---

## 二、重大功能详解（值得重点关注）

### 1. Porcelain Python API（#1779, `c645d0dbb`）

> 全新顶层 `dimos.Dimos` 类 — 首个面向脚本/Notebook 用户的"用一行 import 跑起来"的 Python API。

**核心改动**

- 新增包：`dimos/porcelain/`，包含 `dimos.py`、`local_module_source.py`、`remote_module_source.py`、`module_source.py`、`skills_proxy.py` 共 5 个新模块
- 新增 `dimos/__init__.py`：通过 `__getattr__` 懒加载 `Dimos` 类，避免污染全局命名空间
- 新增 RPyC 守护进程基础设施：`dimos/core/coordination/rpyc_server.py`、`rpyc_services.py`
- 新增 `RunEntry.rpyc_port` 字段，把 daemon 的 RPyC 端口持久化到 `~/.local/state/dimos/runs/<run-id>.json`
- 依赖：`pyproject.toml` 新增 `rpyc>=6.0.0`
- 文档：新增 `docs/usage/python-api.md`，详细描述 Local / Remote 两种用法

**用法示例（Local 模式）**

```python
from dimos import Dimos

app = Dimos(n_workers=8)
app.run("unitree-go2-agentic")          # 通过 registry name 启动 blueprint
app.skills.relative_move(forward=2.0)    # 直接调用 skill
print(app.skills)                        # 列出所有 skill
app.ReplanningAStarPlanner               # 直接拿到模块对象
app.stop()                                # 全部停掉
```

**用法示例（Remote 模式 — 连接到正在运行的 daemon）**

```bash
dimos run unitree-go2-agentic   # 在另一个终端启动 daemon
```

```python
from dimos import Dimos

app = Dimos.connect()                                      # 自动连最新 daemon
app = Dimos.connect(run_id="20260428-...-unitree-go2")     # 按 run-id 选择
app = Dimos.connect(host="192.168.1.50", port=18861)       # 远程主机直连

app.skills.relative_move(forward=2.0)
app.run("keyboard-teleop")               # 在 daemon 上动态加载新模块
app.restart(SomeModule)                   # 热重启某个模块（含源码 reload）
app.stop()                                # 仅断开 RPyC，不杀进程
```

**机制关键点**

- 字符串和已注册 Module class 走"按名 fast path"
- 任意 Module class 或 Blueprint 对象走 pickle → daemon 端反序列化（要求两端可互相 import）
- daemon 通过 `coordinator.start_rpyc_service()` 在 `localhost` 上监听一个随机端口
- `dimos.core.run_registry.get_most_recent_rpyc_port()` 让 client 找到最新的 daemon

> 这条变更彻底改变了"如何把 dimos 当成一个 Python 库使用"的体验，建议优先关注。

---

### 2. Memory2 大升级：plotting + recorder + 语义检索（#1769, `1d0d507a8`）

> 这是单次 commit 中**变更量最大**的一次：123 个文件、+5,262 / -960 行。Memory2 系统从"基础存储"跃迁到"开箱即用的可视化分析平台"。

**主要新增**

- **可视化子模块** `dimos/memory2/vis/`：
  - `vis/color.py`：色彩工具 + colormap
  - `vis/plot/`：1D 曲线绘图（`elements.py`、`plot.py`、`svg.py`、`rerun.py`）
  - `vis/space/`：3D 空间绘图（`elements.py`、`space.py`、`svg.py`、`rerun.py`）
  - 既能输出 SVG 文件，也能直接推到 rerun
- **`StreamModule` 基类**（`dimos/memory2/module.py`）：
  把 push 式的 `In/Out` 端口和 pull 式的 memory2 流水线桥接起来。任何 Module 只要继承它就自带 stream 能力：

  ```python
  class VoxelGridMapper(StreamModule[PointCloud2, PointCloud2]):
      pipeline = Stream().transform(VoxelMapTransformer())
      lidar: In[PointCloud2]
      global_map: Out[PointCloud2]
  ```
- 新增 3 个可注册模块：
  - `memory-module` — `dimos.memory2.module.MemoryModule`
  - `recorder` — `dimos.memory2.module.Recorder`
  - `semantic-search` — `dimos.memory2.module.SemanticSearch`
- **新 blueprint `unitree-go2-memory`**：把 Memory + Recorder 接入 Go2 stack
- **替换旧 `TimedSensorReplay`**：`dimos/utils/testing/replay.py` 改为基于 memory2 的 `SqliteStore` shim
- **CLIPModel 集成**：直接用文本搜索图片（`store.streams.color_image_embedded.search(clip.embed_text("shop"))`）

**新增大量文档与可视化资产**

- `docs/capabilities/memory/index.md` — 端到端 walkthrough（从录制到检索）
- `docs/capabilities/memory/plot.md` — 1D plot 全教程
- `docs/capabilities/memory/algo_comparison.md` — peak detection 算法对比
- `docs/capabilities/memory/demo_rerun.py` — 配套示例
- `docs/capabilities/memory/assets/` — 大量 SVG/PNG 渲染输出（亮度图、植物分布、颜色场、嵌入向量空间等）

**额外细节**

- `dimos/memory2/transform.py` 新增 +267 行：增加 `QualityWindow`、`brightness`、`speed`、`smooth`、`throttle`、`downsample` 等可链式调用的转换器
- 删除了无用的 `dimos/core/test_modules.py`（-331 行）
- `dimos/memory/timeseries/base.py`、`legacy.py` 大幅精简（-78、-87）

---

### 3. Go2 SDK 适配器（DDS 通道）+ nix CycloneDDS 安装（#1885, `d4b9ce094`）

> 这是本次更新里**最新**的 commit，也是 Go2 用户最关心的：终于有了不依赖 WebRTC 的高级控制通道。

**核心新增**

- 新增 `dimos/hardware/drive_trains/unitree_go2/adapter.py`（691 行）：
  `UnitreeGo2TwistAdapter` 实现 `TwistBaseAdapter`（vx, vy, wz 三 DOF），底层走 `unitree_sdk2py` 的 DDS 通道，链路为：

  ```
  ChannelFactoryInitialize → MotionSwitcher → SportClient → StandUp → FreeWalk
  ```

  自动注册为 `"unitree_go2"`，开箱即用。
- **Rage Mode（DDS 路径）**：`rage_mode=True` 时通过往 `rt/wirelesscontroller_unprocessed` 发合成的 `WirelessController_` 消息触发 Rage（约 2.5 m/s 前进包络）
- 新增 blueprint `unitree-go2-keyboard-teleop`（不带 webrtc 后缀的版本，走纯 DDS）：

  ```bash
  export ROBOT_IP=192.168.123.161
  dimos run unitree-go2-keyboard-teleop
  ```

  键位与原 webrtc teleop 完全一致（W/A/S/D + Q/E + Shift/Ctrl + Space + Esc）。
- **`pyproject.toml` 新增 `unitree-dds` extra**：

  ```toml
  unitree-dds = [
      "dimos[unitree]",
      "unitree-sdk2py-dimos>=1.0.2",
      "cyclonedds>=0.10.5",
  ]
  ```
- **`flake.nix` 新增 cyclonedds**，并附带详细的安装说明：
  ```bash
  nix build nixpkgs#cyclonedds
  export CYCLONEDDS_HOME=$(readlink -f ./result)
  export LD_LIBRARY_PATH="$CYCLONEDDS_HOME/lib:$LD_LIBRARY_PATH"
  uv pip install -e ".[unitree-dds]"
  ```
- 文档：`dimos/hardware/drive_trains/unitree_go2/README.md`、`docs/usage/transports/dds.md` 同步更新（包括 Ubuntu apt 的 fallback 方案）

---

### 4. Rust Native Modules（#1794, `a1b5cc850`）

> 首次支持用 Rust 写 dimos Module — 跨语言流水线终于不再是 nice-to-have。

**新增内容**

- `native/rust/` — Rust SDK：
  - `Cargo.toml` 命名为 `dimos-native-module`，依赖 `dimos-lcm`、`tokio`、`serde`
  - `src/lib.rs`、`src/module.rs`（416 行核心 SDK）、`src/lcm.rs`、`src/transport.rs`
  - 暴露 `NativeModule`、`NativeModuleHandle`、`Input`、`Output`、`LcmTransport`、`LcmOptions`
- `examples/native-modules/rust/` — 完整可运行示例：
  - `src/native_ping.rs`（Ping 模块，5Hz 发送 Twist）
  - `src/native_pong.rs`（Pong 模块，回声 Twist）
  - `Cargo.toml`、`Cargo.lock`
- `examples/native-modules/rust_ping_pong.py` — Python 侧的 blueprint 用 `NativeModule` 启动并自动 `cargo build --release`
- `dimos/core/native_module.py` 微调以兼容新 SDK

**Rust 端典型用法（来自示例 README）**

```python
class PingConfig(NativeModuleConfig):
    executable = str(_EXAMPLES / "native_ping")
    build_command = "cargo build --release"
    cwd = str(_RUST_DIR)
    stdin_config = True

class PingModule(NativeModule):
    config: PingConfig
    data: Out[Twist]
    confirm: In[Twist]
```

启动命令：`python examples/native-modules/rust_ping_pong.py`

---

### 5. 进程生命周期管理 — "永不留下孤儿进程"（#1886, `2eb12b8b8`）

> 修复了 `dimos run` daemon 模式下 SIGKILL 主进程会留下 worker 孤儿的痛点。

**新增**

- `dimos/core/coordination/process_lifecycle.py`（130 行）：
  - `DIMOS_RUN_ID_ENV = "DIMOS_RUN_ID"` — 所有子孙进程都继承这个环境变量
  - `kill_run_processes(run_id)` 用 `psutil` 扫全系统，按环境变量精确定位并 SIGTERM → SIGKILL
  - `spawn_watchdog(run_id, log_dir)` 启动一个 sidecar 进程，主进程一旦死亡（含 `SIGKILL`）watchdog 立刻清理所有子孙
- `dimos/core/coordination/watchdog_main.py`（54 行）：sidecar 主入口
- `dimos/core/coordination/test_process_lifecycle.py`（188 行）：测试覆盖
- `dimos/core/daemon.py`、`dimos/core/run_registry.py`、`dimos/robot/cli/dimos.py`：
  - daemon 启动时自动 `os.environ[DIMOS_RUN_ID_ENV] = run_id`
  - daemon 模式下自动 `spawn_watchdog(run_id)`
  - `dimos stop --force` 现在能彻底清理孤儿

> 实际效果：以后再也不会出现"`dimos stop` 之后 `ps aux | grep dimos` 还有一堆 worker"。

---

### 6. Go2 Rage Mode (WebRTC 路径)（#1903, `b6b94461a`）

> 注意区分：**`#1885` 是 DDS 路径的 Rage Mode；本条是 WebRTC 路径的 Rage Mode**。

**改动**

- `dimos/robot/unitree/connection.py`、`dimos/robot/unitree/go2/connection.py` 新增 `mode="rage"` 参数与 `FsmRageMode` 切换逻辑
- `dimos/robot/unitree/keyboard_teleop.py` 支持自定义 `linear_speed` / `angular_speed`（rage 模式使用 1.25 m/s, 1.2 rad/s）
- 新增 blueprint：`unitree-go2-webrtc-rage-keyboard-teleop`

  ```python
  unitree_go2_webrtc_rage_keyboard_teleop = autoconnect(
      unitree_go2_webrtc_keyboard_teleop,
      GO2Connection.blueprint(mode="rage"),
      KeyboardTeleop.blueprint(linear_speed=1.25, angular_speed=1.2),
  ).global_config(obstacle_avoidance=True)
  ```

- `dimos/robot/unitree/mujoco_connection.py` 也加了对 `mode` 参数的兼容（sim 端忽略）

启动命令：`dimos run unitree-go2-webrtc-rage-keyboard-teleop`

---

### 7. rconnect2 — 大量可视化 / CLI / 模块改进（#1784, `50b3f7c45`）

> 杂烩型的"再连接 v2"PR，横跨 43 个文件、+1,465 / -292 行。值得逐项关注。

**最重要的产物**

- **新模块 `MovementManager`**（`dimos/navigation/smart_nav/modules/movement_manager/`）：
  - 把 `tele_cmd_vel`（键盘控制）和 `nav_cmd_vel`（导航器输出）合在一起，输出统一的 `cmd_vel`
  - 处理 `clicked_point` → `goal/way_point` 转发
  - 内置 teleop 冷却时间（`tele_cooldown_sec`）和 teleop 缩放（`tele_cmd_vel_scaling`）
  - 注册名 `movement-manager`，配套测试 117 行
- **WebSocket 可视化栈**：
  - 新增 `dimos/visualization/rerun/websocket_server.py`（244 行）+ `RerunWebSocketServer` 模块（注册名 `rerun-web-socket-server`）
  - 新增 `dimos/visualization/rerun/constants.py`、`conftest.py`、`test_websocket_server.py`、`test_viewer_ws_e2e.py`
  - 新增 **`dimos/visualization/vis_module.py`** —— 统一入口工厂 `vis_module(viewer_backend, rerun_config, foxglove_config)`，根据 `global_config.viewer` 自动选择 rerun/foxglove/none
- **CLI 大改**（`dimos/robot/cli/dimos.py`）：
  - 引入 `--rerun-open {native|web|both|none}` 与 `--rerun-web` 两个独立旋钮
  - `daemon` 模式启动 RPyC 服务（配合 Porcelain Remote 模式使用）
  - 启动时自动 spawn watchdog（配合进程生命周期管理）
  - `get_by_name_or_exit` / `get_module_by_name_or_exit` — 错误时直接 exit 而不是抛异常
- **可视化 backend 重命名**：原 `rerun-web` 模式被废弃，统一为 `rerun + --rerun-open web`
- **文档新增**：`docs/development/conventions.md`（项目编码约定）

**Blueprint 全面切换到 `vis_module`**

`unitree-go2-basic`、`unitree-go2-fleet`、`unitree-go2-webrtc-keyboard-teleop`、`unitree-go2-security`、`unitree-go2-spatial`、`drone-basic`、`unitree-g1-shm`、`uintree-g1-primitive-no-nav`、`xarm-perception`、`unity-sim`、`teleop-quest` 等都从直接引用 `RerunBridgeModule` 改为 `vis_module(viewer_backend=global_config.viewer, ...)`。

> **迁移提示**：如果你自己的 blueprint 直接 import 了 `RerunBridgeModule`，建议改为 `vis_module`。详见 `docs/development/conventions.md`。

---

## 三、修复与维护类（按重要程度）

### 8. mypy 3.10 类型修复（#1921, `d1af9c8ee`）

- 30 多个文件统一补上类型注解 / 修复 mypy strict 报错
- 主要集中在 `dimos/models/`（vl、embedding、qwen、segmentation）、`dimos/perception/detection/` 和 `dimos/agents_deprecated/`
- 删除 `.pre-commit-config.yaml` 里的一行（与新 mypy 版本不兼容）

### 9. macOS UDP 多播修复（#1789, `0b55e8d12`）

- `dimos/protocol/service/system_configurator/lcm.py` 新增 macOS 专属逻辑：
  - 用 `sysctl` 调高 UDP 缓冲区
  - 配置 `route add 224.0.0.0/4 -interface lo0`
- 把 sysctl 修改持久化到 `STATE_DIR/sysctl.json`，便于回滚
- 新增 74 行测试

### 10. dimos-viewer 升级（#1785, `6aaa284f9`）

仅 `uv.lock` 变更：`dimos-viewer 0.30.0a2 → 0.30.0a6.dev99`。配合 `pyproject.toml` 中固定到 `==0.30.0a6.dev99`。

### 11. flakey porcelain test 修复（#1900, `1e9208e99`）

`dimos/porcelain/remote_module_source.py` 中 +18 行：在 RPyC 连接失败时增加重试 + 等待时间，配合 `c645d0dbb` 落地后跑 CI 的稳定性。

### 12. .gitattributes 修复（#1918, `beec592b9`）

仅同步 docs/capabilities/memory/assets 下 LFS 模式，3 行调整。

### 13. perf(rerun): voxel maps 改为 Points3D 球（#1793, `5532e8b60`）

- `dimos/msgs/sensor_msgs/PointCloud2.py` 在 `to_rerun()` 时返回 `Points3D` (球形点) 而不是 `Boxes3D`，渲染速度大幅提升
- 5 个 blueprint 删除 `rrb` 中冗余的 voxel 配置（共 -28 行）

### 14. 把 sim 移出基础安装（#1878, `8a3c790b9`）

`pyproject.toml`：`base` 从 `dimos[agents,web,perception,visualization,sim]` 改为 `dimos[agents,web,perception,visualization]`。`sim` 现在是按需 extra（`uv sync --extra sim`），减小默认安装体积。

> **影响**：如果你之前 `uv sync --all-extras --no-extra dds`，行为不变；如果你只装了 `base`，现在跑 mujoco 仿真前需要追加 `--extra sim`。

### 15. manipulation 测试增强（#1765, `a0eb4a950`）

- `dimos/manipulation/pick_and_place_module.py` 修复 `object_id` 前缀匹配的歧义：多个匹配时返回 `None` 并 warn（之前会随机返回第一个）
- 新增两个测试文件：
  - `dimos/manipulation/test_manipulation_unit.py`（124 行）
  - `dimos/manipulation/test_pick_and_place_unit.py`（159 行）
- 删除老的 `dimos/robot/test_robot_config.py`（-91 行）

---

## 四、新增 / 改名的可注册名（registry）

### 新 Blueprint

| 名称 | 说明 |
|------|------|
| `unitree-go2-keyboard-teleop` | DDS 路径键盘遥控（要 `unitree-dds` extra） |
| `unitree-go2-webrtc-rage-keyboard-teleop` | WebRTC + Rage Mode 键盘遥控 |
| `unitree-go2-memory` | Go2 + Memory + Recorder 全栈 |

### 新 Module

| 名称 | 类 |
|------|----|
| `go2-memory` | `dimos.robot.unitree.go2.blueprints.smart.unitree_go2.Go2Memory` |
| `memory-module` | `dimos.memory2.module.MemoryModule` |
| `recorder` | `dimos.memory2.module.Recorder` |
| `semantic-search` | `dimos.memory2.module.SemanticSearch` |
| `movement-manager` | `dimos.navigation.smart_nav.modules.movement_manager.movement_manager.MovementManager` |
| `rerun-web-socket-server` | `dimos.visualization.rerun.websocket_server.RerunWebSocketServer` |

---

## 五、依赖变化

`pyproject.toml`：
- **新增运行时依赖**：`rpyc>=6.0.0`（Porcelain Remote 必备）
- **dimos-viewer 锁定到 `==0.30.0a6.dev99`**
- **新 extra**：`unitree-dds = [dimos[unitree], unitree-sdk2py-dimos>=1.0.2, cyclonedds>=0.10.5]`
- `dds` extra 不再依赖 `dimos[dev]`
- `base` extra 不再隐式包含 `sim`
- `[tool.mypy]` 新增 `rpyc.*`、`unitree_sdk2py.*` 到 `ignore_missing_imports`
- `dev` 中 `md-babel-py 1.1.1 → 1.1.3`

`flake.nix`：新增 `pkgs.cyclonedds`，并设置 `CYCLONEDDS_HOME` / `CMAKE_PREFIX_PATH`。

---

## 六、对你（本仓库）可能的影响

1. **本地是否要重装依赖**：是。新 commit 引入 `rpyc`、`dimos-viewer` 升级、`unitree-dds` extra、`base` 不含 `sim` 等多项依赖变更，建议在 dev 分支上执行：

   ```bash
   uv sync --all-extras --no-extra dds
   ```

   如果之前用 `unitree-dds`，需要按 `dimos/hardware/drive_trains/unitree_go2/README.md` 的步骤先安装 cyclonedds 库。

2. **CLI 旋钮变化**：
   - `--viewer rerun-web` 已被弃用 → 用 `--viewer rerun --rerun-open web` 或 `--rerun-web`
   - 新增 `--rerun-open {native|web|both|none}`
3. **自定义 blueprint 的可视化部分**：建议从 `RerunBridgeModule` 改为 `vis_module(viewer_backend=global_config.viewer, ...)`（详见 `docs/development/conventions.md`）

4. **`feat/jiangtao` 分支建议合并 dev**：当前 `feat/jiangtao` 已与 dev 落差 15 个 commit，建议尽快 rebase 或 merge，避免后续冲突累积。可在 review 完毕后执行：

   ```bash
   git checkout feat/jiangtao
   git merge dev          # 或 git rebase dev
   ```

5. **`origin (gitlab.topsun)` 同步**：本次只更新了本地 dev，未推送。如需同步到内网 gitlab：

   ```bash
   git push origin dev
   ```

---

## 七、快速验证命令

```bash
# 1. 列出新 blueprint 是否可见
dimos list | grep -E "rage|memory|keyboard-teleop"

# 2. 试跑 Porcelain API（local）
python -c "from dimos import Dimos; print(Dimos)"

# 3. 试跑 Rust ping-pong（要求安装了 cargo / rustc）
python examples/native-modules/rust_ping_pong.py

# 4. 检查进程清理是否生效
dimos --replay run unitree-go2 --daemon
dimos stop --force
ps aux | grep dimos   # 应该没有任何 dimos 残留进程
```

---

> 如需查看任意 commit 的完整 diff：
> `git show <sha>` 或 `git log dev~15..dev --stat`
