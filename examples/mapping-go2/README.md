# Unitree Go2 自主探索与地图绘制

基于DimOS框架的Unitree Go2机器狗自主探索系统，采用多Agent协作架构实现未知环境探索和栅格地图构建。

## 功能特性

- ✅ **自主探索**: 基于前沿探索算法的自主导航
- ✅ **实时建图**: SLAM与栅格地图实时构建
- ✅ **智能避障**: 动态障碍物检测与规避
- ✅ **多Agent协作**: 感知、规划、地图、审查四大Agent协同工作
- ✅ **MCP集成**: 通过MCP协议暴露技能，支持LLM控制

## 架构设计

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
```

## 目录结构

```
examples/mapping-go2/
├── README.md                           # 本文件
├── go2_autonomous_exploration.py       # 主Blueprint入口
├── modules/                            # Agent模块
│   ├── perception_agent_module.py      # 感知Agent
│   ├── planning_control_agent_module.py # 规划控制Agent
│   ├── mapping_agent_module.py         # 地图Agent
│   └── review_agent_module.py          # 审查Agent
├── skills/                             # 技能容器
│   ├── exploration_skill_container.py  # 探索技能
│   ├── perception_skill_container.py   # 感知技能
│   └── mapping_skill_container.py      # 地图技能
└── system_prompts/                     # 系统提示词
    └── exploration_system_prompt.py    # 探索任务提示词
```

## 快速开始

### 安装依赖

```bash
# 安装DimOS及Unitree支持
cd /home/sgk/work/topsun_dimos
uv sync --all-extras --no-extra dds
source .venv/bin/activate
```

### 运行示例

```bash
# 1. 回放模式（使用录制数据，无需硬件）
dimos --replay run go2-autonomous-exploration

# 2. 仿真模式（MuJoCo仿真）
dimos --simulation run go2-autonomous-exploration

# 3. 真实硬件
export ROBOT_IP=192.168.123.161
python examples/mapping-go2/go2_autonomous_exploration.py
```

### 与Agent交互

```bash
# 发送命令给Agent
dimos agent-send "start exploration"
dimos agent-send "get exploration progress"
dimos agent-send "pause exploration"

# 调用MCP技能
dimos mcp list-tools
dimos mcp call start_exploration
dimos mcp call get_current_map
dimos mcp call get_coverage_rate
dimos mcp call navigate_to_goal --arg x=2.0 --arg y=1.5
```

## 核心模块说明

### 1. Perception Agent (感知Agent)

**职责**: 环境感知、障碍物检测、可通行区域识别

**输入流**:
- `color_image` - RGB相机图像
- `depth_image` - 深度图像
- `point_cloud` - 点云数据
- `imu` - IMU数据

**输出流**:
- 障碍物信息
- 可通行区域
- 感知状态

**技能**:
- `detect_obstacles()` - 检测障碍物
- `identify_traversable_area()` - 识别可通行区域
- `get_perception_quality()` - 获取感知质量
- `check_sensor_status()` - 检查传感器状态

### 2. Planning & Control Agent (规划控制Agent)

**职责**: 前沿探索、路径规划、运动控制

**输入流**:
- `current_pose` - 当前位姿
- 地图数据
- 障碍物信息
- 前沿点集合

**输出流**:
- `cmd_vel` - 速度控制指令
- `goal_pose` - 目标位姿
- 规划路径
- 探索状态

**技能**:
- `start_exploration()` - 开始自主探索
- `pause_exploration()` - 暂停探索
- `stop_exploration()` - 停止探索
- `select_next_frontier(strategy)` - 选择前沿点
- `navigate_to_goal(x, y)` - 导航到目标
- `emergency_stop()` - 紧急停止
- `get_exploration_status()` - 获取探索状态
- `set_exploration_strategy(strategy)` - 设置探索策略

### 3. Mapping Agent (地图构建Agent)

**职责**: SLAM、栅格地图维护、探索覆盖率统计

**输入流**:
- `point_cloud` - 点云数据
- `current_pose` - 当前位姿
- `odom` - 里程计数据

**输出流**:
- 占据栅格地图
- 前沿点
- 地图元数据
- 探索覆盖率

**技能**:
- `get_current_map()` - 获取地图状态
- `save_map(filename)` - 保存地图
- `get_coverage_rate()` - 获取覆盖率
- `reset_map()` - 重置地图
- `get_map_statistics()` - 获取统计信息
- `get_frontier_info()` - 获取前沿点信息

### 4. Review Agent (审查Agent)

**职责**: 任务监控、性能评估、异常检测

**技能**:
- `get_exploration_progress()` - 获取探索进度
- `get_system_health()` - 获取系统健康状态
- `generate_report()` - 生成探索报告
- `check_anomalies()` - 检查异常

## 配置参数

可以通过环境变量配置系统参数：

```bash
# 地图参数
export DIMOS_MAP_RESOLUTION=0.05  # 地图分辨率（米/像素）
export DIMOS_MAP_SIZE=400,400     # 地图尺寸（像素）

# 探索参数
export DIMOS_EXPLORATION_STRATEGY=wavefront  # wavefront | nearest | random
export DIMOS_EXPLORATION_RADIUS=10.0         # 探索半径（米）
export DIMOS_COVERAGE_THRESHOLD=0.85         # 探索完成阈值
export DIMOS_FRONTIER_MIN_SIZE=10            # 最小前沿点数量

# 安全参数
export DIMOS_MAX_LINEAR_VELOCITY=0.5      # 最大线速度（m/s）
export DIMOS_MAX_ANGULAR_VELOCITY=1.0     # 最大角速度（rad/s）
export DIMOS_MIN_OBSTACLE_DISTANCE=0.3    # 最小障碍物距离（m）
```

## 可视化

系统集成了Rerun可视化，可以实时查看：

- 机器人位姿与轨迹
- 栅格地图与前沿点
- 规划路径
- 障碍物检测结果
- 探索覆盖率热力图

```bash
# 启动可视化
python examples/mapping-go2/go2_autonomous_exploration.py --viewer rerun

# 或单独启动Rerun bridge
dimos rerun-bridge
```

## 安全注意事项

⚠️ **在真实硬件上运行前，请确保**:

1. **运动安全**:
   - 速度限制已正确配置
   - 最小障碍物距离 > 0.3m
   - 紧急停止功能已测试

2. **环境安全**:
   - 探索区域无危险（楼梯、水域等）
   - 探索边界已正确设置
   - 有足够的空间供机器人移动

3. **系统安全**:
   - 电池电量充足
   - 传感器工作正常
   - 网络连接稳定

4. **测试流程**:
   - 先在仿真中充分测试
   - 在安全区域进行小范围测试
   - 逐步扩大探索范围

## 性能指标

- **探索效率**: 单位时间覆盖面积 (m²/min)
- **地图质量**: 地图准确度、一致性
- **实时性**: 控制循环频率 (目标 > 10Hz)
- **资源占用**: CPU、内存使用率
- **探索完成时间**: 达到覆盖率阈值的时间

## 故障排查

### Blueprint构建失败

```bash
# 检查模块导入
python -c "from examples.mapping_go2.go2_autonomous_exploration import *"

# 查看详细错误
python examples/mapping-go2/go2_autonomous_exploration.py --verbose
```

### 技能未被识别

1. 确保`@skill`装饰器正确使用
2. 确保有完整的docstring
3. 确保所有参数有类型注解
4. 检查McpServer是否在blueprint中

### 数据流问题

```bash
# 查看所有话题
dimos lcmspy

# 监听特定话题
dimos topic echo /go2/color_image
dimos topic echo /cmd_vel
```

## 开发指南

### 添加新技能

1. 在对应的技能容器中添加`@skill`方法
2. 编写完整的docstring和类型注解
3. 返回描述性字符串
4. 更新系统提示词

```python
@skill
def my_new_skill(self, param: float) -> str:
    """技能描述。
    
    Args:
        param: 参数描述
    
    Returns:
        执行结果描述
    """
    # 实现
    return "执行成功"
```

### 修改探索策略

编辑 `modules/planning_control_agent_module.py` 中的前沿选择逻辑。

### 自定义地图参数

编辑 `modules/mapping_agent_module.py` 中的地图配置。

## 测试

```bash
# 运行单元测试
uv run pytest examples/mapping-go2/ -v

# 类型检查
uv run mypy examples/mapping-go2/

# 代码格式检查
uv run ruff check examples/mapping-go2/
```

## 参考资料

- [DimOS文档](../../docs/)
- [AGENTS.md](../../AGENTS.md) - Agent系统指南
- [CLAUDE_GO2_EXPLORATION.md](../../CLAUDE_GO2_EXPLORATION.md) - 详细设计文档
- [Unitree Go2文档](../../docs/platforms/quadruped/go2/index.md)

## 贡献

欢迎提交Issue和Pull Request！

## 许可证

Apache License 2.0

---

**版本**: v1.0  
**创建日期**: 2026-05-13  
**维护者**: sguanke  
**适用平台**: Unitree Go2 (Pro/Air)  
**DimOS版本**: dev branch
