# Go2 真机连接与 unitree-go2-memory 问题记录

> **知识库说明**：环境与配置类问题统一记录在此。Agent 规则见 `.cursor/rules/jiangtao-bugfix-knowledge.mdc` — 遇到类似问题**先查本文件**，解决后**回写本文件**。

## 问题索引

| 日期 | 关键词 | 章节 |
|------|--------|------|
| 2026-06-30 | 数据路径、gitignore、recording_go2.db | [§1](#1-录制数据与数据库文件位置) |
| 2026-06-30 | AES key、WebRTC、data2=3、ROBOT_IP | [§2](#2-go2-真机-webrtc-连接aes-key--ip) |
| 2026-06-30 | unitree-webrtc-connect、aes_128_key | [§3](#3-unitree-webrtc-connect-版本过旧) |
| 2026-06-30 | numpy 2.5、numba 冲突 | [§4](#4-numpy-25-与-numba-冲突) |
| 2026-06-30 | dimos CLI、--robot-ip 位置、env 优先级 | [§5](#5-cli-启动命令写法) |
| 2026-06-30 | LCM 多播、lo、224.0.0.0/4、sudo | [§6](#6-lcm-多播路由未配置启动失败) |
| 2026-06-30 | Rerun 雷达隐藏、world/lidar、max_hz | [§7](#7-没有雷达数据--实为-rerun-图层隐藏) |
| 2026-06-30 | max_hz 不影响录制 | [§8](#8-max_hz-不影响录制) |
| 2026-06-30 | venv 路径、gitlab/dimos editable | [§9](#9-环境与路径备忘) |
| 2026-06-30 | gtsam、map global、PGO、mapping extra | [§11](#11-dimos-map-global-缺少-gtsam) |
| 2026-07-10 | uv.lock 重复包、uv sync 失败 | [§12](#12-uvlock-合并冲突导致重复包) |
| 2026-07-10 | zenoh、activate 指向 gitlab venv | [§13](#13-source-activate-后仍用-gitlab-venvzenoh-缺失) |

---

> 记录时间：2026-06-30
> 机器：Go2_59385，SN `B42D2000Q4O8LD03`，IP `192.168.8.46`
> Blueprint：`unitree-go2-memory`

---

## 1. 录制数据与数据库文件位置

### 现象

- 在 git 视图里找不到 `jiangtao/data/`，以为解析结果丢了。

### 原因

- `.gitignore` 包含 `/jiangtao/data/`，数据在磁盘上存在但不被 git 跟踪。
- 解析输出默认目录：`jiangtao/data/20260626-recording_go2/`（`imgs/`、`odom/`、`pcd/`）。
- 原始 `recording_go2.db` 最初只在 `gitlab/dimos/recording_go2.db`（1.9 GB），`topsun_dimos` 根目录没有。

### 处理

```bash
cp /home/jiangtao/huazhijian/gitlab/dimos/recording_go2.db \
   jiangtao/data/20260626-recording_go2/recording_go2.db
```

### 解析命令

```bash
python jiangtao/scripts/parse_recording_db.py \
  --db jiangtao/data/20260626-recording_go2/recording_go2.db \
  --out jiangtao/data/20260626-recording_go2
```

---

## 2. Go2 真机 WebRTC 连接（AES key + IP）

### 现象

- 固件 ≥ 1.1.15 的机器 `con_notify` 返回 `data2: 3`，不带 per-device key 时 WebRTC 握手失败。

### 配置（`.env`）

```env
ROBOT_IP=192.168.8.46
UNITREE_AES_128_KEY=<32位十六进制，每台机器不同>
NO_PROXY=192.168.8.46,...,localhost,127.0.0.1
```

### 验证

```bash
dimos show-config | grep -E 'robot_ip|unitree_aes'
ping 192.168.8.46
```

`con_notify` 检查：

```bash
python3 - <<'PY'
import base64, json, requests
r = requests.get("http://192.168.8.46:9991/con_notify", timeout=5)
print(json.loads(base64.b64decode(r.text).decode()))
PY
# data2 == 3 → 必须配置 UNITREE_AES_128_KEY
```

---

## 3. `unitree-webrtc-connect` 版本过旧

### 现象

```
UnitreeWebRTCConnection.__init__() got an unexpected keyword argument 'aes_128_key'
```

或连接时 `RSA key format is not supported`（旧包不支持 `data2=3`）。

### 原因

venv 里装的是不含 `aes_128_key` 参数的旧构建，与 dimos 源码预期不一致。

### 修复

```bash
source .venv/bin/activate
uv pip install --force-reinstall "unitree-webrtc-connect==2.1.2"
```

确认：

```bash
python3 -c "import inspect; from unitree_webrtc_connect.webrtc_driver import UnitreeWebRTCConnection; print(inspect.signature(UnitreeWebRTCConnection.__init__))"
# 应包含 aes_128_key 参数
```

---

## 4. NumPy 2.5 与 Numba 冲突

### 现象

```
ImportError: Numba needs NumPy 2.3 or less. Got NumPy 2.5.
```

发生在 `dimos run unitree-go2-memory` 加载 `CostMapper` → `numba` 时。

### 原因

重装 `unitree-webrtc-connect` 时把 `numpy` 升到了 2.5.0。

### 修复

```bash
uv pip install "numpy>=1.26.4,<2.4"
# 当前可用组合：numpy 2.3.5 + numba 0.63.1
```

> **注意**：以后升级 `unitree-webrtc-connect` 后建议立刻 pin numpy，避免再次踩坑。

---

## 5. CLI 启动命令写法

### 现象

```bash
dimos run unitree-go2-memory --robot-ip 192.168.8.46
# Error: No such option '--robot-ip'
```

### 正确写法

`--robot-ip` 是 **dimos 全局参数**，必须写在 `run` **前面**：

```bash
dimos --robot-ip 192.168.8.46 run unitree-go2-memory
```

`.env` 已配置 `ROBOT_IP` 时可省略：

```bash
dimos run unitree-go2-memory
```

### 优先级

```
默认值 < .env < shell 环境变量 < 命令行全局参数（最高）
```

命令行里的 `--robot-ip` **会覆盖** `.env`；写在 `run` 后面则**完全无效**。

---

## 6. LCM 多播路由未配置（启动失败）

### 现象

```
sudo ip link set lo multicast on
# Critical fix failed — sudo: 需要密码
```

dimos 启动前 `MulticastConfiguratorLinux` 检查失败，进程退出。

### 原因

本机 `lo` 未开 `MULTICAST`，且无 `224.0.0.0/4` 路由。LCM 依赖 loopback 多播做进程间通信。

### 一次性修复（需管理员权限）

```bash
sudo ip link set lo multicast on
sudo ip route add 224.0.0.0/4 dev lo
```

验证：

```bash
ip -o link show lo | grep MULTICAST
ip route show 224.0.0.0/4
```

> 重启后若失效需再执行一次；可考虑写进开机脚本或 NetworkManager 配置。

---

## 7. 「没有雷达数据」— 实为 Rerun 图层隐藏

### 现象

用户反馈 Rerun / 界面看不到雷达点云。

### 排查结论

数据链路**正常**：

| 检查项 | 结果 |
|--------|------|
| `recording_go2.db` → `lidar` 表 | 有数据，每帧约 2.5–4 万点 |
| LCM `/lidar` | ~7–8 Hz |
| LCM `/global_map` | 体素地图持续更新 |
| LCM `/global_costmap` | 代价地图有内容 |

### 根因

`dimos/robot/unitree/go2/blueprints/basic/unitree_go2_basic.py` 中 Rerun blueprint 默认：

```python
overrides={
    "world/lidar": rrb.EntityBehavior(visible=False),  # 雷达图层被隐藏
}
```

### 修复

- 删除上述 `visible=False`，让 `world/lidar` 默认可见。
- 增加 `"world/lidar": 2` 的 `max_hz`（仅限制 Rerun 显示帧率，见第 8 节）。

修改后需 **重启 dimos** 才生效：

```bash
dimos stop
dimos --robot-ip 192.168.8.46 run unitree-go2-memory
```

### 其他说明

- `localhost:7779` Web 面板只显示 **costmap**，不显示原始雷达点云；点云请看 **Rerun 3D 视图**。
- 连接后前几帧点较少（解码预热），遛几步后恢复正常。
- `dimos topic echo /lidar` 因单帧较大（~2.5 万点），可能几秒才打印一条，不代表没数据。

---

## 8. `max_hz` 不影响录制

### 问题

给 `world/lidar` 加了 `max_hz: 2`，录制会不会被限帧？

### 答案

**不会。** 限帧只在 `RerunBridgeModule` 画图时生效。

```
GO2Connection ──► LCM /lidar ──┬──► Go2Memory（recording_go2.db）  全帧率
                               ├──► VoxelGridMapper（建图）         全帧率
                               └──► RerunBridge（显示）            max_hz 限 2Hz
```

---

## 9. 环境与路径备忘

| 项 | 值 |
|----|-----|
| 工作目录 | `topsun_dimos` |
| venv 路径 | `topsun_dimos/.venv`（**必须用这个**，不要用 `gitlab/dimos/.venv`） |
| editable 安装源 | `topsun_dimos`（`__editable__.dimos-*.pth` 指向本仓库） |
| 依赖安装 | `uv sync --extra all`（在 `topsun_dimos` 根目录） |
| 日志目录 | `topsun_dimos/logs/<run-id>/main.jsonl`（在 `topsun_dimos` 目录下 `dimos run` 时；见 `resolve_log_dir()`） |

### 激活后自检（每次换机 / 合并上游后建议跑一遍）

```bash
cd /home/jiangtao/huazhijian/topsun-bot/topsun_dimos
deactivate 2>/dev/null || true   # 若之前激活过 gitlab venv，先退出
source .venv/bin/activate
echo "$VIRTUAL_ENV"              # 应输出 .../topsun_dimos/.venv
which python dimos               # 两个都应在 topsun_dimos/.venv/bin 下
python -c "import zenoh; print('zenoh ok')"
```

### 常用运维命令

```bash
dimos status
dimos log -f
dimos stop
dimos show-config
```

---

## 10. 推荐启动流程（汇总）

```bash
cd /home/jiangtao/huazhijian/topsun-bot/topsun_dimos
deactivate 2>/dev/null || true
source .venv/bin/activate
# 确认用的是 topsun venv，不是 gitlab/dimos
test "$VIRTUAL_ENV" = "$(pwd)/.venv"

# 1. 确认 numpy 版本（若刚升级过 webrtc 包）
python3 -c "import numpy; print(numpy.__version__)"   # 当前 lock 为 2.3.x

# 2. 确认 LCM 多播（首次或重启后）
ip route show 224.0.0.0/4

# 3. 启动录制
dimos --robot-ip 192.168.8.46 run unitree-go2-memory
# 或后台：加 --daemon

# 4. 遛完后停止
dimos stop
```

---

## 11. `dimos map global` 缺少 gtsam

### 现象

```bash
dimos map global recording_go2 --export
# ...
ModuleNotFoundError: No module named 'gtsam'
```

`--export` 会隐式启用 `--pgo`（位姿图优化），依赖 `gtsam-extended`。

### 原因

venv 只装了基础依赖，未装 `mapping` extra。

### 修复

```bash
source .venv/bin/activate
uv pip install "gtsam-extended>=4.3a1.post1"
# 或一次性装 mapping 相关依赖：
uv sync --extra mapping
```

验证：

```bash
python3 -c "import gtsam; print('ok')"
dimos map global recording_go2 --export --device CPU:0
```

> 若 CUDA 初始化失败（`CUDA unknown error`），加 `--device CPU:0`。

### 输出

- `recording_go2.pc2.lcm` — premap，供 `unitree-go2-relocalization` 使用
- `recording_go2.rrd` — Rerun 可视化

---

## 12. `uv.lock` 合并冲突导致重复包

### 现象

```bash
uv sync
# error: Failed to parse `uv.lock`
# Caused by: Dependency `pin-pink` has missing `source` field but has more than one matching package
```

### 原因

合并 `upstream/main` 时 `uv.lock` 里同一 `name+version` 的 `[[package]]` 块被重复写入（`pin-pink`、`curl-cffi`、`msgspec` 等 6 组）。

### 修复

删除重复块后 `uv lock` 校验，再重装：

```bash
cd /home/jiangtao/huazhijian/topsun-bot/topsun_dimos
uv lock    # 应 Resolved ... in 1ms
unset VIRTUAL_ENV
rm -rf .venv
uv sync --extra all
```

### 验证

```bash
uv lock
python3 -c "import zenoh; print('ok')"
```

---

## 13. `source activate` 后仍用 gitlab venv（zenoh 缺失）

### 现象

在 `topsun_dimos` 里 `source .venv/bin/activate` 后运行：

```bash
dimos --robot-ip 192.168.12.1 run unitree-go2-memory
# ModuleNotFoundError: No module named 'zenoh'
```

`which python` 显示 `/home/jiangtao/huazhijian/gitlab/dimos/.venv/bin/python`。

### 原因

`topsun_dimos/.venv` 曾从 `gitlab/dimos/.venv` 复制而来，`activate` 脚本和 150+ 个 bin shebang 仍硬编码 `gitlab/dimos/.venv` 路径。激活后 PATH 指向旧 venv，而旧 venv 没有 `eclipse-zenoh`（上游 merge 后新增依赖）。

### 修复

**彻底重建本仓库 venv**（不要复制 gitlab 的）：

```bash
cd /home/jiangtao/huazhijian/topsun-bot/topsun_dimos
deactivate 2>/dev/null || true
unset VIRTUAL_ENV
rm -rf .venv
uv sync --extra all
```

### 验证

```bash
source .venv/bin/activate
echo "$VIRTUAL_ENV"    # .../topsun_dimos/.venv
which dimos            # .../topsun_dimos/.venv/bin/dimos
python -c "import zenoh; print('ok')"
dimos --replay --replay-db go2_short --viewer none run unitree-go2-basic --daemon
dimos stop
```

> **注意**：`gitlab/dimos/.venv` 与 `topsun_dimos/.venv` 是两套环境。在 topsun 仓库工作时只用后者；若 shell 里已激活 gitlab venv，先 `deactivate` 再激活 topsun。

---

> 本文档对应真机联调与环境踩坑记录，后续固件 / 依赖升级后细节可能变化，但整体链路应保持稳定。
