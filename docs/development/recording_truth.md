# 人工遥控采集 Go2 真值 DB —— 小白级操作指南

> 本指南适用于：拿着 Unitree Go2 原装手柄，绕楼走一圈，把传感器和轨迹数据落成可作为 AI 闭环基准的 `.db` 文件。
> 工具：`dimos go2tool truth`（仓库里已有，不用自己写）。
> 数据落到：`data/.lfs/extracted/`（和 `go2_bigoffice.db` 等历史数据并排）。

---

## 一、准备工作

### 1. 硬件清单
- Unitree Go2（标准四足）× 1
- 原装手持遥控器 × 1
- 笔记本电脑（开发机），和机器狗在同一个 WiFi 下
- 已在开发机上 `uv sync` 跑通 dimos（参考仓库根目录 `README.md`）

### 2. 知道机器狗的 IP

如果不知道狗的 IP，开发机上跑：

```bash
uv run dimos go2tool discover --lan --timeout 10
```

会输出类似：
```
SOURCE NAME           IP              MAC                 SERIAL
LAN    -              192.168.123.18  AA:BB:CC:DD:EE:FF   12345
```

记下 `IP` 列的地址。下文用 `<GO2_IP>` 代指。

> 如果机器狗还没接 WiFi，先用 `uv run dimos go2tool connect-wifi --ssid <你的WiFi名> --password <密码>` 蓝牙配网，参考 [go2tool.py](../../dimos/robot/unitree/go2/cli/go2tool.py)。

### 3. 创建落地目录（首次执行）

```bash
mkdir -p data/.lfs/extracted
```

如果目录已经存在（你跟我一样已经有 `go2_bigoffice.db` 之类的文件），跳过这一步。

---

## 二、一条命令开始采集

把 `<GO2_IP>` 换成你的狗的 IP，`<场所名>` 换成你正在采的地点：

```bash
uv run dimos go2tool truth capture \
    --robot-ip <GO2_IP> \
    --route office_loop_north_gate \
    --scene shanghai_office \
    --task "绕楼一圈，检查门是否关闭" \
    --user zhang \
    --save-rerun \
    --rrd-dir ~/.local/share/dimos/rrd
```

参数解释（按出现顺序）：

| 参数 | 必须？ | 说明 |
|---|---|---|
| `--robot-ip` | ✅ | 机器狗 IP，从上面 discover 拿到 |
| `--route` | 推荐 | 路线标签，写到文件名里。约定下划线分隔，例如 `office_loop_v1`、`hongkong_door_check` |
| `--scene` | 推荐 | 场所名，写到 `.manifest.json` 的 `scene` 字段。例如 `shanghai_office` |
| `--task` | 推荐 | 自然语言描述这次采集的目的。AI 闭环用 |
| `--user` | 推荐 | 你的名字 / 工号，用于追溯谁采的 |
| `--save-rerun` | 可选 | 同时存一份 `.rrd`（Rerun 可视化数据），用于复盘看图 |
| `--rrd-dir` | 配合上面 | `.rrd` 文件落到哪 |

执行后，你会看到三段输出：

1. **Underlying command** ——告诉你它实际拉起了什么 `dimos run ...` 命令（看一眼，心里有数）
2. **Capture is running** ——三步提示（等流稳定 → 遥控走一圈 → Ctrl+C 停）
3. **Rerun 窗口** ——可视化弹窗（看到机器狗在地图上走）

### 期望看到的 Rerun 画面

- 左边：摄像头实时画面
- 右边：3D 世界视图，能看到 lidar 点云 + 机器狗模型在动

> ⚠️ 如果 Rerun 没弹窗，看终端有没有报错。常见原因：macOS 上 Rerun 需要原生支持，可换 `--viewer foxglove` 试试，或者干脆 `--viewer none` 不要可视化（数据照样录）。

---

## 三、遥控走一圈

1. 等 5-10 秒，让传感器流先稳定（终端会有日志在刷）
2. **拿手柄把狗扶起来**（站起来）
3. 开始绕楼走，路线和速度按你的判断
4. 走完一圈后，**回到终端，按 `Ctrl+C`**

按下 `Ctrl+C` 后工具会自动：
- 优雅停止 dimos 进程（SIGTERM，15 秒后 SIGKILL 兜底）
- 自检 `.db`：检查 odom/lidar/color_image 三个流的数量和时长
- 写 `.check.json`：包含每条流的统计和 `start_pose`（起点位姿）

---

## 四、检查产物

采完之后看 `data/.lfs/extracted/` 下：

```bash
ls -lh data/.lfs/extracted/go2_office_loop_north_gate_*
```

预期会有三个文件（举例时间戳 `20260525_153012`）：

```
go2_office_loop_north_gate_20260525_153012.db            # 真值数据（最重要）
go2_office_loop_north_gate_20260525_153012.manifest.json # 元数据：scene/task_text/teleop_user/...
go2_office_loop_north_gate_20260525_153012.check.json    # 自检报告：每条流的 count/duration/start_pose
```

打开 `.check.json` 看一眼：

```bash
cat data/.lfs/extracted/go2_office_loop_north_gate_*.check.json | head -40
```

预期看到：
```json
{
  "db_path": "data/.lfs/extracted/go2_office_loop_north_gate_20260525_153012.db",
  "ok": true,
  "duration_s": 287.4,
  "start_pose": {"x": 0.12, "y": -0.03, "z": 0.31},
  "streams": {
    "odom": {"count": 2240, "duration_s": 287.4, "pose_count": 2240},
    "lidar": {"count": 1124, "duration_s": 286.9, "pose_count": 1124},
    "color_image": {"count": 4080, "duration_s": 287.1, "pose_count": 4080}
  }
}
```

判断标准：
- `"ok": true` → 数据合格 ✅
- `"ok": false` → 看 `failed_checks` 找原因（最常见：时长不够 / 某条流缺失）

如果想单独检查某个老 db：

```bash
uv run dimos go2tool truth check data/.lfs/extracted/go2_bigoffice.db
```

---

## 五、常见问题排查

### Q1. `DB already exists` 错误
说明同名 db 已经在，加 `--force` 覆盖，或者改 `--route` 换个名字（推荐后者，避免误删数据）。

### Q2. Ctrl+C 之后狗没停，还在动
脚本只停 dimos 进程，**不会停手柄/狗本身**。手动用手柄停。

### Q3. odom 时长不够（< 30 秒）
默认要求最少 30 秒。如果只是测试想留更短的数据，加 `--min-duration-s 5`；如果真的就是采得太短了，重新采。

### Q4. Rerun 太卡 / 内存爆
加 `--viewer none` 完全不要可视化，只录数据。事后用 `dimos rerun-bridge` 单独看 `.rrd`。

### Q5. 想先看实际要执行的命令，不真跑
加 `--dry-run`，工具会打印底层的 `dimos run ...` 命令但不执行。

### Q6. 想自动定时停（比如固定采 5 分钟）
加 `--duration-s 300`，到点自动 SIGTERM。不用守在终端。

---

## 六、采完之后怎么用这份数据

### 1. 立刻可以做的：回放看

```bash
uv run dimos --replay --replay-db go2_office_loop_north_gate_20260525_153012 \
    --viewer rerun \
    run unitree-go2-memory
```

> `--replay-db` 后面**不带 `.db` 后缀和路径**，dimos 会去 `data/.lfs/extracted/` 找。

### 2. 后续用于 AI 闭环（项目方案里说的）

参考 [PROJECT_PLAN_AI_CODING_ON_ROBOTS.md](../../PROJECT_PLAN_AI_CODING_ON_ROBOTS.md) §4 四步循环：
- 真值通道：你刚采的这个 `.db` 是 ground truth（**只读**）
- 评测通道：把这个 db 的起点 + 场景灌进 Mujoco → AI 代码在 sim 里跑 → 输出 `sim_run_<id>.db`
- 对比：用 ATE/RPE 对比两个 db 的 odom 轨迹

### 3. 加入回归数据集

把你的 db 提交进 git LFS（如果还没在 LFS 里）：

```bash
# 看 docs/development/large_file_management.md 的 LFS 操作流程
git lfs track "data/.lfs/extracted/go2_office_loop_*.db"
git add data/.lfs/extracted/go2_office_loop_*.db .gitattributes
git commit -m "chore: 加入 office_loop 真值采集 (zhang, 2026-05-25)"
```

---

## 七、本指南覆盖的 PROJECT_PLAN 约定

| PROJECT_PLAN §5 字段 | 在哪体现 |
|---|---|
| `scene` | `--scene` 参数 → manifest |
| `start_pose` | 自检时从 odom 第一条提取 → `.check.json` |
| `task_text` | `--task` 参数 → manifest |
| `duration_s` | 自检算的 → `.check.json` |
| `teleop_user` | `--user` 参数 → manifest |
| `map_ref` | `--map-ref` 参数 → manifest（可选） |

---

## 八、不在本工具范围内的事

按手柄遥控的现实，下面这些数据**不会被录到**，知道一下别期待：

- `cmd_vel` 流：手柄走 WebRTC SDK 直连狗内部，dimos 这边的 `cmd_vel` topic 拿不到手柄指令
- `path / goal / waypoint`：你没启动 nav stack 去规划路径，所以这些字段是空的

如果将来要用 KeyboardTeleop（键盘 WASD 走 dimos pipeline）采集，可以做"全栈采集"补这些字段——届时新增一个 `unitree-go2-truth` blueprint。**当前手柄场景下没意义，YAGNI 不做**。

---

## 九、改了这份工具？

修改入口：[dimos/robot/unitree/go2/cli/truth_capture.py](../../dimos/robot/unitree/go2/cli/truth_capture.py)。

测试改完后能跑：
```bash
uv run dimos go2tool truth capture --help
```

看到新参数即生效。
