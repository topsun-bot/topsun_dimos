# Go2 楼梯 Sport API 与 MuJoCo 仿真

完整改动列表、仿真启动与测试流程见：[go2_stairs_sim_workflow.md](./go2_stairs_sim_workflow.md)。

## 官方文档

宇树开发者中心（高层运动控制 / Sport）：

- 英文：https://support.unitree.com/home/en/developer  
- 高层运动章节（浏览器打开）：https://support.unitree.com/home/en/developer/High_motion_control  

文档站为 SPA，若无法抓取，请用浏览器查看；API 与开源 SDK 对照：

- C++：`unitree_sdk2` → `include/unitree/robot/go2/sport/sport_client.hpp`
- Python SDK2：`unitree_sdk2py.go2.sport.sport_client.SportClient`（`CrossStep`, `FreeWalk`, …）
- DimOS WebRTC：`unitree_webrtc_connect.constants.SPORT_CMD`

## 楼梯相关 Sport 能力（与 DimOS 对齐）

Python 运控示例（`TestOption`）中 **id=16 / `walk stair`** 对应 ``sport_client.WalkStair(True/False)``（API **1049**）。DimOS 默认在进入 `ON_STAIR` 时调用该动作，前进仍由 ``Move`` / ``cmd_vel`` 驱动。

| 能力 | SDK2 | API id | DimOS 用法 |
|------|------|--------|------------|
| **爬楼梯** | `WalkStair` | **1049** | 默认 `stair_sport_mode="walk_stair"` |
| 平衡站立 + 自由行走 | `BalanceStand`, `FreeWalk` | 1002, 1045 | `prepare_locomotion()`（仿真也写入 SHM） |
| 抬脚高度（回退） | `FootRaiseHeight` | **1014** | `stair_sport_mode="manual"` 时 `foot_raise_height_m` |
| 机身高度 | `BodyHeight` | 1013 | `body_height_delta_m` |
| 步态切换 | `SwitchGait` | 1011 | `gait_id`（1/2 → 仿真 STAIR 档） |
| 速度档位 | `SpeedLevel` | 1015 | `speed_level` |
| 交叉步 | `CrossStep` | **1302** | `use_cross_step`（仿真默认开） |
| 交叉行走 | `CrossWalk` | **1051** | SHM `CROSS_WALK` 执行档 |
| 单侧步 | `OnesidedStep` | **1303** | SHM 交替前腿抬升 |
| 经济步态 | `EconomicGait` | 1035 | `use_economic_gait` |

实现入口：`sport_actions.py`（`SportClient` 动作表）→ `sport_api.py` → `GO2Connection.publish_request`（真机 WebRTC）或 `MujocoConnection.publish_request`（仿真 SHM）。

固件 **V1.1.6+** 的 PyPI `unitree_sdk2py` 已移除 `WalkStair` 包装，但机载 sport 服务仍可能接受 **1049**；若真机返回 unknown api，将 `StairLocomotionConfig.stair_sport_mode` 设为 `"manual"`。

## MuJoCo 如何“模拟” Sport API

真机由机载固件执行 `FootRaiseHeight` 等指令。仿真没有 Sport 服务，DimOS 将同一套 `SPORT_MOD` 请求写入**共享内存**，由 `Go1OnnxController` 近似：

- 提高策略 obs 中的前进指令增益（`FootRaiseHeight` + `SportExecMode`）
- 在前腿关节控制量上叠加抬腿相位（`ONESIDED_STEP` 为左右交替）
- `CrossStep` / `CrossWalk` 时增加侧向与 `action_scale_boost`

代码：`dimos/simulation/mujoco/sport_state.py`、`shared_memory.py`、`policy.py`（DimOS MuJoCo）与 `unitree_mujoco/go2_policy.py`（官方 Go2 模型，继承同一套 WalkStair 逻辑）。

**注意：** 仿真仍使用 Go1 的 ONNX 策略（`unitree_go2` → `unitree_go1`），与真机 Go2 固件步态不完全一致；用于验证**规划 + Sport 调用链 + 大致爬楼**，不能替代真机 sign-off。

### 仿真稳定性（防翻倒）

| 机制 | 作用 |
|------|------|
| `scene_stairs` **10×15 cm** 踏高、**32 cm** 踏深（约 25°，原 28 cm 约 28°） | 宏观仍 15 cm 均匀；MuJoCo 略放缓坡度减后仰倾倒 |
| MuJoCo **WalkStair 速度辅助** + 分阶段 Sport（先平地再走楼梯） | 仅仿真；真机不注入 qvel |
| 仿真 `cmd_vel` 上限 ~0.26 m/s | 降低冲击 |
| Sport `command_gain` / `action_scale_boost` **上限** | 避免 ONNX 观测饱和 |
| STAIR/WalkStair 时放宽 IMU 倾角上限（~0.58） | 爬坡前倾不再把 cmd_vel 清零 |
| 倾倒检测宽限期 + 仿真 pitch 上限 38° | 避免误 abort |
| `ALIGN` 阶段 **保持 0.8 s** 再 `ON_STAIR` | 避免瞬时猛冲 |
| 倾倒检测（pitch/roll） | 中止爬楼并退出 Sport |

## 运行

```bash
uv sync --extra go2-sim

# 默认：官方 unitree_mujoco + DimOS 楼梯场景（15 cm 子踏面）+ 激光/相机 rig + Sport → SHM
dimos --simulation run unitree-go2-stairs

# Rerun 3D + 代价地图；点击设目标：http://localhost:7779/command-center
dimos --simulation --viewer rerun-web run unitree-go2-stairs

# Agent + MCP + climb_stairs 等 skills
dimos --simulation --viewer rerun-web run unitree-go2-stairs-agentic

# 真机（完整 FootRaiseHeight / FreeWalk / WalkStair 1049）
dimos run unitree-go2-stairs --robot-ip 192.168.123.161

# 回退 DimOS MuJoCo（menagerie 场景）
dimos --simulation --mujoco-backend dimos run unitree-go2-stairs
```

楼梯 MJCF 由 `build_scene_stairs.py` 生成（DimOS `scene_stairs.xml` 与 Unitree `scene_dimos_stairs*.xml` 几何一致）。重建：

```bash
uv run python -m dimos.simulation.mujoco.build_scene_stairs
```

日志中仿真应出现：

`Go2 stair sport mode active ... note=(DimOS MuJoCo mirrors Sport API via SHM)`

以及 debug 级：`MuJoCo sport SHM updated`。

## 扩展仿真保真度

1. 调 `sport_state._recompute_derived_gains` 中的增益系数。  
2. 为 Go2 提供专用 MuJoCo ONNX（替换 `model.py` 中 go1 回退）。  
3. 在真机 `StairLocomotionConfig` 中设置 `use_cross_step=True` 与官方 High_motion_control 一致。
