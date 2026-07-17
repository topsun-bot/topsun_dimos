# Go2 + Mid-360 onboard 导航 — 现场 runbook

> 拓展坞（Jetson Orin NX 16GB）上跑 `unitree-go2-nav-onboard` 蓝图的部署、启停、排障与回滚。
>
> 关联文档：
> - 计划与决策：[`jiangtao/plan/plan.md`](../plan/plan.md)
> - 技术原理：[`jiangtao/plan/dimos-go2-mid360-nav-stack.md`](../plan/dimos-go2-mid360-nav-stack.md)
> - 评估意见：[`jiangtao/plan/Go2与Mid-360新导航栈最终开发说明.md`](../plan/Go2%E4%B8%8EMid-360%E6%96%B0%E5%AF%BC%E8%88%AA%E6%A0%88%E6%9C%80%E7%BB%88%E5%BC%80%E5%8F%91%E8%AF%B4%E6%98%8E.md)
>
> 受众：值班工程师、现场操作员、新接手部署的同事。
>
> 假设：你已 SSH 到拓展坞 `unitree@<拓展坞-IP>`，并能用 `bash` 操作。

---

## 目录

1. [物理接线图](#1-物理接线图)
2. [拓展坞首次安装](#2-拓展坞首次安装)
3. [仓库部署](#3-仓库部署)
4. [C++ 二进制 build](#4-c-二进制-build)
5. [网络配置](#5-网络配置)
6. [Mid-360 规格（对外说明）](#6-mid-360-规格对外说明)
7. [ENV 变量清单](#7-env-变量清单)
8. [启动 / 停止 / 状态查询](#8-启动--停止--状态查询)
9. [笔记本看 viewer](#9-笔记本看-viewer)
10. [回滚命令](#10-回滚命令)
11. [systemd unit](#11-systemd-unit)
12. [常见故障速查](#12-常见故障速查)
13. [升级流程](#13-升级流程)
14. [merge gate 验收清单（值班工程师每天打勾）](#14-merge-gate-验收清单值班工程师每天打勾)

---

## 1. 物理接线图

```
            +----------+   USB-Eth   +----------+
            |  Mid-360 |─────────────│  拓展坞    │── WiFi ────→ 公司网络（10.10.197.46）
            |  (192.168|             | Orin NX  │
            |  .123.20)|             | 16GB     │
            +----------+             |  go2eth  │── 直连有线 ──→ Go2 主控板（192.168.123.161）
                                     |  (.123.18)|
                                     +----------+
```

**关键点**：
- 拓展坞通过 **go2eth** 接 Go2 内网（`192.168.123.0/24`），mid-360 和 Go2 共用这个网段。
- 拓展坞 **wlan0** 接公司 WiFi，仅用于 SSH / rerun-web / 偶尔装包。
- mid-360 数据靠 UDP 单播打到拓展坞 `192.168.123.18`，**笔记本 / 别的机器收不到**。

---

## 2. 拓展坞首次安装

### 2.1 系统环境

| 项 | 值 |
|---|---|
| OS | Ubuntu 20.04 LTS aarch64（Jetson 自带） |
| Python | 3.12（uv 自动下载到 `~/.local/share/uv/python/`，**不用 apt 装**） |
| 包管理 | `uv` (≥ 0.10.9) |
| Git LFS | `sudo apt install git-lfs && git lfs install` |
| Nix（C++ build 用） | `sh <(curl -L https://nixos.org/nix/install) --daemon` |

### 2.2 必要工具

```bash
# 系统层
sudo apt update
sudo apt install -y git git-lfs curl rsync nmcli tcpdump

# uv（已装可跳过）
which uv || curl -LsSf https://astral.sh/uv/install.sh | sh
```

---

## 3. 仓库部署

### 3.1 从笔记本同步代码（首次）

笔记本上：

```bash
# 笔记本起反向 SSH 隧道，把笔记本 Clash 代理 7890 暴露给拓展坞（装包用）
ssh -R 7890:127.0.0.1:7890 unitree@<拓展坞-IP> &

# 在另一个终端 rsync 仓库（含 .git/）
cd /path/to/topsun_dimos
rsync -az --info=progress2 \
  --exclude='.venv/' --exclude='__pycache__/' --exclude='*.pyc' \
  --exclude='node_modules/' --exclude='build/' --exclude='dist/' --exclude='result' \
  --exclude='*.egg-info/' --exclude='.pytest_cache/' --exclude='.cache/' \
  ./ unitree@<拓展坞-IP>:/home/unitree/jiangtao/topsun_dimos/
```

**注意**：**必须包含 `.git/`**——否则 dimos 启动时会去 clone github（拓展坞外网受限会失败）。

### 3.2 拓展坞装依赖

拓展坞上（笔记本侧已起 ssh -R 反向隧道）：

```bash
cd /home/unitree/jiangtao/topsun_dimos

# 通过笔记本代理装包（首次需要外网）
HTTPS_PROXY=http://127.0.0.1:7890 HTTP_PROXY=http://127.0.0.1:7890 \
  uv sync --group tests \
          --extra unitree --extra navigation --extra apriltag \
          --extra agents --extra perception --extra visualization --extra web

# 验证
.venv/bin/dimos --help
```

> **跳过的 extra**：`manipulation`（pyrealsense2 ARM64 无 wheel）、`dds` / `unitree-dds`（cyclonedds 系统库未装）、`cuda`、`sim`（warp-lang ARM64+glibc 2.31 不兼容）。**这些 extra 对 nav 路线不需要**。

### 3.3 网络环境快速检查

```bash
bash jiangtao/scripts/check_network.sh
# 期望全部 7 项 OK
```

---

## 4. C++ 二进制 build

`unitree-go2-nav-onboard` 蓝图依赖以下三个 C++ NativeModule，**每次 build 一次即可**：

| 二进制 | 用途 | build 命令 |
|---|---|---|
| `mid360_native` | Livox SDK2 数据驱动（蓝图未直接用，仅独立调试时需要） | `cd dimos/hardware/sensors/lidar/livox/cpp && nix build .#mid360_native` |
| `fastlio2_native` | FAST-LIO2 SLAM 主流程（含嵌入式 Livox SDK2） | `cd dimos/hardware/sensors/lidar/fastlio2/cpp && nix build .#fastlio2_native` |
| `pgo` | iSAM2 + ICP loop closure | `cd dimos/navigation/nav_stack/modules/pgo/cpp && nix build .#default --no-write-lock-file` |

> Nix flake 中的远程依赖（FAST-LIO-NON-ROS、Livox SDK2）需要外网。如果拓展坞外网受限，**保持 ssh -R 反向隧道并 export HTTPS_PROXY**：
>
> ```bash
> export HTTPS_PROXY=http://127.0.0.1:7890
> nix build ... --extra-experimental-features 'nix-command flakes'
> ```

### 4.1 nav_stack 其他 NativeModule（首次启动自动远程下载）

这些不在 dimos 仓库内，由 Nix flake 远程拉：

| 模块 | 远程 flake |
|---|---|
| TerrainAnalysis | `github:dimensionalOS/dimos-module-terrain-analysis/v0.1.1` |
| LocalPlanner | `github:dimensionalOS/dimos-module-local-planner/v0.6.0` |
| FarPlanner | `github:dimensionalOS/dimos-module-far-planner/v0.5.0` |
| PathFollower | `github:dimensionalOS/dimos-module-path-follower/v0.2.0` |

**首次启动会自动 nix build**，需要外网；之后走本地 `~/.cache/nix` 缓存。建议**离线之前预先跑一次 `dimos run unitree-go2-nav-onboard --help`** 让所有 build_command 触发。

---

## 5. 网络配置

### 5.1 拓展坞 LAN 网卡（go2eth）

go2eth 必须配 `192.168.123.18/24`，否则 FastLio2 启动时 `_validate_network()` 会拒绝。

```bash
ip -br addr show go2eth
# 应输出：go2eth UP 192.168.123.18/24 ...

# 如果没配（出厂未配），手动加：
sudo nmcli con add type ethernet ifname go2eth con-name go2-lan \
    ipv4.addresses 192.168.123.18/24 ipv4.method manual
sudo nmcli con up go2-lan
```

### 5.2 Mid-360 IP 设置

mid-360 出厂默认 IP 是 `192.168.1.155`。拓展坞这套已统一改成 `192.168.123.20`。

**改 mid-360 IP 的步骤**（仅首次部署或换雷达时需要）：

1. 把笔记本临时配在 `192.168.1.5/24` 网段，直连 mid-360。
2. 用 [Livox Viewer](https://www.livoxtech.com/downloads) 连上 mid-360。
3. 在工具里把 mid-360 IP 改成 `192.168.123.20`、netmask `255.255.255.0`、gateway `192.168.123.1`。
4. 重启 mid-360。
5. 拔回拓展坞，验证 `ping 192.168.123.20` 通。

### 5.3 Go2 IP 自动发现

不设 `ROBOT_IP` 时，dimos `jtlinux` 分支 commit `81e9f144` 的自动发现会扫 LAN 上的 Go2，命中 `192.168.123.161`。可手动验证：

```bash
.venv/bin/dimos go2tool discover
# 应列出 SOURCE / NAME / IP / MAC / SERIAL，含 192.168.123.161
```

---

## 6. Mid-360 规格（对外说明）

| 项 | 值 |
|---|---|
| 类型 | Livox Mid-360（非重复扫描固态 LiDAR） |
| FOV | 360° × 59° |
| 点云密度 | 40-line（200K pts/s） |
| 探测距离 | 40 m @ 10% 反射率 |
| 最近距离 | 0.1 m |
| 内置 IMU | 6 轴 @ 200 Hz |
| 重量 | 265 g |
| 接口 | 100M USB-Ethernet |

> 内部计划文档可保留通俗写法（"6 线"等），但**对外发布的 runbook / 报告必须用官方表述**——避免与销售物料不一致。

---

## 7. ENV 变量清单

| 变量 | 默认 | 说明 |
|---|---|---|
| `LIDAR_HOST_IP` | `192.168.123.18` | 拓展坞接 mid-360 / Go2 的 LAN 网卡 IP |
| `LIDAR_IP` | `192.168.123.20` | mid-360 IP |
| `ROBOT_IP` | （不设） | Go2 IP；不设走自动发现，扫到 `192.168.123.161`。多机时建议显式设 |
| `DIMOS_VIEWER` | `rerun` | 可视化后端，三选一：`rerun` / `foxglove` / `none`；rerun 自带 web 接口，远程看法见 §9 |

写进 `~/.bashrc` 或 systemd unit 的 `Environment=` 段。

---

## 8. 启动 / 停止 / 状态查询

### 8.1 前台调试启动

```bash
cd /home/unitree/jiangtao/topsun_dimos
source .venv/bin/activate

# 不带 --daemon，前台跑，Ctrl+C 退出
dimos run unitree-go2-nav-onboard
```

### 8.2 后台 daemon 启动

```bash
dimos run unitree-go2-nav-onboard --daemon
# → Run ID: 20260521-203012-unitree-go2-nav-onboard
# → Log:     /home/unitree/.local/state/dimos/logs/<run-id>
```

### 8.3 状态 / 日志

```bash
dimos status                    # 当前实例状态
dimos log -f                    # 跟随尾部
dimos log -n 200                # 最近 200 行（人类可读）
dimos log --json                # 原始 JSONL 输出

# 跑完 30 分钟后做摘要
python3 jiangtao/scripts/nav_log_summary.py
```

### 8.4 停止

```bash
dimos stop          # SIGTERM → SIGKILL after 5s
dimos stop --force  # 立即 SIGKILL
```

---

## 9. 笔记本看 viewer

### 9.1 直连方式（同 LAN）

```bash
# 笔记本浏览器
firefox http://<拓展坞-IP>:<port>
# port 看 dimos log 里 "RerunWebSocketServer listening on :<port>"
```

### 9.2 SSH 端口转发（推荐，不暴露端口）

```bash
# 笔记本
ssh -L 9876:localhost:9876 unitree@<拓展坞-IP>
firefox http://localhost:9876
```

---

## 10. 回滚命令

| 场景 | 命令 |
|---|---|
| 新栈卡死 | `dimos stop --force && dimos run unitree-go2` |
| 新栈进程崩 | systemd `OnFailure=dimos-go2-fallback.service` 自动起老栈（见 §11）|
| 配置错误 | `git checkout dimos/robot/unitree/go2/blueprints/navigation/` 删除新蓝图改动 |
| 雷达硬件故障 | 拔掉 mid-360；跑 `dimos run unitree-go2`（用 Go2 自带 lidar） |
| Orin NX 资源紧张 | 关 viewer：`dimos run unitree-go2-nav-onboard --viewer none` |

---

## 11. systemd unit

两份 unit 文件在 `jiangtao/scripts/` 下，安装一次即可：

```bash
sudo cp jiangtao/scripts/dimos-go2-nav.service /etc/systemd/system/
sudo cp jiangtao/scripts/dimos-go2-fallback.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable dimos-go2-nav.service     # 开机自启主蓝图
sudo systemctl start dimos-go2-nav.service      # 立即启动

# fallback 不要 enable（会和主蓝图抢端口）；它由主 unit 的 OnFailure= 自动触发
sudo systemctl status dimos-go2-nav
sudo journalctl -u dimos-go2-nav -f
```

**fallback 演练**（每次发版前做一次）：

```bash
sudo systemctl kill -s KILL dimos-go2-nav   # 强杀主 unit
# 等 30s 内观察 dimos-go2-fallback.service 是否启动
sudo systemctl status dimos-go2-fallback
# 看到 active (running) = 演练通过
```

---

## 12. 常见故障速查

| 现象 | 原因 | 解决 |
|---|---|---|
| `dimos run` 报 `FastLio2: host_ip not assigned` | 拓展坞 go2eth 没配 192.168.123.18 | `nmcli con up go2-lan` 或 §5.1 命令重配 |
| 启动很慢，Python worker 卡住 | C++ binary 还没 build / 远程 nix flake 拉不下来 | 检查 `nix build` 输出；保持 ssh -R 隧道并 export HTTPS_PROXY |
| `Failed to clone dimos repository` | 拓展坞 `topsun_dimos/.git/` 缺失 | rsync 时把 `.git/` 也同步过来（§3.1） |
| 地图在原地大漂、Go2 静止 odometry 也漂 | mid-360 mount 错（参考系不一致） | 复核 [`config.py`](../../dimos/robot/unitree/go2/config.py) 的 `_GO2_BASE_LINK_GROUND_HEIGHT` 与实测 |
| 闭环不触发（走完整圈无 PGO 日志） | 没回到 1 m 半径内的旧位置 | 故意走环线尾部回起点 1 m 内；或调大 `pgo.loop_search_radius` |
| 机器人钻不过门 | inflation 太大导致门口全黑 | `stuck_seconds` 内会自动收缩；不行调小 `simple_planner.inflation_radius` |
| cmd_vel 抖动 | PathFollower 跟丢 | 减小 `lookahead_distance`、`max_acceleration` 调大、`max_speed` 调小 |
| Go2 摔倒 | path_follower max_speed 太大 | 临时 `--config-overrides path_follower.max_speed=0.3` |
| rerun-web 看不到点云 | vis_throttle 太小 | 改 nav_stack_rerun_config 里 `vis_throttle=1.0` 试 |
| `Ports in use by <run-id>` | 之前 dimos 实例没干净退出 | `dimos stop --force` 后再启动 |
| `unrecognized arguments: --dist=loadfile` 跑 pytest 时 | `--no-default-groups` 装时漏了 tests group | `uv sync --group tests ...` 重装 |

---

## 13. 升级流程

```bash
# 1. 停服
sudo systemctl stop dimos-go2-nav

# 2. 拉新代码（保持 ssh -R 反向隧道开着）
cd /home/unitree/jiangtao/topsun_dimos
git fetch origin
git checkout jtlinux
git pull origin jtlinux

# 3. 同步依赖（增量）
HTTPS_PROXY=http://127.0.0.1:7890 HTTP_PROXY=http://127.0.0.1:7890 \
  uv sync --group tests \
          --extra unitree --extra navigation --extra apriltag \
          --extra agents --extra perception --extra visualization --extra web

# 4. 重 build C++（如果改了 cpp/）
cd dimos/hardware/sensors/lidar/fastlio2/cpp && nix build .#fastlio2_native
cd dimos/navigation/nav_stack/modules/pgo/cpp && nix build .#default --no-write-lock-file
cd /home/unitree/jiangtao/topsun_dimos

# 5. 启动
sudo systemctl start dimos-go2-nav
sudo systemctl status dimos-go2-nav

# 6. 烟囱测试
bash jiangtao/scripts/check_network.sh
.venv/bin/dimos run unitree-go2-nav-onboard --help

# 7. 出问题立即回滚
sudo systemctl stop dimos-go2-nav
git checkout <旧版本 sha>
sudo systemctl start dimos-go2-nav
```

---

## 14. merge gate 验收清单（值班工程师每天打勾）

按 [plan.md §10.4](../plan/plan.md#104-手工验收脚本) 七步执行；任意一步失败上报值班工程师组长，立即切回老蓝图。

| # | 阶段 | 操作 | 预期结果 | 今日打勾 |
|---|---|---|---|---|
| 1 | 网络 | `bash jiangtao/scripts/check_network.sh` | 7/7 OK | □ |
| 2 | 启动 | `dimos run unitree-go2-nav-onboard` | 10 分钟无 worker 死亡，无未处理异常 | □ |
| 3 | 看点云 | 浏览器打开 rerun-web | mid-360 点云持续刷新；odometry 平滑 | □ |
| 4 | 闭环 | 走 ~20 m"出去-回来" | 日志含 `loop closure triggered`；map→odom TF 跳变 < 0.5 m | □ |
| 5 | 导航 | rerun 点击 5 m 外目标 | 机器人朝目标走，goal_path / way_point / path 全更新 | □ |
| 6 | 抢占 | 手柄发 teleop / NaN goal | 自动导航立刻让出 / 停止 | □ |
| 7 | 回滚 | `dimos stop && dimos run unitree-go2` | 老链路 10 分钟内恢复正常 | □ |

七项全过：**当日新蓝图可继续运行**。任意一项失败：上报 + 回滚到老蓝图 + 排障。

---

> 本 runbook 基于 dimos `jtlinux` @ `81e9f144`（2026-05-21）。
> 升级或回滚后请同步更新本文件中的 commit sha。
