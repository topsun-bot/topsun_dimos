# GO2 室内步越低矮障碍物

## 概述

`StepOverModule` 是 GO2 导航管线的自动组件，在机器人自主导航过程中实时检测前方低矮障碍物，判定是否可安全步越通过。

- **可步越**：允许机器人沿当前路径低速通过
- **不可步越**：取消导航目标 + 语音播报「前方障碍无法通过」

该模块非 LLM 工具，无需 Agent 手动调用。

## 判定逻辑

```
体素地图 → 前方 ROI 条带 → 空间滤波(开运算) → 2.5D 几何分析
    → 时间一致性(5帧) → 可越/不可越
```

### 判定条件（全部满足才可越）

| 条件 | 阈值 |
|------|------|
| 踏面高度 h | ≤ 0.10 m |
| 腹下净空 g | ≥ 0.12 m |
| 台面深度 d | ≤ 0.20 m |
| 孤立障碍宽度 | 0.10–0.50 m |
| 连续路沿 | 不限宽度，仅看 h + d + g |

### 噪点抑制

- **时间一致性**：可步越条件连续 5 帧（≈0.5 s @10 Hz）才触发
- **空间滤波**：3×3 灰度开运算，去除孤立高刺
- **高度死区**：< 5 cm 忽略
- **窄刺剔除**：宽度 < 10 cm 不计为障碍

## 配置参数

所有参数位于 `StepOverConfig`（`dimos/robot/unitree/go2/step_over_config.py`），可通过 YAML 覆盖：

```yaml
# 几何
max_step_height_m: 0.10
min_belly_clearance_m: 0.12
max_ledge_depth_m: 0.20
isolated_obstacle_width_min_m: 0.10
isolated_obstacle_width_max_m: 0.50
min_gap_width_m: 0.40
ignore_noise_m: 0.05

# 距离
analyze_distance_m: 1.0
execute_distance_m: 0.4
roi_start_m: 0.25
roi_end_m: 0.55
roi_width_margin_m: 0.1

# 噪点抑制
stable_frames_required: 5
morph_open_iterations: 1

# 机身
body_bottom_height_m: 0.22  # 真机标定

# 规划迟滞
planner_near_enter_m: 1.3
planner_far_exit_m: 1.7

# 语音
blocked_speech_text: "前方障碍无法通过"
```

## 集成方式

`StepOverModule` 添加到 `unitree_go2` blueprint（`unitree_go2.py`），自动继承到 `unitree_go2_agentic` 等 agentic 蓝图。

```
unitree_go2_basic → VoxelGridMapper → CostMapper → ReplanningAStarPlanner
    → ... → GoHome → StepOverModule
```

## 全局规划器迟滞

`GlobalPlanner._find_wide_path` 的 1.5 m 硬切换已改为迟滞：

- 距目标 > 1.7 m → voronoi（远场）
- 距目标 < 1.3 m → gradient（近场）
- [1.3, 1.7] m → 保持上一次选择

防止路径在目标附近左右横跳。

## 限制

1. **Go2 无专用步越 API**：可越时仅允许低速通过，不执行特殊步态。不做跳跃类 sport 命令。
2. **不做步越状态机迟滞**：判定使用固定阈值（如 10 cm），不做 9 cm 进入 / 11 cm 退出逻辑。
3. **不做惩罚分/代价比较**：不比较绕路代价与步越代价。
4. **动态障碍不步越**：人/明显移动物体不触发步越（保守默认）。

## 真机调试

1. 启动导航：`dimos run unitree-go2-agentic --robot-ip <IP>`
2. 准备测试物：8 cm、12 cm 路沿或木条（宽 ≥ 30 cm）
3. 观察日志中的判定输出：
   ```
   StepOver: h=0.080 d=0.100 g=0.140 width=0.450 n_obs=1 passable=True stable=5
   ```
4. 参数调优：
   - `body_bottom_height_m`：测量机器人站立时腹部距地面高度，填入真实值
   - `min_belly_clearance_m`：可根据实际测试调整安全余量
   - `stable_frames_required`：增大可减少误触发，但响应更慢

## 日志关键字

- `h`：障碍物高度（m）
- `d`：台面深度（m）
- `g`：腹下净空（m）
- `width`：障碍物横向宽度（m）
- `n_obs`：ROI 内独立障碍物数量
- `passable`：当前帧是否可越
- `stable`：连续可越帧计数
