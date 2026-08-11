# Go2 录制与导航流程

> 环境：在 `topsun_dimos` 根目录执行 `deactivate 2>/dev/null; source .venv/bin/activate`，确认 `echo $VIRTUAL_ENV` 以 `topsun_dimos/.venv` 结尾。详见 `jiangtao/bugfix.md` §9/§13。

## Go2 4G WebRTC 导航集成式自动回充

### 0. 当前入口与安全默认值

正式入口是 DimOS 蓝图 `unitree-go2-auto-recharge`，不是单独运行官方 NX 二进制，也不是把程序部署到 Go2 本体。完整通信链路为：

```text
本机 AutoRechargeModule
  → MovementManager 控制权仲裁
  → GO2Connection
  → 4G Remote WebRTC DataChannel / Video Track
  → Go2
```

当前蓝图默认 `allow_liedown=false`：可以识别二维码、接管导航、移动到 staging pose、视觉对准和前进到最终停靠位置，但不会趴下。第一次现场测试必须保持这个默认值。

另外建议启动时显式设置 `auto_takeover=false`。此时程序只监控，不会因为启动时已经看到二维码而立即移动；由操作员在第二个终端调用 `start_recharge()` 后才开始回充。

### 1. 每次启动前准备

机器人周围必须有人看护，后方至少预留 0.8 m 回退空间，机器人与充电桩之间、机器人后方均不得有人、线缆或其他障碍物。首次测试先关闭充电桩电源或覆盖充电触点，只验证轨迹。

凭据只放在本机 `.env` 或 `jiangtao/run-self.md`，不要提交到 Git，也不要直接写进本文件：

```bash
cd /Users/taojiang/huazhijian/topsun-bot/topsun_dimos
deactivate 2>/dev/null || true
source .venv/bin/activate

# 如果凭据保存在 .env：
set -a
source .env
set +a

# 4G Remote 必需配置；具体值由 .env 提供。
export UNITREE_WEBRTC_METHOD=remote
export UNITREE_REGION=cn

# 检查配置是否存在，不输出密码和 AES key。
test -n "$UNITREE_USERNAME" && echo "UNITREE_USERNAME: OK"
test -n "$UNITREE_PASSWORD" && echo "UNITREE_PASSWORD: OK"
test -n "$UNITREE_SERIAL" && echo "UNITREE_SERIAL: $UNITREE_SERIAL"

# 确认新蓝图已注册。
dimos list | rg 'unitree-go2-auto-recharge'
```

如果虚拟环境依赖还没有安装：

```bash
uv sync --extra all
```

### 2. 第一阶段：只验证轨迹，不允许趴下

终端 1 启动蓝图。该模式会在操作员放行后真实旋转、前进或回退，但最终不会趴下：

```bash
cd /Users/taojiang/huazhijian/topsun-bot/topsun_dimos
source .venv/bin/activate
set -a; source .env; set +a

dimos \
  --viewer none \
  --navigation-trace-level full \
  --unitree-webrtc-method remote \
  --unitree-username "$UNITREE_USERNAME" \
  --unitree-password "$UNITREE_PASSWORD" \
  --unitree-serial "$UNITREE_SERIAL" \
  --unitree-region cn \
  run unitree-go2-auto-recharge \
  -o autorechargemodule.auto_takeover=false \
  -o autorechargemodule.allow_liedown=false
```

等终端 1 完成 WebRTC、视频、odom、costmap 和模块启动后，在终端 2 连接运行中的 DimOS：

```bash
cd /Users/taojiang/huazhijian/topsun-bot/topsun_dimos
source .venv/bin/activate
dimos shell
```

进入 IPython 后依次执行：

```python
modules()
app.AutoRechargeModule.recharge_status()

# 确认机器人前后无人、后方回退区域无障碍后，才执行这一行。
app.AutoRechargeModule.start_recharge()

# 随时查询当前状态、二维码距离和 staging 目标。
app.AutoRechargeModule.recharge_status()
```

`start_recharge()` 之后的预期状态链路是：

```text
monitor
→ validate_dock
→ claim_task
→ staging_nav（不在中线时先导航到二维码正前方）
→ acquire_for_servo
→ claim_servo
→ stop_and_observe / visual_servo
→ 必要时 recovery_stop → recovery_backoff → recovery_reacquire
→ final_settle
→ succeeded（allow_liedown=false 时到此结束并释放控制权）
```

到达 `succeeded` 后检查：

- 机器人正对二维码，大致落在充电板中心线上；
- 相机到二维码的 PnP 深度约为 `0.30～0.45 m`；
- 没有横移指令；一次只执行 yaw 或前后移动中的一个；
- 每个运动脉冲后都会发零速度并等待新图像；
- 近场丢码或走过头时先停止，再直线后退到约 `0.70～0.85 m` 的重新捕获窗口；
- 全程没有进入 `failed`，也没有出现 `recovery_corridor_blocked` 等安全失败。

### 3. 紧急停止、取消和重新开始

最优先的现场取消方式是在 `dimos shell` 中执行：

```python
app.AutoRechargeModule.cancel_recharge()
app.AutoRechargeModule.recharge_status()
```

取消会立即发布零速度、取消 staging 导航目标并释放回充任务/视觉伺服控制权。键盘遥操作或新的地图点击目标也会抢占回充并触发取消。

如果 shell 不可用，在另一个终端执行：

```bash
dimos stop
```

前台运行时也可以在终端 1 按 `Ctrl-C`。停止后必须现场确认机器狗已经静止；不要仅依据命令返回值判断。

取消后重新尝试：

```python
app.AutoRechargeModule.start_recharge()
```

### 4. 第二阶段：允许趴下并验证充电

只有第一阶段的正面、侧面、斜向、近场丢码和走过头恢复测试全部通过后，才允许使用这一模式。启动命令与第一阶段相同，只把最后一项改为 `true`：

```bash
cd /Users/taojiang/huazhijian/topsun-bot/topsun_dimos
source .venv/bin/activate
set -a; source .env; set +a

dimos \
  --viewer none \
  --navigation-trace-level full \
  --unitree-webrtc-method remote \
  --unitree-username "$UNITREE_USERNAME" \
  --unitree-password "$UNITREE_PASSWORD" \
  --unitree-serial "$UNITREE_SERIAL" \
  --unitree-region cn \
  run unitree-go2-auto-recharge \
  -o autorechargemodule.auto_takeover=false \
  -o autorechargemodule.allow_liedown=true
```

然后仍由终端 2 手动放行：

```bash
dimos shell
```

```python
app.AutoRechargeModule.recharge_status()
app.AutoRechargeModule.start_recharge()
```

完整成功状态为：

```text
final_settle
→ lie_down
→ verify_charge
→ charging_hold
```

`charging_hold` 才代表已经通过本机 WebRTC `lowstate.bms_state.current` 连续约 4 秒的充电判据。进入该状态后，回充模块会释放视觉速度控制权，但继续持有任务控制权并保持零速度，防止导航目标再次启动。

需要离开充电桩时，先释放 `charging_hold`；该命令本身不会让机器狗站起或移动：

```python
app.AutoRechargeModule.leave_charger()
```

随后再使用经过确认的站立/离桩操作。不要在仍处于 `charging_hold` 时直接发送导航目标。

### 5. 状态、日志和失败原因

运行状态和实时日志：

```bash
dimos status
dimos log -f
```

RPC 状态包含当前状态、最后一次二维码位姿、staging pose 和失败码：

```python
app.AutoRechargeModule.recharge_status()
```

启用 `--navigation-trace-level full` 后，本次运行目录中会生成：

```text
<run-dir>/main.jsonl
<run-dir>/navigation/recharge-<pid>.jsonl
<run-dir>/navigation/mux-<pid>.jsonl
<run-dir>/navigation/planner-<pid>.jsonl
```

停止程序后，从 `dimos status` 或 `~/.local/state/dimos/logs/` 找到对应 `<run-dir>`，执行：

```bash
# 查看回充状态切换、每个速度脉冲和失败码。
rg 'recharge_state_transition|recharge_cmd|recharge_failed|recharge_success' \
  '<run-dir>/navigation'/recharge-*.jsonl

# 生成导航诊断报告；必须在该 run 已停止后执行。
dimos nav analyze '<run-dir>'
```

常见失败码与处理：

| 失败码 | 含义 | 现场处理 |
|---|---|---|
| `input_image_stale` | 4G 视频帧超时 | 取消，检查 WebRTC 视频和网络后重启 |
| `input_odom_stale` | odom 超时 | 取消，不允许继续盲走 |
| `costmap_stale` | costmap 超时 | 检查建图链路；不得绕过可达性门禁 |
| `dock_target_blocked` / `staging_target_blocked` | 目标落在障碍或未知区 | 清障或重新放置机器人，不要强行前进 |
| `staging_corridor_blocked` | 去 staging pose 的走廊被阻挡 | 清理路线后重新开始 |
| `marker_not_found_at_stage` | 到 staging 后仍未稳定看到二维码 | 调整初始位置、光照或二维码平整度 |
| `recovery_corridor_blocked` | 机器人后方回退区域不安全 | 立即人工检查后方，清障后重启 |
| `near_field_reacquire_failed` | 后退后仍无法重新捕获二维码 | 检查码是否出画、反光或被机器人遮挡 |
| `visual_servo_timeout` | 视觉对接总时间超限 | 根据 trace 检查 yaw 符号、摇杆死区和图像延迟 |
| `lie_down_failed` | 趴下 API 失败 | 取消并人工确认姿态，不继续验证充电 |
| `charge_unverified` | 已趴下但 BMS 电流未进入充电带 | 自动站起重试或最终失败；检查落点和充电桩供电 |

### 6. 仅做视觉/充电判据诊断的旧脚本

下面脚本只用于校准和排障，不是最终导航集成入口。

只观察二维码，不发送运动命令：

```bash
uv run python jiangtao/scripts/demo_go2_4g_aruco_recharge.py
```

采集已经趴在桩上时的 BMS 充电状态：

```bash
uv run python jiangtao/scripts/demo_go2_sample_charge_state.py --seconds 20
```

独立脚本只有显式增加 `--execute` 才会运动；日常回充测试应使用 `unitree-go2-auto-recharge` 蓝图。

## 4G Remote 摇杆死区（硬下限，禁止再降）

> **适用范围**：`UNITREE_WEBRTC_METHOD=remote`（4G）且默认 `velocity_api=False`。
> 这时 `UnitreeWebRTCConnection.move(Twist)` **不会**把命令当成真正的 m/s 或 rad/s，而是直接映射到无线摇杆轴：

| Twist 字段（代码里常误叫 mps / rad_s） | 实际下发 | 含义 |
|---|---|---|
| `linear.x` | `ly` | 前后摇杆，约 **−1~+1**（+1 ≈ 满杆前进） |
| `linear.y` | `lx = -y` | 左右横移摇杆 |
| `angular.z` | `rx = -yaw` | 转向摇杆 |

源码：`dimos/robot/unitree/connection.py` → `_publish_movement` → `WIRELESS_CONTROLLER`。
只有显式 `velocity_api=True` 时才走 Sport `Move` 的 `x/y/yaw`（那才更接近物理速度语义）。

### 三狗（现场设备，SN 不入库）标定 — 2026-08-05

| 轴 | 硬下限 | 实测依据 | 低于此值的行为 |
|---|---|---|---|
| **前进 `\|ly\|` / `linear.x`** | **0.10**（10% 杆） | odom：0.05/0.06/0.08 位移 &lt;1 cm；**0.10** 起 3 s 走约 6 cm | **设备直接不动** |
| **转向 `\|rx\|` / `\|angular.z\|`** | **0.20**（20% 杆） | 4G 旋转标定；`DIMOS_ROTATE_MIN_RAD_S=0.2` | **设备直接不转** |
| 横移 `\|lx\|` / `\|linear.y\|` | 暂按 **0.15** | 回充侧向初值；未做完整扫档 | 低于易无响应 |

### 充电确认（三狗实采 2026-08-05）

本机走 WebRTC，**没有**独立充电 DDS topic。主判据只看 `rt/lf/lowstate` 的 **`bms_state.current`**：

| 状态 | `bms_state.current` | `power_v` | 备注 |
|---|---|---|---|
| **趴桩充电 (带 A)** | **≈ -1030 mA**（-1059~-1009） | ≈ 30.87 V | 早期样本 |
| **趴桩充电 (带 B)** | **≈ +8030 mA**（7933~8121） | ≈ 31.0~31.3 V | 2026-08-05 晚对齐趴桩, SOC 65~71% |
| **站立未充** | **≈ -2172 mA**（-3692~-2156） | ≈ 31.07 V | 两带之外 |

`power_v` / `bms_status` **不能**可靠区分。`service_state` 无充电标志位。

**主判定**（`calibrated_go2_4g_charge_rules()`）：连续 ≥4s 满足以下**任一**带：

- **带 A**：`-1500 ≤ current ≤ -500` mA
- **带 B**：`7500 ≤ current ≤ 8500` mA

**辅助**（慢, 仅 hint）：SOC 在采样窗口内上升 ≥1 百分点 → `soc_rising_charge_hint()`。
晚场实采 SOC 65%→71% 时带 B 电流仍稳定在 ~8 A, **暂未看到**「电量越少电流越大」的明显规律。

代码：`charge_verify.py` / `jiangtao/scripts/demo_go2_sample_charge_state.py`。

**写代码铁律（导航 / 回充 / 遥操作 / skill 一律遵守）：**

1. 发 4G 摇杆命令时，**任何非零指令不得低于上表硬下限**（安全限速只能往上砍上限，不能把下限砍掉）。
2. 命名里的 `*_mps` / `*_rad_s` 在 Remote 路径上是**历史误名**；文档与注释必须写清是 **摇杆比例**，不要当成 SI 单位调参。
3. 标定脚本：前进 `jiangtao/scripts/demo_go2_forward_calibration.py`；转向 `jiangtao/scripts/demo_go2_rotate_calibration.py`。

```bash
# 导航原地旋转下限（名字带 RAD_S, 实际是摇杆 |rx|）
export DIMOS_ROTATE_MIN_RAD_S=0.2
# 回充模块内对应:
#   RechargeConfig.min_forward_mps = 0.10   # 实际是 |ly|
#   RechargeConfig.min_yaw_rad_s   = 0.20   # 实际是 |rx|
```

## 4G Remote 重定位导航与诊断日志

原来的真机命令仍然适用，但诊断追踪默认关闭。需要记录导航链路时，在命令前增加 `--navigation-trace-level`：

```bash
------------------- 配置环境 ---------------------------------
# 狗三 key / API key 放在 jiangtao/run-self.md（已 gitignore）或本机 .env
export UNITREE_AES_128_KEY='你的AES128密钥'
export OPENAI_API_KEY="你的OpenAI密钥"
export OPENAI_BASE_URL="https://api.deepseek.com"
export DIMOS_ROTATION_STEP_DEG=60
export DIMOS_ROOM_SCAN_ROTATIONS=5
export DIMOS_SEARCH_CONFIRM_CENTER_TOLERANCE_DEG=10.0
# 原地旋转最小摇杆 |rx| (名字带 RAD_S, 实际不是 rad/s); 4G 实测 <0.15 不转, 0.20 起可靠
# 完整死区表见本文档顶部「4G Remote 摇杆死区」
export DIMOS_ROTATE_MIN_RAD_S=0.2

# 云端 VLM fallback (本地不可用时自动切换)
export DIMOS_VLM_CLOUD_API_KEY="你的云端VLM密钥"
export DIMOS_VLM_CLOUD_BASE_URL="https://ws-sy431890c06kqcoz.cn-beijing.maas.aliyuncs.com/compatible-mode/v1"
export DIMOS_VLM_CLOUD_MODEL_NAME="qwen3-vl-plus"


# 账号、密码和序列号只从环境变量读取，不要写入命令或日志
export UNITREE_WEBRTC_METHOD=remote
export UNITREE_USERNAME='你的宇树账号'
export UNITREE_PASSWORD='你的密码'
export UNITREE_SERIAL='B42D........'
export UNITREE_REGION=cn
------------------- 配置环境 ---------------------------------
dimos --navigation-trace-level full run unitree-go2-agentic-deepseek --disable security-module

# 第一步，建图，只跑一遍
dimos --robot-ip 192.168.12.1 run unitree-go2-memory
# 第二部，第一步stop后，解析图
dimos map global recording_go2 --export

# 基于重定位的空间记忆：完整规划、控制链和 costmap 日志
dimos --navigation-trace-level full \
  --unitree-webrtc-method remote \
  --unitree-username "$UNITREE_USERNAME" \
  --unitree-password "$UNITREE_PASSWORD" \
  --unitree-serial "$UNITREE_SERIAL" \
  --unitree-region "$UNITREE_REGION" \
  run unitree-go2-relocalization-memory-agentic-deepseek \
  --disable security-module \
  -o relocalizationmodule.map_file=recording_go2
```

`summary`/`full` 会自动在本次 run 目录下写入：

```text
logs/<run-id>/navigation/
```

停止运行后生成离线报告：

```bash
dimos status
dimos nav analyze logs/<run-id>
```

静止测试可在另一个终端启动只读采集器（不会发布运动指令）：

```bash
uv run python -m dimos.navigation.diagnostics.static_capture \
  /tmp/go2-stationary.json --duration-sec 600 --check
```

原命令不加 `--navigation-trace-level` 时仍能正常运行，但不会创建 `navigation/` 诊断文件。`forensic` 会额外记录点云，须在 `full` 门禁通过后再单独启用。

本机 2026-07-20 验收（4G）：`lidar≈6.9Hz`，`odom≈18Hz`，`color_image≈14Hz`，`global_map` 有输出。
完整流程见：`jiangtao/plan/2026-07-20-Go2-4G远程WebRTC跨电脑完整测试指南.md`。

## 4G Remote 跑 unitree-go2（云 TURN，不改 LAN）

默认仍是 **LocalSTA**（`--robot-ip` / `.env` 的 `ROBOT_IP`），行为与原来一致。
只有显式指定 `unitree_webrtc_method=remote` 才走 4G。

```bash
# 个人账号密码放在 jiangtao/run-self.md（已 gitignore），这里只用占位符
export UNITREE_WEBRTC_METHOD=remote
export UNITREE_USERNAME='你的宇树账号'
export UNITREE_PASSWORD='你的密码'
export UNITREE_SERIAL='B42D........'
export UNITREE_REGION=cn

dimos --viewer none \
  --unitree-webrtc-method remote \
  --unitree-username "$UNITREE_USERNAME" \
  --unitree-password "$UNITREE_PASSWORD" \
  --unitree-serial "$UNITREE_SERIAL" \
  --unitree-region cn \
  run unitree-go2

# 或后台：末尾加 --daemon
```

LAN 用法不变：

```bash
dimos --robot-ip 192.168.12.1 run unitree-go2
# 或依赖 .env 里的 ROBOT_IP / UNITREE_AES_128_KEY
```

---



## Step 1: 录制（真机遛一圈）

```bash
dimos --robot-ip 192.168.12.1 run unitree-go2-memory
# 生成 recording_go2.db
```



## Step 2: 离线 PGO 导出 premap

前置：需安装 `gtsam-extended`（`uv pip install "gtsam-extended>=4.3a1.post1"` 或 `uv sync --extra mapping`）。

```bash
dimos map global recording_go2 --export
# 生成 ./recording_go2.pc2.lcm
# CUDA 异常时加 --device CPU:0
```



## Step 3: 重定位导航

回放测试：

```bash
dimos --replay --replay-db recording_go2 run unitree-go2-relocalization \
  -o relocalizationmodule.map_file=recording_go2
```

真机：

```bash
dimos --robot-ip 192.168.12.1 run unitree-go2-relocalization \
  -o relocalizationmodule.map_file=recording_go2
```

---



## 数据解析脚本

```bash
# 完整解析 (首次运行, 约 3 分钟)
python jiangtao/scripts/parse_recording_db.py

# 只重新生成点云 (图像和 odom 已有)
python jiangtao/scripts/parse_recording_db.py --skip-imgs --skip-odom

# 更改体素大小
python jiangtao/scripts/parse_recording_db.py --skip-imgs --skip-odom --voxel 0.03
```



## 可视化脚本

```bash
# 可视化地图 (保存图片)
python jiangtao/scripts/visualize_global_map.py --save output.png

# 可视化地图 (交互式, 需要显示器)
python jiangtao/scripts/visualize_global_map.py --backend open3d

# 去掉地面 (Z < 0)
python jiangtao/scripts/visualize_global_map.py --z-min 0 --save output.png

# 只看某个高度区间, 比如 0.2m ~ 0.8m (桌腿/椅子高度)
python jiangtao/scripts/visualize_global_map.py --z-min 0.2 --z-max 0.8 --save output.png

# 俯视图
python jiangtao/scripts/visualize_global_map.py --save topview.png --elevation 90 --azimuth -90
```



## 开机 odom 一致性诊断

同一物理位置固定 T 重定位多次结果不同时, 用此脚本验证 odom 开机原点是否漂移:

```bash
# 终端 A: 已启动 unitree-go2-relocalization
# 终端 B: 狗站定, 开机后尽快录
.venv/bin/python jiangtao/scripts/record_boot_consistency.py record \
  --mode lcm --duration 25 --tag boot1

# 需要 IMU / Unitree 原始 frame_id 时 (不要同时开 blueprint)
.venv/bin/python jiangtao/scripts/record_boot_consistency.py record \
  --mode webrtc --robot-ip 192.168.12.1 --duration 20 --tag boot1

# 对比多次
.venv/bin/python jiangtao/scripts/record_boot_consistency.py compare \
  jiangtao/cache/boot_consistency/boot1_* \
  jiangtao/cache/boot_consistency/boot2_*
```

输出目录: `jiangtao/cache/boot_consistency/<tag>_<time>_<mode>/`
关键看 `summary.json` 的 `odom_first` / `lidar_base_*` / `global_map_first`.

分析报告（含 odom 来源、0.31°/s 漂移、对建图/固定 T 的影响）:
`jiangtao/20260706-开机odom一致性与yaw漂移分析报告.md`

## odom txt 格式

每行一个值，共 11 行：


| 行   | 字段        | 说明         |
| --- | --------- | ---------- |
| 1   | timestamp | Unix 时间戳   |
| 2   | x         | 世界坐标 X (m) |
| 3   | y         | 世界坐标 Y (m) |
| 4   | z         | 世界坐标 Z (m) |
| 5   | qx        | 四元数 x      |
| 6   | qy        | 四元数 y      |
| 7   | qz        | 四元数 z      |
| 8   | qw        | 四元数 w      |
| 9   | yaw       | 偏航角 (rad)  |
| 10  | pitch     | 俯仰角 (rad)  |
| 11  | roll      | 翻滚角 (rad)  |


---



## Step 4: 空间找物（单次会话, 无 premap）

环境变量：

```bash
# 二狗 / 三狗 key 见 jiangtao/run-self.md
export UNITREE_AES_128_KEY='你的AES128密钥'

export OPENAI_API_KEY="你的OpenAI密钥"
export OPENAI_BASE_URL="https://api.deepseek.com"
export DIMOS_VLM_API_KEY="EMPTY"
export DIMOS_VLM_BASE_URL="http://10.10.153.172:8080/v1"
export DIMOS_VLM_MODEL_NAME="/Users/dijia/models/Qwen3-VL-8B-4bit"
export DIMOS_ROTATION_STEP_DEG=60
export DIMOS_ROOM_SCAN_ROTATIONS=6

# 云端 VLM fallback (本地不可用时自动切换)
export DIMOS_VLM_CLOUD_API_KEY="你的云端VLM密钥"
export DIMOS_VLM_CLOUD_BASE_URL="https://ws-sy431890c06kqcoz.cn-beijing.maas.aliyuncs.com/compatible-mode/v1"
export DIMOS_VLM_CLOUD_MODEL_NAME="qwen3-vl-plus"
export DIMOS_VLM_FALLBACK_COOLDOWN=60
# 调整真机速度
export DIMOS_NAV_SPEED=0.5

# unset 代理是为了避免局域网请求(Go2 WebRTC信令 / 本地VLM)走代理导致连接失败
# 云端VLM走公网, 如果机器没有直连能力需要保留代理, 改用 NO_PROXY 排除局域网IP即可
unset ALL_PROXY all_proxy HTTP_PROXY http_proxy HTTPS_PROXY https_proxy
```

真机：

```bash
dimos --robot-ip 10.206.176.64 run unitree-go2-agentic-deepseek --disable security-module
```

找物交互：

```bash
dimos tell '标记一下当前是办公室'
dimos tell '去找饮水机'
```



## Step 5: 重定位空间找物（基于 premap）

前置：已完成 Step 1 ~ Step 2, 生成了 `recording_go2.pc2.lcm`。

环境变量同 Step 4。

### 启动时记忆（`new_memory`，默认清空）

重启后 **odom 原点会重置**，但磁盘上的 `landmarks.json` / CLIP 房间图若残留，CLIP 仍可能认出旧房间名（如「办公室」），再和旧坐标交叉验证就会报坐标过期、拒绝导航。

因此 `GlobalConfig.new_memory` **默认** `True`：启动时自动清空

- `~/.local/state/dimos/landmark_memory/landmarks.json` 与 `snapshots/`
- SpatialMemory 的 CLIP / Chroma 持久化库

**一般不必再手动** `rm`**。** 启动日志应看到：

```text
Cleared landmark memory on start (new_memory=True, ...)
```

而不是 `Loaded N landmark(s) from ...`。

需要跨次运行保留地标时：

```bash
dimos --no-new-memory --robot-ip 10.10.155.110 \
  run unitree-go2-relocalization-memory-agentic-deepseek \
  --disable security-module \
  -o relocalizationmodule.map_file=recording_go2

# 或在 .env 里：
# DIMOS_NEW_MEMORY=false
```

真机（默认清空记忆）：

```bash
dimos --robot-ip 10.10.155.110 run unitree-go2-relocalization-memory-agentic-deepseek \
  --disable security-module \
  -o relocalizationmodule.map_file=recording_go2
```

找物交互：

```bash
dimos tell '标记一下当前是办公室'
dimos tell '去找饮水机'
```

图片保存位置（本 session 标记后才会重新生成）：

```
~/.local/state/dimos/landmark_memory/
├── landmarks.json             ← 地标记录（名称、位置、房间等元数据）
└── snapshots/
    └── <record_id>.jpg        ← 每个地标的 JPG 快照
```



## Step 6: 可选沿途 VLM 寻物（默认关闭）

环境变量必须在启动 blueprint 之前设置：

```bash
export DIMOS_ENROUTE_OBJECT_SEARCH_ENABLED=true
dimos --robot-ip 10.206.176.64 run unitree-go2-relocalization-memory-agentic-deepseek \
  --disable security-module \
  -o relocalizationmodule.map_file=recording_go2
```

找物交互保持不变：

```bash
dimos tell '去找饮水机'
```



### 沿途寻物调参（可选）

不设置时使用右侧默认值：

```bash
# 两次沿途 VLM 请求的最短间隔
export DIMOS_SEARCH_VLM_INTERVAL_S=0.8

# 离开每段导航起点至少 0.20m 后才开始检测，避免检测任务起始位置
export DIMOS_SEARCH_START_DISPLACEMENT_M=0.20

# 图片时间戳与 odom 时间戳的最大允许误差
export DIMOS_SEARCH_POSE_SYNC_TOLERANCE_S=0.20

# VLM 结果返回时，图片允许的最大年龄
export DIMOS_SEARCH_MAX_RESULT_AGE_S=8.0

# 已走出拍照位置超过 0.40m 时，重新规划返回该观察位置
export DIMOS_SEARCH_REWIND_THRESHOLD_M=0.40

# 保留的 odom 历史时长，需要覆盖可能的 VLM 延迟
export DIMOS_SEARCH_ODOM_BUFFER_S=15.0

# 命中后等待旧导航 goal 释放控制权的最长时间
export DIMOS_SEARCH_CANCEL_WAIT_S=2.0

# 返回图片拍摄观察位置的导航超时
export DIMOS_SEARCH_REWIND_TIMEOUT_S=120.0

# 每次转向结束后，先等待 0.3 秒让机器人和视频管线稳定
export DIMOS_SEARCH_CONFIRM_SETTLE_S=0.3

# 稳定窗口结束后至少跨过 1 个新的相机回调，禁止复用缓存旧帧
export DIMOS_SEARCH_CONFIRM_MIN_NEW_FRAMES=1

# 稳定窗口结束后，等待合格新相机帧的最长时间
export DIMOS_SEARCH_CONFIRM_FRAME_TIMEOUT_S=2.0

# 二次确认最多检查 2 帧；首帧偏离中心时会转向并用下一帧复核
export DIMOS_SEARCH_CONFIRM_MAX_CHECKS=2

# 最终确认时，目标 bbox 中心允许偏离相机中心的最大角度
export DIMOS_SEARCH_CONFIRM_CENTER_TOLERANCE_DEG=10.0

# 导航线速度 (m/s, 范围 0.2~2.5, 真机默认 0.7); 沿途寻物时建议降速,
# 减小运动模糊和 image/odom 同步误差, 提高 VLM 检测与拍照位姿的准确性
export DIMOS_NAV_SPEED=0.5
```

最终物体确认采用关闭失败策略：新帧超时、VLM 未命中、bbox 不合理或两次
检查后仍未居中时，都不会回复“找到”或执行打招呼动作，而是继续扫描当前房间
或进入后续寻物 fallback。单轮新帧等待上限约为 `0.3 + 2.0` 秒；不会在超时后
降级复用旧画面。

建议首轮真机测试只设置总开关，其他参数先使用默认值：

```bash
export DIMOS_ENROUTE_OBJECT_SEARCH_ENABLED=true
```
