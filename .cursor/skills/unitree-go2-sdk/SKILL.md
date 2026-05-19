---
name: unitree-go2-sdk
description: Reads Unitree Go2 developer documentation and maps Sport/motion APIs to DimOS code paths (WebRTC and DDS). Use when editing Go2 behavior, locomotion, sport commands, velocity control, gait modes, or when the user mentions Unitree SDK, support.unitree.com, Go2 motion control, or 狗子运动控制.
---

# Unitree Go2 SDK（行为与运动控制）

## 官方文档入口

阅读宇树开发者文档，了解 Go2 行为编辑与运动控制：

**https://support.unitree.com/home/en/developer**

文档站为 SPA，若抓取失败：用浏览器打开上述链接，或查 GitHub 开源 SDK 作为补充（见 [reference.md](reference.md)）。

## 先选控制通道

DimOS 里 Go2 有两条与官方 SDK 对应的通路，改代码前必须确认蓝图用的是哪条：

| 通道 | 典型场景 | 依赖 | 本仓库入口 |
|------|----------|------|------------|
| **WebRTC** | 默认 `dimos run`、未装 DDS | `unitree-webrtc-connect` | `dimos/robot/unitree/connection.py` |
| **DDS (SDK2)** | `unitree-go2-keyboard-teleop` 等 | `uv pip install -e ".[unitree-dds]"` | `dimos/hardware/drive_trains/unitree_go2/adapter.py` |

`GlobalConfig.unitree_connection_type`：replay / mujoco / **webrtc**（真机默认）。

## 阅读文档时的对照清单

1. **运动模式 / FSM**：`BalanceStand`、`FreeWalk`、`StandUp`、`StandDown`、避障、Rage Mode 等前置条件。
2. **Sport API**：每个动作对应 `api_id`；WebRTC 走 `RTC_TOPIC["SPORT_MOD"]` + `publish_request`。
3. **速度控制**：`Move(vx, vy, vyaw)`（DDS）vs `WIRELESS_CONTROLLER` 摇杆字段（WebRTC，注意坐标映射）。
4. **Motion Switcher**：连接时切换 `ai` 等模式（WebRTC `MOTION_SWITCHER` api_id 1002）。

## 在本仓库落地改动

### WebRTC（多数 blueprint）

- 连接与模式：`dimos/robot/unitree/connection.py` → `UnitreeWebRTCConnection`
- 速度：`move()` → `RTC_TOPIC["WIRELESS_CONTROLLER"]`（`lx=-vy`, `ly=x`, `rx=-yaw`）
- 姿态/特技：`publish_request(RTC_TOPIC["SPORT_MOD"], {"api_id": ...})`
- Agent 可调用的 sport 名称列表：`dimos/robot/unitree/unitree_skill_container.py` → `UNITREE_WEBRTC_CONTROLS`、`execute_sport_command`
- 模块 RPC：`dimos/robot/unitree/go2/connection.py` → `GO2Connection`（`standup`, `balance_stand`, `move`, `enable_rage_mode`）

### DDS / unitree_sdk2py

- `SportClient` 封装：`dimos/hardware/drive_trains/unitree_go2/adapter.py`
- 启动序列：MotionSwitcher → StandUp → FreeWalk → `Move` / 摇杆话题
- 说明与排错：`dimos/hardware/drive_trains/unitree_go2/README.md`

### 楼梯等定制行为

- `dimos/robot/unitree/go2/stair_locomotion/`（如 `FootRaiseHeight` api_id 1014）
- MuJoCo 仿真 Sport 镜像：`docs/development/go2_stair_mujoco_sport.md`、`dimos/simulation/mujoco/sport_state.py`
- 蓝图：`dimos/robot/unitree/go2/blueprints/smart/unitree_go2_stairs.py`

## 推荐工作流

```
1. 打开 https://support.unitree.com/home/en/developer → 定位 Go2 / Sport / 运动控制章节
2. 记下 api_id、参数结构、模式前置条件
3. 在仓库 Grep api_id 或 Sport 方法名，看是否已有封装
4. 无封装 → 在 connection.py / adapter.py / skill_container 中选与当前通道一致的路径添加
5. 真机前：replay 或 mujoco；WebRTC 用 mock IP；DDS 需 ROBOT_IP + cyclonedds
```

## 安全与顺序

- 动态特技（空翻等）后常需 `RecoveryStand`（api_id 1006）。
- 发速度前通常需 `balance_stand()` / `FreeWalk()`。
- `enable_rage_mode` 假设已 BalanceStand；勿在未站稳时连续发特技。

## 延伸阅读

- API 对照表、GitHub 文档镜像、RTC 话题： [reference.md](reference.md)
