# 现场实机检查单（plan.md 里"必须 Go2 走动"的所有任务）

> **本清单只列出"远程做不了、必须现场实机操作员执行"的任务**。
> 每项都附 plan.md 出处、命令、预期结果、不通时怎么办。
> 现场操作员需要：mid-360 装好的 Go2、拓展坞、电池、清空的实验场地（≥ 5 × 5 m）、teleop 手柄、急停按钮可达。

## 前置：开机自检（5 分钟）

```bash
# 拓展坞 SSH
ssh unitree@<拓展坞-IP>
cd /home/unitree/jiangtao/topsun_dimos
bash jiangtao/scripts/check_network.sh
# 期望全部 7/7 OK
```

不通就翻 [runbook §12 故障速查](go2-nav-onboard.md#12-常见故障速查)。

---

## 里程碑 B - 实机部分

### B-4 GO2Connection + WebRTC 连接验证

**对应 plan.md**：[§6.1 B-4](../plan/plan.md#61-任务列表)

**操作**：

```bash
# 不带 mid-360，先验证 Go2 自带 lidar / odom / 摄像头流通
unset ROBOT_IP   # 走自动发现
.venv/bin/dimos --viewer rerun run unitree-go2-basic --daemon

# 5 秒后看状态
.venv/bin/dimos status
.venv/bin/dimos log -n 50
```

**预期**：
- log 含 `Found 1 Go2 ... using it`（IP 自动发现）或 `ROBOT_IP=192.168.123.161 valid, online`
- 笔记本浏览器 rerun-web 能看到 Go2 自带 4D LiDAR 点云、odometry 平滑、摄像头流
- 不报错运行 5 分钟无 worker 死亡

**不通的话**：
- IP 自动发现失败：检查 `dimos go2tool discover` 是否扫到 `192.168.123.161`
- WebRTC 连不上：确认 Go2 上电、`ping 192.168.123.161` 通

**完成后停掉**（避免占端口）：
```bash
.venv/bin/dimos stop
```

---

## 里程碑 C - 硬件 bring-up

### C-1 拓展坞 LAN 网卡确认

```bash
sudo tcpdump -i go2eth -nn 'host 192.168.123.20' -c 20
# 让 mid-360 上电（如还没的话），应看到 UDP 包持续打到拓展坞
# Ctrl+C 退出
```

**预期**：看到 `192.168.123.20.* > 192.168.123.18.56500` 等 UDP 流。

### C-2 Mid-360 IP / 状态核对

mid-360 标签 / Livox Viewer 确认：
- IP = `192.168.123.20`
- host_ip = `192.168.123.18`（雷达侧设置的目标 IP）
- 内置 IMU 已开启
- 出货固件版本（记到下面 §测试报告）

### C-4 nix build 三个 C++ 二进制

```bash
# 笔记本侧保持 ssh -R 7890:127.0.0.1:7890 unitree@<拓展坞-IP> 反向隧道
export HTTPS_PROXY=http://127.0.0.1:7890

cd /home/unitree/jiangtao/topsun_dimos/dimos/hardware/sensors/lidar/livox/cpp
nix build .#mid360_native --extra-experimental-features 'nix-command flakes'

cd ../../fastlio2/cpp
nix build .#fastlio2_native --extra-experimental-features 'nix-command flakes'

cd /home/unitree/jiangtao/topsun_dimos/dimos/navigation/nav_stack/modules/pgo/cpp
nix build .#default --no-write-lock-file --extra-experimental-features 'nix-command flakes'
```

**预期**：每条命令成功，`result/bin/<二进制>` 存在且可执行。Aarch64 + Ubuntu 20.04 上首次 build 约 15-40 分钟。

**不通的话**：
- 网络：保持 ssh -R 隧道 + export HTTPS_PROXY；超时重跑
- 远程 flake 依赖（Livox SDK2 / FAST-LIO-NON-ROS）拉不下来：换更稳定的代理

### C-5 单跑 mid360 蓝图（仅雷达驱动）

```bash
.venv/bin/dimos --viewer rerun run mid360 --daemon
.venv/bin/dimos log -f
# 1 分钟后看 rerun-web，应有 mid-360 点云
.venv/bin/dimos stop
```

**预期**：rerun 里看到 mid-360 点云持续刷新（约 10 Hz），范围 30+ m。

### C-6 单跑 mid360-fastlio-voxels（雷达 + SLAM + voxel map）

```bash
.venv/bin/dimos --viewer rerun run mid360-fastlio-voxels --daemon

# 静止 10 秒后慢慢推 / 拿着 mid-360 走小段
# 看 rerun：odometry 是否平滑、global_map 是否累积合理

.venv/bin/dimos stop
```

**预期**：
- odometry 在静止时漂移 < 1 cm
- 走 5 m 直线，FastLio2 估计的距离应 = 实测距离 ± 5%

**不通的话**：
- mid-360 mount 错（SLAM 漂飞）→ 复核 [`config.py`](../../dimos/robot/unitree/go2/config.py) 的 `_GO2_BASE_LINK_GROUND_HEIGHT`
- 端口冲突 → `ss -ulnp | grep 565` 查谁占了 mid-360 端口

### C-7 整合到 unitree-go2-nav-onboard 启动

```bash
.venv/bin/dimos --viewer rerun run unitree-go2-nav-onboard --daemon
.venv/bin/dimos status
.venv/bin/dimos log -f
```

**预期**：
- 11 个 worker 全活（FastLio2、PGO、TerrainAnalysis、TerrainMapExt、SimplePlanner、LocalPlanner、PathFollower、MovementManager、GO2Connection、vis_module 内 3 个）
- rerun 看到 mid-360 点云、`corrected_odometry`、`terrain_map`、`terrain_map_ext`、`costmap_cloud`、`global_map_pgo`

### C-8 闭环触发测试（merge gate 第 4 项）

让 Go2 走"出去 - 回来" ~20 m 闭环：

```
   起点 A
   ↓
   走 10 m 到 B
   ↓
   原路返回 A（或绕一圈回 A）
```

```bash
# 跑动期间看日志
.venv/bin/dimos log -f | grep -i "loop\|pgo"
```

**预期**：回到起点附近时（半径 1 m），日志含 `PGO loop closure triggered`，map→odom TF 跳变 < 0.5 m。

**不通的话**：
- 没回到 1 m 内 → 故意走得离起点更近
- 阈值太严 → 调大 `pgo.loop_search_radius`（仓库默认 1.0，可改 2.0 - 3.0）

### C-9 mid360.yaml 评估

**已完成**（见 PR 描述）：mid360.yaml 的 `extrinsic_T/R` 是雷达**内部 IMU vs 雷达本体**（Livox 出厂标定），与 Go2 安装外参分开，**不要改**。Go2 安装外参写在 [`config.py`](../../dimos/robot/unitree/go2/config.py) `_MID360_MOUNT`。

---

## 里程碑 D - 规划与控制闭环

> 在 ≥ 5 × 5 m 的实验场地，地面平整，急停手柄常握。**Go2 速度限定 0.4 m/s（merge default）**。

### D-1 ~ D-3 启动 + 调参（已在代码里固化）

```bash
.venv/bin/dimos --viewer rerun run unitree-go2-nav-onboard --daemon
```

参数已按 plan.md §8.2 固化在 [`unitree_go2_nav_onboard.py`](../../dimos/robot/unitree/go2/blueprints/navigation/unitree_go2_nav_onboard.py)：
- `path_follower.max_speed = 0.4 m/s`（merge default）
- `simple_planner.cell_size = 0.15 m`、`inflation_radius = 0.25 m`
- `local_planner.vehicle_length = 0.7 m`、`vehicle_width = 0.3 m`

### D-4 室内 5 m 直线 goal 测试（merge gate 第 5 项）

**操作**：
1. Go2 站在场地一端（点 A）
2. rerun-web 中点击 5 m 外的目标点（点 B）
3. 观察 Go2 朝目标走，直到 `goal_reached`

**merge gate**：**至少 1 次成功**到达 ±0.3 m。

**灰度门槛**（合并后第 2 周）：连续 10 次成功率 ≥ 80%。

### D-5 室内绕静态障碍 goal 测试

**操作**：
1. 在场地中央放一把椅子
2. 设置 goal 在椅子另一侧 3 - 4 m 远
3. 观察 Go2 绕开椅子到达

**灰度门槛**：5 次成功率 ≥ 80%。

### D-6 teleop 抢占测试（merge gate 第 6 项）

**操作**：
1. 开始一次自动导航
2. 自动导航中按 teleop 手柄方向键

**预期**：MovementManager 立即让出控制权，Go2 跟手柄走；松开手柄后 Go2 停止（不继续之前的自动 goal）。

### D-7 goal 取消测试（merge gate 第 6 项）

**操作**：
1. 开始一次自动导航
2. 在 rerun 发一个 NaN goal（或新的 goal）

**预期**：Go2 立即停止当前 goal，转而执行新 goal（或停在原地）。

### D-8 回滚演练（merge gate 第 7 项）

**操作**：

```bash
.venv/bin/dimos stop
.venv/bin/dimos run unitree-go2 --daemon
.venv/bin/dimos status
```

**预期**：旧蓝图（`unitree-go2`）10 分钟内启动且 Go2 自带 lidar 点云在 rerun 可见。

### D-9 卡住自适应测试

**操作**：故意让 Go2 卡在一个窄道（不能动 5 秒）。

**预期**：日志含 `Stuck — shrinking inflation`；inflation 自动从 0.25 缩到 0.05；Go2 重新规划成功通过。

---

## 里程碑 E - 实测部分

### E-5 30 分钟压力测试

**操作**：

```bash
# 启动
.venv/bin/dimos --viewer rerun run unitree-go2-nav-onboard --daemon

# 在另一个 SSH 终端开 top / htop 监控 30 分钟
htop  # 关注 CPU、RAM
# 期间反复发 5 m goal，让 Go2 走来走去

# 30 分钟后停掉
.venv/bin/dimos stop

# 出摘要
python3 jiangtao/scripts/nav_log_summary.py | tee logs/30min-stress-$(date +%Y%m%d-%H%M).txt
```

**灰度门槛**：
- CPU peak < 70%
- RAM peak < 8 GB
- 关键 topic（`registered_scan` / `corrected_odometry` / `path` / `cmd_vel`）频率偏差 < 10%
- 0 次 worker 死亡 / 未处理异常

**fallback 演练**：

```bash
# 测试主 unit 失败时 systemd OnFailure 是否切到老蓝图
sudo systemctl kill -s KILL dimos-go2-nav
# 30 秒内观察
sudo systemctl status dimos-go2-fallback
# 应 active (running)，rerun 看到老蓝图链路恢复
```

---

## 测试报告模板

每次发版前填一份贴在 PR 评论里：

| 项 | 测试人 | 日期 | 结果 | 备注 |
|---|---|---|---|---|
| B-3 IP 自动发现 |  |  | □ |  |
| B-4 GO2Connection 启动 |  |  | □ |  |
| C-4 nix build 三个二进制 |  |  | □ |  |
| C-5 mid360 单跑 |  |  | □ |  |
| C-6 mid360-fastlio-voxels |  |  | □ |  |
| C-7 nav-onboard 11 worker 全活 |  |  | □ |  |
| C-8 闭环触发 |  |  | □ |  |
| **D-4 5m 直线 goal merge gate（≥1）** |  |  | X/N |  |
| **D-6 teleop 抢占 merge gate（≥1）** |  |  | X/N |  |
| **D-7 goal 取消 merge gate（≥1）** |  |  | X/N |  |
| **D-8 回滚演练 merge gate** |  |  | □ |  |
| D-9 卡住自适应 |  |  | □ |  |
| (灰度) D-4 直线 × 10 |  |  | X/10 |  |
| (灰度) D-5 绕障 × 5 |  |  | X/5 |  |
| (灰度) E-5 30min 压力 |  |  | CPU peak / RAM peak |  |
| E-5 fallback 演练 |  |  | □ |  |

发现的 bug / regression：（如有，写在这里）

---

> 本检查单基于 dimos `jtlinux` @ `81e9f144`（2026-05-21）。
> 升级或回滚后请同步更新本文件中的 commit sha。
