# Unitree Go2 自主探索与地图绘制 - Claude 配置文件

## 项目概述

为Unitree Go2机器狗实现自主探索功能，能够在未知环境中自主导航并绘制栅格地图（occupancy grid map）。采用多Agent架构，包括感知Agent、规划控制Agent、地图构建Agent和审查Agent。

## 技术栈

- **框架**: DimOS (Dimensional Operating System)
- **机器人平台**: Unitree Go2 (quadruped)
- **编程语言**: Python 3.12
- **核心依赖**: 
  - dimos核心模块 (Module, Blueprint, Stream)
  - LCM/SHM传输层
  - 现有的导航、感知、地图模块

## 架构设计

### 多Agent架构

```
┌─────────────────────────────────────────────────────────────┐
│                    McpServer (MCP协议)                       │
│              统一的技能注册与调用接口                          │
└─────────────────────────────────────────────────────────────┘
                              │
        ┌─────────────────────┼─────────────────────┐
        │                     │                     │
┌───────▼────────┐  ┌────────▼────────┐  ┌────────▼────────┐
│ Perception     │  │ Planning &      │  │ Mapping         │
│ Agent          │  │ Control Agent   │  │ Agent           │
│                │  │                 │  │                 │
│ - 环境感知     │  │ - 路径规划      │  │ - SLAM          │
│ - 障碍物检测   │  │ - 前沿探索      │  │ - 栅格地图构建  │
│ - 目标识别     │  │ - 运动控制      │  │ - 地图更新      │
└────────────────┘  └─────────────────┘  └─────────────────┘
        │                     │                     │
        └─────────────────────┼─────────────────────┘
                              │
                    ┌─────────▼─────────┐
                    │  Review Agent     │
                    │                   │
                    │  - 任务监控       │
                    │  - 性能评估       │
                    │  - 异常检测       │
                    └───────────────────┘
                              │
                    ┌─────────▼─────────┐
                    │  Go2 Connection   │
                    │  (硬件接口层)      │
                    └───────────────────┘
```

### 模块组织结构

**重要**: 所有代码放在 `examples/mapping-go2/` 目录下，作为独立示例项目。

```
examples/mapping-go2/
├── README.md                              # 项目说明文档
├── go2_autonomous_exploration.py          # 主Blueprint入口
├── modules/                               # Agent模块
│   ├── __init__.py
│   ├── perception_agent_module.py         # 感知Agent模块
│   ├── planning_control_agent_module.py   # 规控Agent模块
│   ├── mapping_agent_module.py            # 地图Agent模块
│   └── review_agent_module.py             # 审查Agent模块
├── skills/                                # 技能容器
│   ├── __init__.py
│   ├── exploration_skill_container.py     # 探索技能容器
│   ├── perception_skill_container.py      # 感知技能容器
│   └── mapping_skill_container.py         # 地图技能容器
└── system_prompts/                        # 系统提示词
    ├── __init__.py
    └── exploration_system_prompt.py       # 探索任务系统提示词
```

## 核心模块设计

### 1. Perception Agent (感知Agent)

**职责**:
- 处理相机、激光雷达数据流
- 实时障碍物检测与跟踪
- 环境特征提取
- 可通行区域识别

**输入流**:
- `color_image: In[Image]` - RGB相机图像
- `depth_image: In[Image]` - 深度图像
- `point_cloud: In[PointCloud2]` - 点云数据
- `imu: In[Imu]` - IMU数据

**输出流**:
- `obstacles: Out[ObstacleArray]` - 检测到的障碍物
- `traversable_area: Out[OccupancyGrid]` - 可通行区域
- `perception_status: Out[PerceptionStatus]` - 感知状态

**关键技能**:
- `detect_obstacles() -> str` - 检测当前视野内的障碍物
- `identify_traversable_area() -> str` - 识别可通行区域
- `get_perception_quality() -> str` - 获取感知质量评估

### 2. Planning & Control Agent (规划控制Agent)

**职责**:
- 前沿探索（Frontier Exploration）
- 路径规划（A* / RRT）
- 运动控制与避障
- 探索策略决策

**输入流**:
- `current_pose: In[PoseStamped]` - 当前位姿
- `occupancy_grid: In[OccupancyGrid]` - 占据栅格地图
- `obstacles: In[ObstacleArray]` - 障碍物信息
- `frontiers: In[FrontierArray]` - 前沿点集合

**输出流**:
- `cmd_vel: Out[Twist]` - 速度控制指令
- `goal_pose: Out[PoseStamped]` - 目标位姿
- `path: Out[Path]` - 规划路径
- `exploration_status: Out[ExplorationStatus]` - 探索状态

**关键技能**:
- `start_exploration() -> str` - 开始自主探索
- `pause_exploration() -> str` - 暂停探索
- `select_next_frontier(strategy: str) -> str` - 选择下一个探索目标
- `navigate_to_goal(x: float, y: float) -> str` - 导航到指定位置
- `emergency_stop() -> str` - 紧急停止

**依赖模块**:
- 使用现有的 `dimos/navigation/frontier_exploration/wavefront_frontier_goal_selector.py`
- 使用现有的 `dimos/navigation/replanning_a_star/` 路径规划器

### 3. Mapping Agent (地图构建Agent)

**职责**:
- SLAM（同步定位与地图构建）
- 栅格地图维护与更新
- 地图保存与加载
- 探索覆盖率统计

**输入流**:
- `point_cloud: In[PointCloud2]` - 点云数据
- `current_pose: In[PoseStamped]` - 当前位姿
- `odom: In[Odometry]` - 里程计数据

**输出流**:
- `occupancy_grid: Out[OccupancyGrid]` - 占据栅格地图
- `frontiers: Out[FrontierArray]` - 前沿点
- `map_metadata: Out[MapMetadata]` - 地图元数据
- `coverage_rate: Out[Float32]` - 探索覆盖率

**关键技能**:
- `get_current_map() -> str` - 获取当前地图状态
- `save_map(filename: str) -> str` - 保存地图到文件
- `get_coverage_rate() -> str` - 获取探索覆盖率
- `reset_map() -> str` - 重置地图

**依赖模块**:
- 使用现有的 `dimos/mapping/occupancy/` 占据栅格地图
- 使用现有的 `dimos/mapping/voxels.py` 体素地图
- 集成 `dimos/mapping/costmapper.py` 代价地图

### 4. Review Agent (审查Agent)

**职责**:
- 监控各Agent运行状态
- 性能指标统计与分析
- 异常检测与告警
- 探索任务进度跟踪

**输入流**:
- `perception_status: In[PerceptionStatus]`
- `exploration_status: In[ExplorationStatus]`
- `map_metadata: In[MapMetadata]`
- `system_health: In[SystemHealth]`

**输出流**:
- `review_report: Out[ReviewReport]` - 审查报告
- `alerts: Out[AlertArray]` - 告警信息

**关键技能**:
- `get_exploration_progress() -> str` - 获取探索进度
- `get_system_health() -> str` - 获取系统健康状态
- `generate_report() -> str` - 生成探索报告
- `check_anomalies() -> str` - 检查异常情况

## 数据流设计

### 核心数据流

1. **感知流**: 相机/激光雷达 → Perception Agent → 障碍物/可通行区域
2. **地图流**: 点云/位姿 → Mapping Agent → 占据栅格/前沿点
3. **控制流**: 地图/障碍物 → Planning Agent → 速度指令 → Go2硬件
4. **监控流**: 各Agent状态 → Review Agent → 报告/告警

### 传输层选择

- **图像数据**: 使用 `SHMTransport` (共享内存，高效)
- **点云数据**: 使用 `pSHMTransport` (压缩共享内存)
- **控制指令**: 使用 `LCMTransport` (低延迟)
- **状态信息**: 使用 `LCMTransport`

## 实现步骤

### Phase 1: 基础模块实现
1. 创建Perception Agent模块，集成现有感知能力
2. 创建Mapping Agent模块，集成SLAM和栅格地图
3. 创建Planning & Control Agent模块，集成前沿探索
4. 创建Review Agent模块，实现基础监控

### Phase 2: 技能容器实现
1. 实现ExplorationSkillContainer，定义探索相关@skill方法
2. 实现PerceptionSkillContainer，定义感知相关@skill方法
3. 实现MappingSkillContainer，定义地图相关@skill方法
4. 为每个技能编写完整的docstring和类型注解

### Phase 3: Blueprint组装
1. 创建go2_autonomous_exploration blueprint
2. 使用autoconnect()连接所有模块
3. 配置合适的Transport（SHM for images, LCM for control）
4. 集成McpServer和McpClient

### Phase 4: 系统提示词
1. 编写exploration_system_prompt.py
2. 定义探索任务的目标和约束
3. 列出所有可用技能及其使用场景
4. 添加安全规则和异常处理指导

### Phase 5: 测试与优化
1. 使用--replay模式测试（无需真实硬件）
2. 使用--simulation模式在MuJoCo中测试
3. 在真实Go2硬件上测试
4. 性能优化和参数调优

## 配置参数

### GlobalConfig扩展

```python
# 探索相关配置
exploration_strategy: str = "wavefront"  # wavefront | random | greedy
frontier_min_size: int = 10              # 最小前沿点数量
exploration_radius: float = 10.0         # 探索半径（米）
map_resolution: float = 0.05             # 地图分辨率（米/像素）
map_size: tuple[int, int] = (400, 400)   # 地图尺寸（像素）
coverage_threshold: float = 0.85         # 探索完成阈值
```

### 运行配置

```bash
# 回放模式（使用录制数据）
dimos --replay run go2-autonomous-exploration

# 仿真模式
dimos --simulation run go2-autonomous-exploration

# 真实硬件
export ROBOT_IP=192.168.123.161
dimos run go2-autonomous-exploration --daemon

# 查看探索状态
dimos agent-send "get exploration progress"
dimos mcp call get_current_map
dimos mcp call get_coverage_rate
```

## 关键技术点

### 1. 前沿探索算法
- 使用现有的 `WavefrontFrontierGoalSelector`
- 实现前沿点评分机制（距离、信息增益、可达性）
- 动态更新前沿点集合

### 2. SLAM集成
- 集成现有的点云处理模块
- 实现增量式地图更新
- 处理闭环检测（可选）

### 3. 避障策略
- 实时障碍物检测
- 动态窗口法（DWA）或时间弹性带（TEB）
- 紧急避障机制

### 4. 多Agent协调
- 使用RPC进行Agent间通信
- 定义清晰的Spec接口
- 避免循环依赖

### 5. 可视化
- 集成Rerun可视化
- 实时显示地图、路径、前沿点
- 显示探索覆盖率热力图

## 安全考虑

1. **运动安全**:
   - 速度限制（线速度 < 0.5 m/s，角速度 < 1.0 rad/s）
   - 最小障碍物距离（> 0.3m）
   - 紧急停止机制

2. **系统安全**:
   - 看门狗定时器（超时自动停止）
   - 传感器故障检测
   - 电池电量监控

3. **探索边界**:
   - 定义探索区域边界
   - 防止机器人走失
   - 返回起点功能

## 测试策略

### 单元测试
- 每个模块的独立测试
- 技能方法的功能测试
- 边界条件测试

### 集成测试
- Blueprint构建测试
- 数据流连接测试
- Agent间通信测试

### 系统测试
- 回放模式端到端测试
- 仿真环境测试
- 真实硬件测试

## 性能指标

- **探索效率**: 单位时间覆盖面积
- **地图质量**: 地图准确度、一致性
- **实时性**: 控制循环频率（目标 > 10Hz）
- **资源占用**: CPU、内存使用率
- **探索完成时间**: 达到覆盖率阈值的时间

## 参考资料

### DimOS文档
- [Modules](docs/usage/modules.md)
- [Blueprints](docs/usage/blueprints.md)
- [Agent System](docs/agents/)
- [Navigation](docs/capabilities/navigation/native/index.md)

### 现有实现参考
- `dimos/robot/unitree/go2/blueprints/agentic/unitree_go2_agentic.py`
- `dimos/navigation/frontier_exploration/wavefront_frontier_goal_selector.py`
- `dimos/agents/skills/navigation.py`
- `dimos/mapping/occupancy/`

### 相关论文
- Frontier-Based Exploration
- Occupancy Grid Mapping
- SLAM算法（ORB-SLAM, Cartographer等）

## 开发规范

### 代码风格
- 遵循DimOS代码规范
- 使用类型注解（mypy strict mode）
- 完整的docstring（Google风格）
- 运行pre-commit hooks

### Git工作流
- 分支命名: `feat/go2-autonomous-exploration`
- PR目标分支: `dev`
- 提交信息: 清晰描述变更内容
- 避免force-push

### 文档要求
- 每个模块添加README.md
- 技能方法必须有完整docstring
- 更新AGENTS.md中的blueprint列表
- 添加使用示例

## 下一步行动

1. **创建项目结构**: 建立上述目录和文件框架
2. **实现Perception Agent**: 从感知模块开始，因为它是数据源
3. **实现Mapping Agent**: 地图是探索的基础
4. **实现Planning Agent**: 核心探索逻辑
5. **实现Review Agent**: 监控和评估
6. **组装Blueprint**: 连接所有模块
7. **编写系统提示词**: 指导LLM使用技能
8. **测试验证**: 从回放到仿真到真机

## 注意事项

- **不要重复造轮子**: 优先使用dimos现有模块
- **保持模块独立**: 每个Agent应该是独立的Module
- **清晰的接口**: 使用Spec定义模块间依赖
- **完整的类型注解**: 所有In/Out流和方法参数
- **技能返回字符串**: @skill方法必须返回描述性字符串
- **避免阻塞**: 长时间任务使用异步或线程
- **日志记录**: 使用dimos.utils.logging记录关键事件
- **错误处理**: 优雅处理传感器故障、通信中断等异常

---

**版本**: v1.0  
**创建日期**: 2026-05-13  
**作者**: Claude (Opus 4.7)  
**适用平台**: Unitree Go2 (Pro/Air)  
**DimOS版本**: dev branch
