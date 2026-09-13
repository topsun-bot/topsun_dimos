# AI 代码生成 + 轨迹闭环验证方案

> 围绕 GO2 机器狗"遥控录制 → 数据回灌 → AI 生成代码 → 仿真回放 → 轨迹对比"的完整工程方案。基于对 dimos 仓库现状（`dimos/visualization/rerun`、`dimos/memory2`、`dimos/utils/testing/replay.py`、`dimos/robot/unitree/go2`、`dimos/simulation/mujoco`）的实际代码梳理。

---

## 0. 结论速览

| 问题 | 结论 |
|---|---|
| Rerun 能否在录制时一直运行并落盘 | **能**。`RerunBridgeModule.save_to_disk=True` 时把每条 `rr.log()` 同时写入 `~/.local/share/dimos/rrd/dimos_<timestamp>.rrd`，可离线回放。 |
| "类似 .db / .bag" 的录制方案是否已经存在 | **已存在**。`data/.lfs/*.db` 就是 SQLite 写的"DimOS-bag"，由 `dimos.memory2.store.sqlite.SqliteStore` 写入，已经有 `go2_bigoffice.db`、`go2_china_office.db`、`go2_hongkong_office.db`、`go2_short.db`、`go2_slamabuse{1,2}.db` 等真实办公室录制数据。 |
| 整条"录 → 放 → 用 AI 生成代码 → 比对"闭环可行性 | **基本可行**，但有一个关键架构注意点：**纯数据回放是开环的，AI 跑出来的 cmd_vel 不会改变回放里的 odom**。要做闭环 AI 评测，必须把"回放回灌"和"仿真器（Mujoco / DimSim）"分开使用，详见 §3。 |

---

## 1. Rerun 在 dimos 中的整体架构

### 1.1 三层模型

```
┌───────────────────────────────────────────────────────────────────┐
│   生产者 (任何模块)                                                │
│   ─ rr.log("world/...", Archetype)                                 │
│   ─ 或在 LCM 消息上实现 .to_rerun() → 让 Bridge 自动转发           │
└───────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌───────────────────────────────────────────────────────────────────┐
│   RerunBridgeModule  (dimos/visualization/rerun/bridge.py)         │
│   ─ 订阅所有 pubsub topic (LCM/SHM)                                │
│   ─ 对每条消息查找 to_rerun() / visual_override → 一条 rr.log()     │
│   ─ 启动 gRPC server (默认 9877, RERUN_GRPC_PORT)                  │
│   ─ 可选: save_to_disk → 写 dimos_<ts>.rrd                         │
│   ─ 可选: 拉起 dimos-viewer / web viewer                           │
└───────────────────────────────────────────────────────────────────┘
                              │
              gRPC + WebSocket │ Blueprint (layout 描述)
                              ▼
┌───────────────────────────────────────────────────────────────────┐
│   消费者 (Viewer)                                                  │
│   ─ dimos-viewer 桌面端 (Rerun 的 dimensional fork，带遥控+点导航)  │
│   ─ Web Viewer (端口 9878, RERUN_WEB_VIEWER_PORT)                  │
│   ─ 离线: rerun <file.rrd>  /  dimos-viewer <file.rrd>            │
└───────────────────────────────────────────────────────────────────┘
```

### 1.2 关键代码（项目内位置）

| 关注点 | 文件 | 关键符号 |
|---|---|---|
| Rerun 初始化（gRPC + colormap 注册） | `dimos/visualization/rerun/init.py` | `rerun_init(start_grpc, grpc_config)` |
| 桥接模块（核心入口） | `dimos/visualization/rerun/bridge.py` | `RerunBridgeModule`、`Config`、`run_bridge()` |
| 落盘 .rrd 的代码 | `dimos/visualization/rerun/bridge.py:411` (`_enable_disk_sink`) + 配置 `save_to_disk` / `save_dir` (191–193) |
| 视图工厂（蓝图选择 rerun / foxglove） | `dimos/visualization/vis_module.py` | `vis_module(viewer_backend, rerun_config)` |
| 默认布局 | `dimos/robot/unitree/go2/blueprints/smart/unitree_go2.py` 等 |
| 全局配置（CLI 入口） | `dimos/core/global_config.py` | `rerun_save`、`rerun_save_dir`、`replay`、`replay_db`、`viewer`、`rerun_open` |
| Rerun bridge CLI | `dimos/robot/cli/dimos.py:795` | `dimos rerun-bridge` |

### 1.3 数据怎么流进 Rerun

DimOS 用 **LCM / SHM** 做 pubsub。任何"想被可视化"的消息类只需要实现：

```python
def to_rerun(self) -> Archetype | list[(entity_path, Archetype)]: ...
```

`RerunBridgeModule._on_message` 收到消息后查 `_visual_override_for_entity_path`，最终通过 `rr.log(entity_path, archetype)` 落入 gRPC server。所以**只要某个 topic 在跑，Rerun 就会持续画**——这正是你描述的"让 Rerun 一直运行"的机制。

---

## 2. 真正的"bag"在哪里：`.db` 文件 + memory2

你描述的 ".db / .bag 数据格式"在仓库里 **已经实现** 而且 LFS 里有现成样本：

### 2.1 数据位置

```
data/.lfs/
├── go2_short.db.tar.gz             ← 80 MB，默认 replay
├── go2_bigoffice.db.tar.gz         ← 196 MB
├── go2_china_office.db.tar.gz      ← 136 MB
├── go2_hongkong_office.db.tar.gz   ← 772 MB
├── go2_slamabuse1.db.tar.gz        ← 285 MB
├── go2_slamabuse2.db.tar.gz        ← 306 MB
└── extracted/<同名>.db              ← get_data() 解压后的位置
```

LFS 文档：`docs/development/large_file_management.md`；自动拉取由 `dimos/utils/data.py::get_data()` 负责（首次访问时 `tar -xzf`）。

### 2.2 存储后端

| 层 | 文件 | 角色 |
|---|---|---|
| 通用 Store API | `dimos/memory2/store/sqlite.py` | `SqliteStore(path, must_exist)`：WAL + sqlite-vec + FTS5 |
| 流式 query | `dimos/memory2/stream.py` | `.after / .before / .at / .near / .time_range / .live() / .map / .transform / .search` |
| 大块 payload | `dimos/memory2/blobstore/sqlite.py` | LiDAR / 图像放 blob 表，元数据放 observation 表 |
| 编解码 | `dimos/memory2/codecs/` | 自动选 `JpegCodec`（图像，10-20× 压缩）/ `LcmCodec`（DimOS 消息）/ `PickleCodec`（兜底） |
| Legacy 写入兜底 | `dimos/memory/timeseries/legacy.py` | `LegacyPickleStore`（旧 `TimedSensorStorage` 仍用） |

### 2.3 录制 / 回放 API（已存在）

**录**：`dimos/robot/unitree/go2/connection.py:230` (`GO2Connection.record`)：

```python
@rpc
def record(self, recording_name: str) -> None:
    TimedSensorStorage(f"{recording_name}/lidar").consume_stream(connection.lidar_stream())
    TimedSensorStorage(f"{recording_name}/odom" ).consume_stream(connection.odom_stream())
    TimedSensorStorage(f"{recording_name}/video").consume_stream(connection.video_stream())
```

**回放**：`dimos/robot/unitree/go2/connection.py:135` (`ReplayConnection`)：

```python
class ReplayConnection(UnitreeWebRTCConnection):
    def lidar_stream(self): return TimedSensorReplay(f"{dataset}/lidar").stream(**cfg)
    def odom_stream(self):  return TimedSensorReplay(f"{dataset}/odom" ).stream(**cfg)
    def video_stream(self): return TimedSensorReplay(f"{dataset}/color_image").stream(**cfg)
```

`make_connection()` 在 `cfg.replay or ip == "replay"` 时直接返回 `ReplayConnection(dataset=cfg.replay_db)`——**所有下游模块（SLAM、感知、Rerun bridge）都不知道自己在跑离线数据**。这就是"数据闭环"的底层支撑。

**Replay 调度核心**：`dimos/utils/testing/replay.py::timed_playback` —— RxPY Observable，按原始 ts 在 `TimeoutScheduler` 上 `schedule_relative`，速度可调（`speed=2.0`、`seek=10`、`duration=30`、`loop=True`），还能 `find_closest(ts, tolerance)`。

### 2.4 文档

- `docs/usage/data_streams/storage_replay.md` —— Sensor 录/放完整 API
- `docs/usage/sensor_streams/storage_replay.md` —— 镜像副本
- `dimos/memory2/intro.md` + `dimos/memory2/architecture.md` —— memory2 设计

---

## 3. 整体方案可行性 + 一个关键陷阱

### 3.1 直接对应到你的设想

| 你的设想 | 现成支撑 |
|---|---|
| 用遥控器让狗持续围绕楼运行 | `dimos/teleop/`（Quest / 手机），或 `dimos/robot/unitree/keyboard_teleop.py` |
| 运行时产生轨迹和"真值回灌" | `GO2Connection.odom`（PoseStamped）发布；额外有 `dimos/navigation/nav_stack/modules/nav_record/nav_record.py` 用于轨迹录制 |
| 数据格式 .db / .bag | 已是 SQLite `.db`（=DimOS-bag），见 §2 |
| Rerun 一直运行 | `RerunBridgeModule` 长驻；`--rerun-open native/web/both/none` 全部支持 |
| 保存运行中的轨迹 | 两个独立通道：(a) Rerun `.rrd`（可视化级），(b) memory2 `.db`（真值级），见 §4 |

### 3.2 关键陷阱（必须解决）

**"用回放数据评测 AI 生成的代码"在物理上是不闭环的。** 

回放只是把 `lidar_stream / odom_stream / video_stream` 当一个 Observable 重放出来，`ReplayConnection.move(twist)` 是 `return True` 的空操作。**AI 发的 cmd_vel 不会改变回放里的 odom 真值**——拿同一条回放 odom 去和"AI 跑出来的轨迹"对比，对比的是同一条数据。

正确的做法：

```
┌────────────────────────┐   遥控        ┌────────────────────────┐
│ 真车 / WebRTC connect  │ ─────────▶   │ memory2 SqliteStore    │
│  go2_<run_name>.db     │             │  (lidar/odom/video/cmd) │
└────────────────────────┘             └────────────────────────┘
                                                 │
                                                 │ 作为真值 (GT)
                                                 ▼
┌────────────────────────────────────────────────────────────────┐
│ 评测 harness:                                                   │
│   1. 起一份 MujocoConnection (或 DimSimConnection)              │
│      初始位姿 = GT 轨迹 t=0 的 pose                              │
│      场景 = 同一栋楼的 OccupancyGrid / mesh (data/.lfs/         │
│             big_office.ply, unitree_go2_bigoffice_map.pickle)   │
│   2. exec(AI 生成的脚本) → 它会调用 relative_move / set_goal     │
│   3. 订阅 sim 输出的 odom_stream → 写入 sim_run_<id>.db          │
│   4. 对比 GT odom  vs  sim odom  → ATE/RPE → 回传 LLM            │
└────────────────────────────────────────────────────────────────┘
```

> 备选方案 A（更便宜，但只能验证"感知/规划"代码而非"运动"代码）：保留回放，AI 生成的代码只允许做"读 lidar / 调 planner 算出一条 path"这类纯输出，再把 AI 算的 path 对照遥控时真车走的 path 比对。这种适合验证 path-planning 类技能，不适合验证导航控制环。

---

## 4. 数据留存方案（详细）

### 4.1 一次录制 = 两个产物

| 产物 | 路径 | 工具 | 用途 |
|---|---|---|---|
| **真值 .db** | `data/<run_name>.db` | `GO2Connection.record(run_name)` | AI 评测的 ground-truth |
| **可视化 .rrd** | `~/.local/share/dimos/rrd/dimos_<ts>.rrd` | `RerunBridgeModule(save_to_disk=True)` | 离线复盘、调 Blueprint、做 demo |

两者**独立**：`.rrd` 是 viewer 帧序列、不便程序化分析；`.db` 是结构化时序、可以 SQL/Stream API 查询，是真值唯一来源。

### 4.2 推荐的录制 CLI 流程（最少改动）

1. 启动一条标准 GO2 蓝图，开启录制 + 落盘：

   ```bash
   # 启动 go2 + rerun bridge，遥控连接，同时录 db + rrd
   DIMOS_RERUN_SAVE=1 DIMOS_RERUN_SAVE_DIR=./data/runs/rrd \
   dimos --viewer rerun --rerun-open native run unitree-go2

   # 在另一个 shell 调 RPC 启动 db 录制（或在 blueprint 里 autostart）
   dimos rpc GO2Connection.record name="go2_office_$(date +%Y%m%d_%H%M)"
   ```

2. 用 Quest / 手机 / 键盘遥控狗在楼里转，所有 `lidar / odom / color_image / cmd_vel / tf` 自动落到：

   - `data/go2_office_<ts>/lidar/000.pickle …` （legacy 写路径，仍是 pickle 目录）
   - 后续 PR 把写路径迁移到 `data/go2_office_<ts>.db`（memory2 read-side 已迁移，write-side TODO，见 `replay.py` 顶部注释 *"out of scope for the memory2 migration"*）

3. 结束 Ctrl+C：`.rrd` 自动落盘；`.db` 已增量写完。

### 4.3 真值 schema 约定（建议落地）

为了让 AI 代码评测可重复，约定一条录制必须含以下 streams（topic → memory2 stream name）：

| Topic | Stream | 类型 | 用途 |
|---|---|---|---|
| `/go2/odom` | `odom` | `PoseStamped` | **轨迹真值**（必需） |
| `/go2/cmd_vel` | `cmd_vel` | `TwistStamped` | 遥控指令（用于 BC / 调试） |
| `/go2/lidar` | `lidar` | `PointCloud2` | 感知输入 |
| `/go2/color_image` | `color_image` | `Image` | 感知输入 |
| `/tf` | `tf` | `TFMessage` | 坐标系 |
| `/nav/goal_request` | `goal_request` | `PoseStamped` | 高层 goal（可选） |

再在 `data/<run_name>.meta.json` 写一份 sidecar：场景名、起始位姿、目标描述（"绕办公楼一周"）、遥控者、长度、地图引用（`big_office.ply` / OccupancyGrid pickle）。这个 metadata 是后面 AI 评测 harness 必读的。

### 4.4 已经存在、可以直接用的辅助

- `dimos/navigation/nav_stack/modules/nav_record/nav_record.py` —— 轨迹/路径专用录制
- `dimos/utils/data.py::get_data()` —— 一键拉 LFS、解压、返回本地路径
- `data/.lfs/unitree_go2_bigoffice_map.pickle` —— 大办公室的 occupancy map，可以直接作为 sim 场景
- `data/.lfs/big_office.ply` —— 大办公室点云，可作 Mujoco 场景或语义地图

---

## 5. AI 生成 + 闭环验证方案

### 5.1 总体管线（每次 AI 迭代一次）

```
┌──────────────────────────────────────────────────────────────────┐
│ Step 1  评测 harness 选一段 GT run：data/go2_office_xxx.db        │
│         读取 meta.json → 拿到 (scene, start_pose, task_text, len) │
└──────────────────────────────────────────────────────────────────┘
                                  │
                                  ▼
┌──────────────────────────────────────────────────────────────────┐
│ Step 2  prompt LLM (e.g. agents/demo_agent.py 改造):               │
│   system: dimos skill 列表 (relative_move / start_patrol /        │
│           set_goal …), Twist / PoseStamped 类型签名                │
│   user:   task_text + start_pose                                  │
│   ↓                                                              │
│   产出 generated_skill.py，必须导出 run(robot) 协程                │
└──────────────────────────────────────────────────────────────────┘
                                  │
                                  ▼
┌──────────────────────────────────────────────────────────────────┐
│ Step 3  在 sim 里跑：                                             │
│   GlobalConfig.simulation = "mujoco",                            │
│     mujoco_room = <场景>, mujoco_start_pos = start_pose           │
│   ModuleCoordinator.build( go2_smart_blueprint ).loop_until(end) │
│   同时 SqliteStore("sim_run_<id>.db") 订阅 sim odom_stream         │
│   背景线程 exec( generated_skill.run(robot_proxy) )                │
└──────────────────────────────────────────────────────────────────┘
                                  │
                                  ▼
┌──────────────────────────────────────────────────────────────────┐
│ Step 4  轨迹对比：                                                 │
│   gt   = SqliteStore("data/go2_office_xxx.db").stream("odom")    │
│   sim  = SqliteStore("sim_run_<id>.db"     ).stream("odom")      │
│   ATE  = mean ||align(sim_xy) - gt_xy||₂                          │
│   RPE  = window-wise relative pose error                         │
│   COV  = 覆盖面积 IoU (走没走到关键 waypoint)                       │
│   if ATE < ε_ate and COV > τ_cov:  pass                          │
│   else: feed (ATE, RPE, COV, diff plot) 回 LLM，goto Step 2       │
└──────────────────────────────────────────────────────────────────┘
```

### 5.2 误差指标（必须有数学定义）

约定 GT 轨迹 `G = {(t_i, x_i, y_i, θ_i)}`，AI 仿真轨迹 `S = {(t_j, x_j, y_j, θ_j)}`。

1. **时间对齐**：对每个 `t_i` 用 `find_closest(t_i, tolerance=0.1s)` 找 `S` 对应点（`replay.py` 已经提供）。
2. **Umeyama 对齐**（评测 AI 在自由起点也算对）：解 SE(2) 最优刚体变换 `T*`，最小化 `Σ ||T* s_j - g_i||²`。
3. **ATE (Absolute Trajectory Error)** = `sqrt(mean_i ||T* s_i - g_i||²)`。
4. **RPE (Relative Pose Error, Δ=1s)** = `sqrt(mean_i || (g_{i+Δ} ⊖ g_i) ⊖ (s_{i+Δ} ⊖ s_i) ||²)`。
5. **Coverage / Waypoint Hit Rate**：从 GT 中等距采样 `K` 个关键 waypoint，统计 sim 是否在容差球内通过。
6. **Pass 判定**（建议初始阈值，按场景调）：
   - 小规模相对移动技能：`ATE < 0.25 m` 且 `RPE < 0.10 m/s` 且 `Hit Rate > 0.9`
   - 办公楼一圈巡逻：`ATE < 1.0 m` 且 `Hit Rate > 0.8`

### 5.3 给 LLM 的反馈结构（建议 JSON）

```json
{
  "verdict": "fail",
  "metrics": { "ate_m": 1.83, "rpe_m": 0.41, "coverage": 0.62 },
  "diff_summary": "AI 轨迹在第 3 个 waypoint 后向右偏移 ~2.5m，未通过会议室门。",
  "rerun_url": "rerun+http://127.0.0.1:9877/proxy",
  "first_divergence_ts": 17.4,
  "suggested_focus": ["检查 set_goal 的 yaw 方向", "确认 relative_move 的 left 符号"]
}
```

把这个回灌给 LLM 做下一轮 codegen——这就是你说的"AI 持续修改直到误差在允许范围内"。

### 5.4 实现路径（可执行清单）

> 优先级 P0=必做才能闭环；P1=显著提升体验；P2=可后置

| # | 模块 | 任务 | 优先级 | 落点 |
|---|---|---|---|---|
| 1 | memory2 写入 | 把 `TimedSensorStorage` 写路径从 `LegacyPickleStore` 切到 `SqliteStore`（已是 TODO，见 `replay.py` 顶部） | P0 | `dimos/memory2/store/sqlite.py` + `replay.py` |
| 2 | 录制脚本 | 加 `dimos record --run-name --topics ...` CLI，封装 `GO2Connection.record` + 写 meta.json | P0 | `dimos/robot/cli/dimos.py` |
| 3 | 评测 harness | 新建 `dimos/eval/codegen_loop/` 三件：`runner.py` / `metrics.py` / `feedback.py` | P0 | 新包 |
| 4 | sim wrapper | `dimos/eval/codegen_loop/sim_wrapper.py`：给定 `(scene, start_pose, ai_skill_path)` 跑 Mujoco 并把 odom 落到 sim_db | P0 | 新文件 |
| 5 | metrics | 实现 ATE / RPE / Hit Rate，参考 `evo` 库的 Umeyama；对齐用 `Memory2ReplayAdapter.find_closest` | P0 | `metrics.py` |
| 6 | LLM 桥 | 复用 `dimos/agents/demo_agent.py` 或写个 `eval/codegen_loop/llm.py`，限制只允许调用 `UnitreeSkillContainer` 暴露的 `@skill` | P0 | 新文件 |
| 7 | Rerun 可视对比 | 在 Rerun 里同时画 GT (红) 和 sim (绿) 两条轨迹 + 误差色带，单独 Blueprint | P1 | 新 blueprint |
| 8 | LFS GT 集 | 把团队遥控录的 N 条 office run 入 LFS，定义"标准评测集" | P1 | `data/.lfs/eval/*.db` |
| 9 | CI 接入 | `pytest tests/eval/test_ai_codegen.py` 跑回归套件，pass 阈值见 §5.2 | P2 | tests |
| 10 | 安全沙箱 | exec AI 代码时用 `dimos/porcelain/local_module_source.py` 子进程隔离，避免 LLM 写出 `os.system` 之类 | P2 | `porcelain` |

### 5.5 与 dimos 现有抽象的最小耦合接口

```python
# dimos/eval/codegen_loop/runner.py 草图
from dimos.memory2.store.sqlite import SqliteStore
from dimos.eval.codegen_loop.sim_wrapper import run_in_sim
from dimos.eval.codegen_loop.metrics import ate, rpe, hit_rate
from dimos.eval.codegen_loop.llm import propose_skill, refine

def codegen_eval_loop(gt_db_path: str, max_iters: int = 5) -> dict:
    gt = SqliteStore(path=gt_db_path, must_exist=True)
    meta = json.load(open(gt_db_path.replace(".db", ".meta.json")))

    history = []
    for step in range(max_iters):
        skill_src = propose_skill(meta["task_text"], meta["start_pose"], history)
        sim_db = run_in_sim(
            scene=meta["scene"],
            start_pose=meta["start_pose"],
            skill_src=skill_src,
            duration_s=meta["duration_s"] * 1.2,
        )
        m = {
            "ate":  ate(gt.stream("odom"), SqliteStore(sim_db).stream("odom")),
            "rpe":  rpe(gt.stream("odom"), SqliteStore(sim_db).stream("odom"), delta=1.0),
            "hit":  hit_rate(gt.stream("odom"), SqliteStore(sim_db).stream("odom")),
        }
        history.append({"src": skill_src, "metrics": m})
        if m["ate"] < 1.0 and m["hit"] > 0.8:
            return {"status": "pass", "iters": step + 1, "history": history}
        # 反馈给下一轮
    return {"status": "fail", "iters": max_iters, "history": history}
```

---

## 6. 落地路线图（建议两周内能跑通 demo）

| 周次 | 里程碑 | 验收 |
|---|---|---|
| W1 D1-D2 | 把 §4.2 的录制流程跑通，团队产出 3 条 `office_lap_*.db` + meta.json | 能用 `TimedSensorReplay` 重放并在 Rerun 看到 |
| W1 D3-D4 | 写完 §5.4 #3-#5（runner / sim_wrapper / metrics），手写一段 skill_src 跑通对比 | 在 Mujoco 中跑出 sim_run.db，ATE/RPE 打印有数 |
| W1 D5 | 接通 LLM (`agents/demo_agent.py` + skills 注入)，能产出 skill.py | Codegen 单次成功率 ≥ 30% |
| W2 D1-D2 | 闭环迭代：失败 → 喂回 metrics → 再 codegen，跑出"3 次内 pass" 的 case | 至少 1 个 office_lap pass |
| W2 D3 | Rerun 双轨迹叠加 Blueprint + 误差色带 | demo 可视化通过 |
| W2 D4-D5 | CI 与文档：3 个评测集 + pytest 套件 + `docs/usage/ai_codegen_loop.md` | 跑通 `pytest tests/eval/test_ai_codegen.py` |

---

## 7. 已识别风险 & 解法

| 风险 | 影响 | 解法 |
|---|---|---|
| sim 与真车动力学差距 (sim2real gap) | sim 轨迹和 GT 偶发不可对齐 | 用相对指标（RPE）+ 覆盖率代替严格 ATE；阈值按场景标定 |
| 回放数据不可用于运动评测（§3.2） | 看起来在闭环，实则空转 | 强制 codegen 评测走 sim；回放只用于"感知 / 规划只读"评测 |
| memory2 写路径还在 pickle | 录制工程化弱、扩展性差 | 路线图 #1，把 SensorStorage 切到 SqliteStore |
| LLM 产生不安全代码（系统调用） | 评测机被污染 | exec 走 `porcelain` 子进程 + 白名单 import + 网络隔离 |
| 大场景下 Mujoco 慢 | 单轮迭代时间长 | mesh 简化 + `mujoco_steps_per_frame` 调大 + 起点附近 BBOX 限定 |
| `.rrd` 文件体积膨胀 | 磁盘满 | `memory_limit` 控制 in-mem ring，再加 logrotate 清理 `~/.local/share/dimos/rrd/` |

---

## 8. 参考索引（你可以直接打开看的）

- `dimos/visualization/rerun/bridge.py` — Rerun 桥接全部逻辑
- `dimos/visualization/rerun/init.py` — `rerun_init()` 入口
- `dimos/visualization/vis_module.py` — 选择 viewer 后端
- `dimos/utils/testing/replay.py` — `TimedSensorReplay` (memory2 适配器) + `timed_playback`
- `dimos/memory2/intro.md` / `architecture.md` — memory2 设计文档
- `dimos/memory2/store/sqlite.py` — `.db` 真正的存储后端
- `dimos/robot/unitree/go2/connection.py` — `GO2Connection.record` / `ReplayConnection` / `make_connection`
- `dimos/robot/unitree/mujoco_connection.py` — Mujoco sim 连接（同一 Protocol）
- `dimos/robot/unitree/unitree_skill_container.py` — `@skill relative_move` 等 AI 可调技能
- `dimos/agents/demo_agent.py` — LLM agent 起点
- `docs/usage/data_streams/storage_replay.md` — 官方录/放 API 说明
- `data/.lfs/go2_*.db.tar.gz` — 现成的 5 条办公室真值数据集

---

**TL;DR**：dimos 已经把"录制 → 回放 → Rerun 可视化"这套基础设施做完了（`.db` + `.rrd` + `RerunBridgeModule` + `ReplayConnection`）。你描述的"AI codegen + 轨迹比对"闭环要落地，**核心新增工作只有两块**：(1) 把"运行 AI 代码"的目标从 `ReplayConnection` 切到 `MujocoConnection`（因为前者不响应 cmd_vel）；(2) 写一个评测 harness 把 GT `.db` 和 sim `.db` 的 odom 流做 ATE/RPE/Hit-Rate 对比并喂回 LLM。其它东西基本都不用动。

---

## 9. 现状核实（2026-07-02，逐条对照源码验证）

### 9.1 勘误

| 原说法 | 核实结果 | 依据 |
|---|---|---|
| Rerun gRPC 默认端口 9876 | **实际 9877** | `dimos/visualization/rerun/constants.py:30` |
| Web Viewer 端口 7779 | **Rerun web 端口是 9878**；7779 属于 `WebsocketVisModule`（另一套通用 web 可视化） | `constants.py:31`、`dimos/web/websocket_vis/websocket_vis_module.py` |
| `dimos rerun-bridge --daemon` | `--daemon` 是 `dimos run` 的选项，正确写法 `dimos --daemon run …`；`rerun-bridge` 子命令本身无 daemon 参数 | `dimos/robot/cli/dimos.py:218,795` |

### 9.2 录制写路径的准确结论（双轨制）

- **旧轨（pickle，不要再用）**：`GO2Connection.record()`（`connection.py:229`）→ `TimedSensorStorage = LegacyPickleStore`（`dimos/utils/testing/replay.py:319`），写 `<name>/<stream>/NNN.pickle`。
- **新轨（.db，采用这条）**：memory2 `Recorder`（`dimos/memory2/module.py:247`）→ `SqliteStore`。`Go2Memory`（`dimos/robot/unitree/go2/blueprints/smart/unitree_go2.py:49`）随 `unitree-go2-memory` 蓝图录 `odom/lidar/color_image`；`NavRecord`（`dimos/navigation/nav_stack/modules/nav_record/nav_record.py:37`）可补录 `cmd_vel/path/goal` 等导航流。
- §4.2/§5.4 中"把 TimedSensorStorage 切到 SqliteStore"一项**实际上不需要做**——直接用 `Recorder` 新轨即可；本分支的 `dimos go2tool truth capture` 已经这么做了。

### 9.3 已落地（本分支 codex/go2-truth-capture-gui）

| 组件 | 位置 | 状态 |
|---|---|---|
| 真值采集 CLI `dimos go2tool truth capture/check` | `dimos/robot/unitree/go2/cli/truth_capture.py` | ✅ 写 `.db + manifest.json + check.json` |
| Mac 双击 GUI | `Go2TruthCapture.command` + `truth_capture_gui.py` | ✅ |
| 轨迹指标 ATE/RPE/HitRate/首发散点 | `dimos/eval/codegen_loop/metrics.py` | ✅ 纯 numpy |
| waypoint 抽取（弧长均匀 + RDP） | `dimos/eval/codegen_loop/waypoints.py` | ✅ |
| LLM prompt 模板 + 无 LLM 基线 | `dimos/eval/codegen_loop/prompt.py` | ✅ |
| 现场操作指南 | `docs/development/go2_truth_capture.md`、`recording_truth.md` | ✅ |

### 9.4 尚缺（闭环跑通前的 P0）

1. `dimos/eval/codegen_loop/sim_wrapper.py`：给定 (scene, start_pose, skill 源码) 拉起 MujocoConnection 跑完并把 odom 落成 `sim_run_<id>.db`。
2. `dimos/eval/codegen_loop/runner.py`：propose → run_in_sim → evaluate → refine 循环驱动器（先用 `prompt.hardcoded_baseline` 验通链路，再接 LLM）。
3. `tests/eval/test_metrics.py`：metrics 的 self-vs-self 单测（metrics.py 文档字符串已引用但文件不存在）。
4. MuJoCo 办公室场景：现只有 `scene_office1.xml`/`scene_empty.xml`（`data/.lfs/extracted/mujoco_sim/`）；真实办公楼场景需用 `--mujoco-room-from-occupancy` 从占用栅格生成，或手工做 `scene_bigoffice.xml`。
5. 真值数据：`data/truth/go2/` 目前为空，需按 `docs/development/recording_truth.md` 现场采集第一条办公楼真值。
6. 现有 LFS 的 6 条 `go2_*.db` 只有 `odom/lidar/color_image`（已用 sqlite3 验证），**无 `cmd_vel` 等控制流**——可作回放/感知评测数据，不含遥控指令真值；新采集时如需控制流，把 `NavRecord` 挂进蓝图。
