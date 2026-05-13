# Unitree Go2 自主探索与栅格地图保存 - Claude 配置文件

## 项目概述

为Unitree Go2机器狗实现**自主探索与栅格地图保存**功能，能够在未知环境中自主导航、实时构建栅格地图，并自动保存为ROS标准格式（PGM+YAML）。

### 核心目标

1. **自主探索**: 自动检测未探索区域（前沿点），智能选择探索目标，A*路径规划 + 动态避障
2. **栅格地图保存**: 实时构建栅格地图，自动保存（定时）+ 手动保存（MCP技能），ROS标准格式

### 实现方式

使用DimOS内置模块组装完整系统，包括：
- **感知层**: SpatialMemory + PerceiveLoopSkill
- **地图层**: VoxelGridMapper + CostMapper
- **导航层**: ReplanningAStarPlanner + WavefrontFrontierExplorer + PatrollingModule
- **保存层**: MapSaverModule（自动保存 + 手动保存）
- **技能层**: NavigationSkillContainer + MCP Server/Client

## 技术栈

- **框架**: DimOS (Dimensional Operating System)
- **机器人平台**: Unitree Go2 (quadruped)
- **编程语言**: Python 3.12
- **核心依赖**: 
  - dimos核心模块 (Module, Blueprint, Stream)
  - LCM/SHM传输层
  - DimOS内置的导航、感知、地图模块
  - MapSaverModule（地图保存模块）

## 系统架构

### 完整系统架构

```
┌─────────────────────────────────────────────────────────────┐
│                    McpServer (MCP协议)                       │
│              统一的技能注册与调用接口                          │
└─────────────────────────────────────────────────────────────┘
                              │
        ┌─────────────────────┼─────────────────────┐
        │                     │                     │
┌───────▼────────┐  ┌────────▼────────┐  ┌────────▼────────┐
│ Spatial        │  │ VoxelGrid       │  │ Replanning      │
│ Memory         │  │ Mapper          │  │ A* Planner      │
│                │  │                 │  │                 │
│ - 物体跟踪     │  │ - 点云->3D地图  │  │ - A*路径规划    │
│ - 空间记忆     │  │ - 体素网格      │  │ - 动态重规划    │
│ - 物体定位     │  │ - 障碍物检测    │  │ - 避障控制      │
└────────────────┘  └─────────────────┘  └─────────────────┘
        │                     │                     │
        └─────────────────────┼─────────────────────┘
                              │
        ┌─────────────────────┼─────────────────────┐
        │                     │                     │
┌───────▼────────┐  ┌────────▼────────┐  ┌────────▼────────┐
│ Perceive       │  │ CostMapper      │  │ Wavefront       │
│ Loop Skill     │  │                 │  │ Frontier        │
│                │  │                 │  │ Explorer        │
│ - 感知循环     │  │ - 代价地图生成  │  │ - 前沿检测      │
│ - 物体识别     │  │ - 障碍物膨胀    │  │ - 目标选择      │
└────────────────┘  └─────────────────┘  └─────────────────┘
        │                     │                     │
        └─────────────────────┼─────────────────────┘
                              │
                    ┌─────────▼─────────┐
                    │  MapSaverModule   │
                    │                   │
                    │  - 自动保存       │
                    │  - 手动保存       │
                    │  - PGM+YAML格式   │
                    └───────────────────┘
```

### 模块组织结构

**实际实现**: 所有代码放在 `examples/mapping-go2/` 目录下，作为独立示例项目。

```
examples/mapping-go2/
├── README.md                              # 项目说明文档
├── go2_autonomous_exploration.py          # 主Blueprint入口
└── map_saver_module.py                    # 地图保存模块
```

**使用的DimOS内置模块**:
- `dimos.perception.spatial_perception.SpatialMemory` - 空间记忆
- `dimos.perception.perceive_loop_skill.PerceiveLoopSkill` - 感知循环技能
- `dimos.mapping.voxels.VoxelGridMapper` - 体素地图
- `dimos.mapping.costmapper.CostMapper` - 代价地图
- `dimos.navigation.replanning_a_star.ReplanningAStarPlanner` - A*规划器
- `dimos.navigation.frontier_exploration.WavefrontFrontierExplorer` - 前沿探索
- `dimos.navigation.patrolling.PatrollingModule` - 巡逻模块
- `dimos.agents.skills.navigation.NavigationSkillContainer` - 导航技能容器
- `dimos.agents.mcp.McpServer` - MCP服务器
- `dimos.agents.mcp.McpClient` - MCP客户端

## 核心模块设计

### 1. SpatialMemory (空间记忆)
- 跟踪环境中的物体
- 维护物体的空间位置
- 提供物体查询接口
- 支持物体持久化记忆

### 2. PerceiveLoopSkill (感知循环技能)
- 持续感知环境
- 物体识别和分类
- 为LLM提供感知技能接口
- 支持查询"看到了什么"

### 3. VoxelGridMapper (体素地图)
- 将点云数据转换为3D体素网格
- 实时更新地图
- 障碍物检测

### 4. CostMapper (代价地图)
- 生成导航代价地图
- 障碍物膨胀（安全距离）
- 为路径规划提供代价信息

### 5. ReplanningAStarPlanner (A*规划器)
- A*算法路径规划
- 动态重规划（环境变化时）
- 平滑路径跟踪
- 避障控制

### 6. WavefrontFrontierExplorer (前沿探索)
- Wavefront算法检测前沿点
- 自动选择最优探索目标
- 信息增益评估
- 探索完成判断

### 7. PatrollingModule (巡逻模块)
- 管理探索目标队列
- 目标切换逻辑
- 探索进度跟踪

### 8. MapSaverModule (地图保存模块) ⭐ 核心新增

**职责**: 自动和手动保存栅格地图

**功能**:
- **自动保存**: 每60秒自动保存地图到 `maps/` 目录
- **停止保存**: 系统停止时自动保存最终地图
- **手动保存**: 通过MCP技能随时保存地图
- **格式转换**: 将CostMapper的代价地图转换为PGM+YAML格式

**输入流**:
- `cost_map: In[OccupancyGrid]` - 代价地图

**MCP技能**:
- `save_map_now(name: str) -> str` - 立即保存地图
- `get_save_status() -> str` - 获取保存状态
- `set_auto_save(enabled: bool) -> str` - 启用/禁用自动保存

**保存格式**:
- **PGM文件**: 灰度图像（0=障碍，254=自由，205=未知）
- **YAML文件**: 元数据（分辨率、原点、阈值等）
- **兼容性**: ROS Navigation Stack标准格式

### 9. NavigationSkillContainer (导航技能容器)
- 为LLM提供导航技能接口
- 支持目标设置、取消、状态查询
- 集成到MCP协议

## 数据流设计

### 感知流
```
相机 + 点云 → SpatialMemory → 物体跟踪
                    ↓
            PerceiveLoopSkill → 物体识别
```

### 地图流
```
点云传感器 → VoxelGridMapper → 3D体素地图
                    ↓
              CostMapper → 代价地图
                    ↓
            MapSaverModule → PGM+YAML文件
```

### 导航流
```
代价地图 → WavefrontFrontierExplorer → 前沿点
                    ↓
           PatrollingModule → 探索目标
                    ↓
       ReplanningAStarPlanner → 路径 + 控制指令
                    ↓
                Go2机器人
```

### 地图保存流 ⭐
```
CostMapper → 代价地图 → MapSaverModule
                              ↓
                    ┌─────────┴─────────┐
                    │                   │
              自动保存定时器        MCP技能调用
                    │                   │
                    └─────────┬─────────┘
                              ↓
                    保存为PGM+YAML文件
                    (maps/目录)
```

## 可用技能

### 导航技能 (NavigationSkillContainer)

- **set_goal(x: float, y: float, theta: float)**: 设置导航目标
- **cancel_goal()**: 取消当前导航目标
- **get_navigation_state()**: 获取当前导航状态

### 感知技能 (PerceiveLoopSkill)

- **perceive_objects()**: 感知当前环境中的物体
- **query_spatial_memory(query: str)**: 查询空间记忆

### 地图保存技能 (MapSaverModule) ⭐

- **save_map_now(name: str)**: 立即保存当前地图
- **get_save_status()**: 获取地图保存状态
- **set_auto_save(enabled: bool)**: 启用/禁用自动保存

## 运行方式

```bash
# 激活虚拟环境
source .venv/bin/activate

# 1. 真实硬件（需要连接Go2机器人）
export ROBOT_IP=192.168.123.161
python examples/mapping-go2/go2_autonomous_exploration.py

# 2. 带可视化运行
python examples/mapping-go2/go2_autonomous_exploration.py --viewer rerun

# 3. 查看帮助信息
python examples/mapping-go2/go2_autonomous_exploration.py --help
```

## 紧急停止方法

### 方法1: 键盘中断（最快）
```bash
Ctrl + C
```

### 方法2: MCP命令停止导航
```bash
dimos mcp call cancel_goal
```

### 方法3: Go2遥控器（硬件级别）
- 按下遥控器的紧急停止按钮（L2+R2同时按下）

## 配置参数

### 地图保存配置
```python
MapSaverModule.blueprint(
    save_dir="maps",              # 保存目录
    auto_save_interval=60.0,      # 自动保存间隔（秒）
    enable_auto_save=True,        # 启用自动保存
)
```

### 前沿探索配置
```python
WavefrontFrontierExplorer.blueprint(
    min_frontier_perimeter=0.5,      # 最小前沿周长（米）
    safe_distance=3.0,                # 安全距离（米）
    lookahead_distance=5.0,           # 前瞻距离（米）
    max_explored_distance=10.0,       # 最大探索距离（米）
    info_gain_threshold=0.03,         # 信息增益阈值
    goal_timeout=15.0,                # 目标超时（秒）
)
```

### 地图配置
```python
# VoxelGridMapper配置
VoxelGridMapper.blueprint(
    voxel_size=0.05,                  # 体素尺寸（米）
    max_range=10.0,                   # 最大检测范围（米）
)

# CostMapper配置
CostMapper.blueprint(
    inflation_radius=0.3,             # 障碍物膨胀半径（米）
    cost_scaling_factor=10.0,         # 代价缩放因子
)
```

### 导航配置
```python
# ReplanningAStarPlanner配置
ReplanningAStarPlanner.blueprint(
    max_speed=0.5,                    # 最大速度（m/s）
    goal_tolerance=0.3,               # 目标容差（米）
    replan_frequency=2.0,             # 重规划频率（Hz）
)
```

## 开发规范

### 代码组织
- 所有代码放在 `examples/mapping-go2/` 目录
- 使用DimOS内置模块，不重复造轮子
- 新增模块（如MapSaverModule）放在同目录下

### 命名规范
- 模块类名: PascalCase (如 `MapSaverModule`)
- 函数名: snake_case (如 `save_map_now`)
- 常量: UPPER_CASE (如 `DEFAULT_SAVE_DIR`)

### 文档规范
- 每个模块都有docstring说明
- README.md包含完整的使用说明
- 代码注释简洁明了

## 测试与验证

### 功能测试
1. **自主探索**: 机器人能否自动检测前沿点并导航
2. **地图构建**: 地图是否实时更新，障碍物是否正确标记
3. **地图保存**: 自动保存和手动保存是否正常工作
4. **可视化**: Rerun是否正确显示地图、路径、前沿点
5. **紧急停止**: 各种停止方法是否有效

### 性能指标
- 探索覆盖率 > 90%
- 地图更新频率 > 1Hz
- 路径规划时间 < 1秒
- 地图保存时间 < 2秒

## 故障排查

### 地图未保存
1. 检查 `maps/` 目录是否存在
2. 查看MapSaverModule日志
3. 确认auto_save_interval配置正确

### 探索停滞
1. 检查是否还有可达的前沿点
2. 查看WavefrontFrontierExplorer日志
3. 调整前沿检测参数

### 导航失败
1. 检查代价地图是否生成
2. 查看路径规划器状态
3. 确认目标点可达

## 版本信息

- **版本**: v2.0
- **创建日期**: 2026-05-13
- **维护者**: sguanke
- **适用平台**: Unitree Go2 (Pro/Air)
- **DimOS版本**: dev branch

## 许可证

Apache License 2.0
