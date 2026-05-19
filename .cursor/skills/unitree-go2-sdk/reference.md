# Unitree Go2 SDK — 参考

## 官方与镜像文档

| 资源 | URL |
|------|-----|
| 宇树文档中心（英文） | https://support.unitree.com/home/en/developer |
| 宇树文档中心（中文） | https://support.unitree.com/home/zh/developer |
| unitree_sdk2（C++，Sport API 头文件） | https://github.com/unitreerobotics/unitree_sdk2 |
| sport_api.hpp / sport_client.hpp | `include/unitree/robot/go2/sport/` |
| unitree_sdk2py（DDS Python） | 随 `unitree-dds` extra 安装 |
| WebRTC 驱动（DimOS 使用） | https://github.com/legion1581/unitree_webrtc_connect |

DeepWiki（结构导读，非官方）：[Sport Control API](https://deepwiki.com/unitreerobotics/unitree_sdk2/5.3-sport-control-api)

## Sport 服务（SDK2 概念）

- 服务名：`sport`
- 典型方法（C++/Python `SportClient`）：`Move`, `BalanceStand`, `StandUp`, `StandDown`, `Sit`, `RiseSit`, `FreeWalk`, `StopMove`, `RecoveryStand`, `SwitchGait`, `SpeedLevel`, 以及舞蹈/空翻等
- DimOS DDS 适配器在连接后调用 `StandUp()` → `FreeWalk()`，再通过 `Move` 或 Rage 摇杆话题发速度

## WebRTC：话题与 api_id

来自 `unitree_webrtc_connect.constants`（`RTC_TOPIC`, `SPORT_CMD`）：

| 用途 | 话题键 | 说明 |
|------|--------|------|
| Sport 指令 | `SPORT_MOD` | `{"api_id": <id>, "parameter": {...}?}` |
| 速度（摇杆） | `WIRELESS_CONTROLLER` | `lx, ly, rx, ry` |
| 模式切换 | `MOTION_SWITCHER` | 如 `api_id` 1002 + `name: "ai"` |
| 里程计 | `ROBOTODOM` | |
| 雷达 | `ULIDAR_ARRAY` | |
| 低层状态 | `LOW_STATE` | |

DimOS 已映射的 WebRTC sport 命令（节选，完整列表见 `unitree_skill_container.py`）：

| 名称 | api_id |
|------|--------|
| BalanceStand | 1002 |
| StandUp | 1004 |
| StandDown | 1005 |
| RecoveryStand | 1006 |
| Sit | 1009 |
| RiseSit | 1010 |
| SwitchGait | 1011 |
| BodyHeight | 1013 |
| FootRaiseHeight | 1014 |
| SpeedLevel | 1015 |
| TrajectoryFollow | 1018 |
| SwitchJoystick | 1027 |
| Rage Mode（DimOS 扩展） | 2059 |
| Handstand | 1301 |

调用示例（与 `execute_sport_command` 一致）：

```python
from unitree_webrtc_connect.constants import RTC_TOPIC

connection.publish_request(RTC_TOPIC["SPORT_MOD"], {"api_id": 1004})  # StandUp
```

带参数示例（Rage Mode）：

```python
connection.publish_request(
    RTC_TOPIC["SPORT_MOD"],
    {"api_id": 2059, "parameter": {"data": True}},
)
```

## WebRTC 速度坐标（易错）

`UnitreeWebRTCConnection.move()` 注释约定：

- `twist.linear.x` → 左右（正=右）
- `twist.linear.y` → 前后（正=前）
- `twist.angular.z` → 偏航（正=右转）

映射到摇杆：`lx=-y`, `ly=x`, `rx=-yaw`, `ry=0`。

## DimOS 文件索引

| 文件 | 职责 |
|------|------|
| `dimos/robot/unitree/connection.py` | WebRTC 连接、move、sport、传感器流 |
| `dimos/robot/unitree/go2/connection.py` | `GO2Connection` 模块、idle rest、cmd_vel |
| `dimos/robot/unitree/unitree_skill_container.py` | Agent `execute_sport_command` |
| `dimos/hardware/drive_trains/unitree_go2/adapter.py` | DDS SportClient + Rage 摇杆 |
| `dimos/robot/unitree/go2/stair_locomotion/locomotion_policy.py` | 楼梯 sport 参数 |
| `docs/capabilities/navigation/native/index.md` | WebRTC 雷达说明 |

## 本地验证命令

```bash
# WebRTC 回放（无真机）
dimos --replay run unitree-go2

# DDS 键盘遥操作（需 unitree-dds + ROBOT_IP）
export ROBOT_IP=192.168.123.161
dimos run unitree-go2-keyboard-teleop

# 列出 sport 技能名（agentic 蓝图运行后）
dimos mcp call execute_sport_command --arg command_name=Hello
```

## 从官方文档到代码的映射步骤

1. 文档中的 **API 名称** → 在 `unitree_sdk2` 的 `sport_client.hpp` 或本仓库 `UNITREE_WEBRTC_CONTROLS` 中查 **api_id**。
2. 文档中的 **参数** → WebRTC 放入 `parameter` 字典；DDS 查 `SportClient` 方法签名。
3. 文档中的 **模式依赖** → 在调用前插入 `balance_stand()` / `free_walk()`（WebRTC）或 adapter 启动序列（DDS）。
4. 新增 Agent 技能 → `unitree_skill_container.py` 加条目 + 更新 system prompt `# AVAILABLE SKILLS`（若暴露给 LLM）。
