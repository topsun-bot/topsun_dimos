# Go2 楼梯 Sport API 与 MuJoCo 仿真

## 官方文档

宇树开发者中心（高层运动控制 / Sport）：

- 英文：https://support.unitree.com/home/en/developer  
- 高层运动章节（浏览器打开）：https://support.unitree.com/home/en/developer/High_motion_control  

文档站为 SPA，若无法抓取，请用浏览器查看；API 与开源 SDK 对照：

- C++：`unitree_sdk2` → `include/unitree/robot/go2/sport/sport_client.hpp`
- DimOS WebRTC：`unitree_webrtc_connect.constants.SPORT_CMD`

## 楼梯相关 Sport 能力（与 DimOS 对齐）

| 能力 | SDK2 / 文档 | WebRTC `SPORT_CMD` | DimOS 用法 |
|------|-------------|-------------------|------------|
| 自由行走前置 | `FreeWalk()` | `FreeWalk` 1045 | `prepare_locomotion()` |
| 抬脚高度 | `FootRaiseHeight` | `FootRaiseHeight` **1014** | `StairLocomotionConfig.foot_raise_height_m` |
| 机身高度 | `BodyHeight` | `BodyHeight` 1013 | `body_height_delta_m` |
| 步态切换 | `SwitchGait` | `SwitchGait` 1011 | `gait_id` |
| 速度档位 | `SpeedLevel` | `SpeedLevel` 1015 | `speed_level` |
| 交叉步（文档/SDK2 `CrossStep`） | API 2051 (SDK2) | `CrossStep` **1302** | 可选，仿真 SHM 会加大前进增益 |
| 经济步态 | `EconomicGait` | `EconomicGait` 1035 | `use_economic_gait` |

实现入口：`dimos/robot/unitree/go2/stair_locomotion/sport_api.py` → `GO2Connection.publish_request`（真机 WebRTC）或 `MujocoConnection.publish_request`（仿真 SHM）。

## MuJoCo 如何“模拟” Sport API

真机由机载固件执行 `FootRaiseHeight` 等指令。仿真没有 Sport 服务，DimOS 将同一套 `SPORT_MOD` 请求写入**共享内存**，由 `Go1OnnxController` 近似：

- 提高策略 obs 中的前进指令增益（对应 `FootRaiseHeight`）
- 在前腿关节控制量上叠加抬腿相位偏置（视觉上的抬脚）
- `CrossStep` 时略增侧向与前进增益

代码：`dimos/simulation/mujoco/sport_state.py`、`shared_memory.py`（`sport` 缓冲区）、`policy.py`。

**注意：** 仿真仍使用 Go1 的 ONNX 策略（`unitree_go2` → `unitree_go1`），与真机 Go2 固件步态不完全一致；用于验证**规划 + Sport 调用链 + 大致爬楼**，不能替代真机 sign-off。

## 运行

```bash
# 仿真（Sport API → SHM → ONNX 调制）
dimos --simulation run unitree-go2-stairs

# 真机（完整 FootRaiseHeight / FreeWalk）
dimos run unitree-go2-stairs --robot-ip 192.168.123.161
```

日志中仿真应出现：

`Go2 stair sport mode active ... note=(MuJoCo mirrors Sport API via shared memory)`

以及 debug 级：`MuJoCo sport SHM updated`。

## 扩展仿真保真度

1. 调 `sport_state._recompute_derived_gains` 中的增益系数。  
2. 为 Go2 提供专用 MuJoCo ONNX（替换 `model.py` 中 go1 回退）。  
3. 在 `StairLocomotionConfig` 中增加 `use_cross_step: bool`，在 `enter_stair_mode` 里发送 `CrossStep`（与官方 High_motion_control 一致）。
