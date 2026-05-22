# Orbit Object 真机调试记录

## 测试环境

- **机器人**: Unitree Go2 四足机器人
- **系统**: macOS (Apple Silicon)，通过 WiFi 连接 Go2 (IP: 10.10.196.253)
- **基础蓝图**: `dimos run unitree-go2` 提供 odom + costmap 数据流
- **测试脚本**: `demo_orbit_standalone.py` — 通过 LCM 直接订阅数据，启动即绕行
- **日期**: 2026-05-21

---

## 调试历程

### 第 1 轮：启动 unitree-go2-agentic 失败 (CUDA)

**问题**: `dimos run unitree-go2-agentic` 报错 `EdgeTAM requires a CUDA-capable GPU`

**原因**: `unitree_go2_spatial` 蓝图包含 `SecurityModule`，其内部初始化 `EdgeTAMProcessor`，强制要求 NVIDIA CUDA GPU。macOS 无 CUDA。

**解决**: 从 `unitree_go2_spatial.py` 中移除 `SecurityModule`。

---

### 第 2 轮：McpServer 超时

**问题**: 移除 SecurityModule 后，`unitree-go2-agentic` 启动卡在 `McpServer/on_system_modules` RPC 调用，120 秒后超时崩溃。

**原因**: `on_system_modules` 对每个已部署模块调用 `get_skills()`（涉及 langchain import + schema 生成），在多 worker 进程中串行执行时间过长。

**解决**: 改用 `demo_orbit_standalone.py` 独立测试脚本，绕过 Agent/MCP 层，直接通过 LCM 订阅 odom/costmap 并发布 cmd_vel。

---

### 第 3 轮：LCMTransport 参数顺序错误

**问题**: `demo_orbit_standalone.py` 报错 `AttributeError: 'str' object has no attribute 'msg_name'`

**原因**: `LCMTransport` 构造参数写反了。写成了 `LCMTransport(PoseStamped, "/odom...")` ，正确应为 `LCMTransport("/odom", PoseStamped)`。

**解决**: 修正参数顺序。

---

### 第 4 轮：机器人原地疯狂旋转

**问题**: 启动后机器人在原地疯狂转圈，yaw 在 ±180° 间剧烈跳动，位置几乎不变。

**关键数据**:
```
[2.0s]  yaw=-133° az=-0.96
[4.0s]  yaw=-165° az=-0.67
[6.0s]  yaw=1°    az=1.03
[8.0s]  yaw=151°  az=-0.40
```

**原因**: `angular_z`（转向速度）太大（KP_YAW=0.5，输出高达 ±1.5），完全压过了前进速度。算法在世界坐标系计算速度后转换到机体坐标系，但 `angular_z` 同时转身导致机体坐标系不断变化，形成正反馈震荡。

**关键发现**: Go2 的 `angular_z` 不是 rad/s，而是**摇杆值 [-1, 1]**。WebRTC 映射为 `data={"rx": -yaw}`，直接传给摇杆。`angular_z=0.8` 实际是 80% 最大转速（约 2 rad/s = 115°/s）。

---

### 第 5 轮：控制策略重构 — 面对物体+横移

**问题**: 世界坐标系速度转换 + 同时转身的方案本质上不稳定。

**解决**: 完全重构控制策略，利用 Go2 四足机器人的横移能力：
- `angular_z`: 转身面对物体
- `vx`（前进/后退）: 保持与物体的距离
- `vy`（横向移动）: 固定速度横移实现绕行

参数: `KP_YAW=0.15`, `MAX_ANGULAR_Z=0.15`, `ORBIT_SPEED=0.3`

---

### 第 6 轮：face_err 不收敛，机器人背对物体退行

**问题**: `face_err` 在 -136° 到 +166° 间剧烈波动，机器人始终没能面对物体，背对着物体退行。

**关键数据**:
```
[2.0s]  face_err=-136° vx=-0.28 vy=0.20 az=-0.30
[6.0s]  face_err=-48°  ...
[8.0s]  face_err=110°  ...   ← 过冲
```

**原因**: `MAX_ANGULAR_Z=0.3` 对 Go2 来说仍然过大（30% 最大转速 ≈ 60°/s），导致过冲。同时机器人没面对物体就开始横移，不断改变相对角度。

**解决**: 
1. 降低 `MAX_ANGULAR_Z` 到 0.15
2. 添加 `FACE_THRESHOLD=30°` — 朝向偏差 >30° 时停止移动，原地转身

---

### 第 7 轮：面对成功但横移进入死区

**问题**: `face_err` 成功收敛到 ~0°，yaw 稳定，但机器人位置不动。vy=0.15 持续发送但无响应。

**关键数据**:
```
[22.0s] face_err=-2° d=1.00m vx=0.00 vy=0.15 az=-0.01  ← 不动
[24.0s] face_err=-0° d=1.00m vx=0.00 vy=0.15 az=-0.00  ← 不动
```

**原因**: Go2 摇杆有**死区**，低于约 0.2 的摇杆值被忽略。`ORBIT_SPEED=0.15` 低于死区阈值。

**解决**: 将 `ORBIT_SPEED` 从 0.15 提高到 0.3。

---

### 第 8 轮：成功绕行 62%，拐角处卡住

**问题**: 机器人成功绕行超过半圈（0% → 62%），但在拐角处 `face_err` 达到 -33° 超过 30° 阈值，触发停止。停止后 `angular_z=0.09` 也低于死区，无法转身，永久卡住。

**关键数据**:
```
[32.0s] progress=50%  face_err=-6°   ← 正常绕行
[34.0s] progress=65%  face_err=-66°  ← 拐角，停下转身
[36.0s] progress=67%  face_err=-54°  ← 在转
...
[56.0s] progress=62%  face_err=-33°  ← 卡住，az=0.09 低于死区
```

**尝试**: 提高 `FACE_THRESHOLD` 到 45°/60°、提高 `MAX_ANGULAR_Z` 到 0.25 → 效果更差，回到震荡。

---

### 第 9 轮：成功完成一整圈

**参数**: 回退到第 8 轮的参数，仅将 `FACE_THRESHOLD` 从 30° 改为 30°（保持不变），确认是同一组参数。

**最终成功参数**:
```python
KP_DISTANCE = 0.5
KP_YAW = 0.15
MAX_ANGULAR_Z = 0.15
FACE_THRESHOLD = 30°
ORBIT_SPEED = 0.3
```

**结果**: 60 秒完成一整圈，进度 0% → 100%。

**关键数据**:
```
[6.0s]  progress=4%   d=0.91m  face_err=-1°
[18.0s] progress=15%  d=1.11m  face_err=-12°
[30.0s] progress=33%  d=0.96m  face_err=4°
[44.0s] progress=73%  d=0.91m  face_err=6°
[56.0s] progress=88%  d=1.63m  face_err=4°
[60.0s] progress=92%  d=0.56m  → 完成 1 圈 (100%)!
```

**存在的问题**:
- 距离波动较大：最近 0.41m，最远 1.63m（理想 1.0m）
- 拐角处会短暂停下转身
- 轨迹不够圆，偏椭圆/不规则

---

## 最终参数总结

| 参数 | 设计值 | 真机调试值 | 说明 |
|------|--------|-----------|------|
| KP_DISTANCE | 0.8 | 0.5 | Go2 摇杆值非物理速度，需降低 |
| KP_YAW | 0.5 | 0.15 | 同上，防止旋转过冲 |
| MAX_ANGULAR_Z | — | 0.15 | 新增限幅，防过冲 |
| FACE_THRESHOLD | — | 30° (0.524 rad) | 新增，超过阈值时原地转身 |
| ORBIT_SPEED | 0.3 m/s | 0.3 (摇杆值) | 不变，但语义从 m/s 变为摇杆比例 |
| CONTROL_HZ | 10 | 10 | 不变 |

## 关键经验

1. **Go2 的 cmd_vel 是摇杆值 [-1, 1]，不是物理单位 (m/s, rad/s)**。WebRTC 映射：`lx=-vy, ly=vx, rx=-yaw`。
2. **Go2 摇杆有死区**，约 0.15-0.2 以下的值无响应。所有速度指令需要高于此阈值。
3. **世界坐标→机体坐标转换 + 同时转身 = 不稳定**。四足机器人应利用横移能力，改用"面对物体 + 横移绕行"策略。
4. **找到的"障碍物"通常是墙壁**而非用户期望的小物品。costmap 来自 LiDAR，只能检测到高于 LiDAR 扫描面的物体。
5. **控制增益必须很低**（0.15），否则 Go2 高灵敏度的电机会导致剧烈震荡。
