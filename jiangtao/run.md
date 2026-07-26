# Go2 录制与导航流程

> 环境：在 `topsun_dimos` 根目录执行 `deactivate 2>/dev/null; source .venv/bin/activate`，确认 `echo $VIRTUAL_ENV` 以 `topsun_dimos/.venv` 结尾。详见 `jiangtao/bugfix.md` §9/§13。

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

## 4G Remote 重定位导航与诊断日志

原来的真机命令仍然适用，但诊断追踪默认关闭。需要记录导航链路时，在命令前增加 `--navigation-trace-level`：

```bash
# 账号、密码和序列号只从环境变量读取，不要写入命令或日志
export UNITREE_WEBRTC_METHOD=remote
export UNITREE_USERNAME='你的宇树账号'
export UNITREE_PASSWORD='你的密码'
export UNITREE_SERIAL='B42D........'
export UNITREE_REGION=cn

# Gate 3 静止测试：低负载摘要日志
dimos --viewer none \
  --navigation-trace-level summary \
  --unitree-webrtc-method remote \
  --unitree-username "$UNITREE_USERNAME" \
  --unitree-password "$UNITREE_PASSWORD" \
  --unitree-serial "$UNITREE_SERIAL" \
  --unitree-region "$UNITREE_REGION" \
  run unitree-go2-relocalization-memory-agentic-deepseek \
  --disable security-module \
  -o relocalizationmodule.map_file=recording_go2

# 低速/路线诊断：完整规划、控制链和 costmap 日志
# 仅在 summary 静止门禁通过后使用
dimos --viewer none \
  --navigation-trace-level full \
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
# 二狗
export UNITREE_AES_128_KEY='0a7d97828984ec332984571244318f30'
# 三狗
export UNITREE_AES_128_KEY='a84c9f3171fb9ceaa1629ec0d3a82031'

export OPENAI_API_KEY="sk-b1c3c485844d49a6a2782a20a790ba20"
export OPENAI_BASE_URL="https://api.deepseek.com"
export DIMOS_VLM_API_KEY="EMPTY"
export DIMOS_VLM_BASE_URL="http://10.10.153.172:8080/v1"
export DIMOS_VLM_MODEL_NAME="/Users/dijia/models/Qwen3-VL-8B-4bit"
export DIMOS_ROTATION_STEP_DEG=60
export DIMOS_ROOM_SCAN_ROTATIONS=6

# 云端 VLM fallback (本地不可用时自动切换)
export DIMOS_VLM_CLOUD_API_KEY="sk-ws-H.EDDREER.p3xq.MEUCIQDdBEwnddKuZEg2EXYSeqpWRBGlATod78ixRpzjHrew8wIgH0sJMDzpuZJdyORYcnsOQLDuP5gpF5ZDqfnW_WDpbto"
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

因此 `GlobalConfig.new_memory` **默认 `True`**：启动时自动清空

- `~/.local/state/dimos/landmark_memory/landmarks.json` 与 `snapshots/`
- SpatialMemory 的 CLIP / Chroma 持久化库

**一般不必再手动 `rm`。** 启动日志应看到：

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

# 回到观察位置后等待新相机帧的最长时间
export DIMOS_SEARCH_CONFIRM_FRAME_TIMEOUT_S=3.0

# 二次确认最多检查 2 帧；首帧偏离中心时会转向并用下一帧复核
export DIMOS_SEARCH_CONFIRM_MAX_CHECKS=2

# 最终确认时，目标 bbox 中心允许偏离相机中心的最大角度
export DIMOS_SEARCH_CONFIRM_CENTER_TOLERANCE_DEG=5.0

# 导航线速度 (m/s, 范围 0.2~2.5, 真机默认 0.7); 沿途寻物时建议降速,
# 减小运动模糊和 image/odom 同步误差, 提高 VLM 检测与拍照位姿的准确性
export DIMOS_NAV_SPEED=0.5
```

建议首轮真机测试只设置总开关，其他参数先使用默认值：

```bash
export DIMOS_ENROUTE_OBJECT_SEARCH_ENABLED=true
```
