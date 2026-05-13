# Unitree Go2 自主探索与地图绘制

基于DimOS框架的Unitree Go2机器狗自主探索系统，使用DimOS内置的导航和地图模块实现真实的自主探索功能。

## 功能特性

- ✅ **自主探索**: 基于Wavefront前沿探索算法
- ✅ **实时建图**: 体素地图（VoxelGridMapper）+ 代价地图（CostMapper）
- ✅ **智能导航**: A*路径规划 + 动态重规划
- ✅ **前沿检测**: 自动检测未探索区域边界
- ✅ **MCP集成**: 通过MCP协议暴露导航技能，支持LLM控制

## 系统架构

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
```

## 核心模块

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

## 快速开始

### 安装依赖

```bash
cd /home/sgk/work/topsun_dimos
source .venv/bin/activate
```

### ⚠️ 紧急停止方法

**在测试过程中，如果机器人要撞障碍物，可以使用以下方法紧急停止：**

#### 方法1: 键盘中断（最快）
```bash
# 在运行程序的终端按下
Ctrl + C
```
这会立即终止程序，机器人会停止移动。

#### 方法2: MCP命令停止导航
```bash
# 在另一个终端执行
dimos mcp call cancel_goal
```
这会取消当前导航目标，机器人会停止移动。

#### 方法3: Go2遥控器（硬件级别）
- **按下遥控器的紧急停止按钮**（L2+R2同时按下）
- 这是硬件级别的停止，最安全

#### 方法4: 发送停止命令
```bash
# 在另一个终端执行
dimos agent-send "stop immediately"
```

#### 方法5: 让机器人趴下
```bash
# 使用Go2的趴下功能
python -c "from dimos.robot.unitree.go2.connection import GO2Connection; import os; conn = GO2Connection(os.getenv('ROBOT_IP')); conn.start(); conn.liedown()"
```

**推荐顺序**：
1. 首选 **Ctrl+C**（最快）
2. 备选 **遥控器紧急停止**（硬件级别）
3. 其他方法作为补充

**安全提示**：
- 测试时保持在遥控器范围内
- 确保测试区域有足够的安全空间
- 首次测试建议使用仿真模式
- 真实硬件测试时，建议有人随时准备按遥控器紧急停止

### 运行示例

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

**注意**：
- 回放模式和仿真模式需要先注册blueprint到DimOS系统
- 推荐直接使用Python运行脚本
- 真实硬件测试前，请确保已阅读"紧急停止方法"部分

### 与系统交互

```bash
# 发送命令给Agent
dimos agent-send "navigate to x=2, y=1"
dimos agent-send "cancel navigation"
dimos agent-send "what is the navigation state"

# 调用MCP技能
dimos mcp list-tools
dimos mcp call set_goal --arg x=2.0 --arg y=1.0 --arg theta=0.0
dimos mcp call cancel_goal
dimos mcp call get_navigation_state
```

## 可用技能

### 导航技能 (NavigationSkillContainer)

- **set_goal(x: float, y: float, theta: float)**: 设置导航目标
  - x, y: 目标位置（米）
  - theta: 目标朝向（弧度）

- **cancel_goal()**: 取消当前导航目标

- **get_navigation_state()**: 获取当前导航状态
  - 返回: IDLE, NAVIGATING, GOAL_REACHED等

### 感知技能 (PerceiveLoopSkill)

- **perceive_objects()**: 感知当前环境中的物体
  - 返回检测到的物体列表

- **query_spatial_memory(query: str)**: 查询空间记忆
  - 例如: "where is the chair?"
  - 返回物体的位置信息

### 地图保存技能 (MapSaverModule)

- **save_map_now(name: str)**: 立即保存当前地图
  - name: 文件名前缀（可选）
  - 返回保存的文件路径

- **get_save_status()**: 获取地图保存状态
  - 返回自动保存配置和保存历史

- **set_auto_save(enabled: bool)**: 启用/禁用自动保存
  - enabled: True启用，False禁用

## 地图保存

### 自动保存

系统会**自动保存地图**：

- **保存间隔**: 每60秒自动保存一次
- **保存目录**: `maps/` 目录
- **保存格式**: PGM + YAML（ROS标准格式）
- **文件命名**: `auto_YYYYMMDD_HHMMSS.pgm`
- **停止时保存**: 系统停止时自动保存最终地图为 `final_YYYYMMDD_HHMMSS.pgm`

### 手动保存

通过MCP技能手动保存地图：

```bash
# 立即保存地图
dimos mcp call save_map_now

# 保存并指定名称
dimos mcp call save_map_now --arg name="my_exploration"

# 查看保存状态
dimos mcp call get_save_status

# 启用/禁用自动保存
dimos mcp call set_auto_save --arg enabled=false
```

### 地图文件格式

保存的地图包含两个文件：

1. **PGM文件** (`map_name.pgm`): 灰度图像
   - 0 (黑色): 障碍物
   - 254 (白色): 自由空间
   - 205 (灰色): 未探索区域

2. **YAML文件** (`map_name.yaml`): 元数据
   ```yaml
   image: map_name.pgm
   resolution: 0.05  # 米/像素
   origin: [0.0, 0.0, 0.0]  # 地图原点
   negate: 0
   occupied_thresh: 0.65
   free_thresh: 0.196
   ```

### 使用保存的地图

保存的地图可以用于：

- **ROS导航**: 直接加载到ROS Navigation Stack
- **路径规划**: 离线路径规划
- **地图分析**: 分析探索覆盖率
- **可视化**: 使用图像查看器查看

```bash
# 查看地图
eog maps/auto_20260513_160000.pgm

# 在ROS中加载
rosrun map_server map_server maps/auto_20260513_160000.yaml
```

### 前沿探索参数

```python
# 在代码中配置WavefrontFrontierExplorer
WavefrontFrontierExplorer.blueprint(
    min_frontier_perimeter=0.5,      # 最小前沿周长（米）
    safe_distance=3.0,                # 安全距离（米）
    lookahead_distance=5.0,           # 前瞻距离（米）
    max_explored_distance=10.0,       # 最大探索距离（米）
    info_gain_threshold=0.03,         # 信息增益阈值
    goal_timeout=15.0,                # 目标超时（秒）
)
```

### 地图参数

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

### 导航参数

```python
# ReplanningAStarPlanner配置
ReplanningAStarPlanner.blueprint(
    max_speed=0.5,                    # 最大速度（m/s）
    goal_tolerance=0.3,               # 目标容差（米）
    replan_frequency=2.0,             # 重规划频率（Hz）
)
```

## 可视化

### 启动可视化

系统集成了Rerun可视化，**实时显示**探索过程：

```bash
# 使用Rerun可视化（推荐）
python examples/mapping-go2/go2_autonomous_exploration.py --viewer rerun

# 使用Rerun Web版本（浏览器中查看）
python examples/mapping-go2/go2_autonomous_exploration.py --viewer rerun-web

# 使用Foxglove
python examples/mapping-go2/go2_autonomous_exploration.py --viewer foxglove
```

### 可视化内容

**实时更新的内容**：

- **3D体素地图**: 环境的3D重建（VoxelGridMapper输出）
- **代价地图**: 导航代价分布，彩色热力图（CostMapper输出）
- **点云数据**: 激光雷达扫描的原始点云
- **相机图像**: RGB相机视图（左侧窗口）
- **机器人位姿**: 当前位置和朝向（实时更新）
- **规划路径**: A*规划的路径（绿色线条）
- **前沿点**: 未探索区域的边界（红色标记）
- **检测物体**: 识别到的物体（通过SpatialMemory，带标签）

### 可视化布局

Rerun使用分屏布局：
- **左侧**: 相机视图（2D图像）
- **右侧**: 3D世界视图（地图、路径、机器人、前沿点）
- **底部**: 时间轴（可回放历史数据）

所有数据都是**实时更新**的，可以看到机器人探索的全过程！

## 工作流程

### 自主探索流程

1. **初始化**
   - 启动所有模块
   - 初始化地图
   - 等待传感器数据

2. **地图构建**
   - VoxelGridMapper接收点云
   - 更新3D体素地图
   - CostMapper生成代价地图

3. **前沿检测**
   - WavefrontFrontierExplorer扫描地图
   - 检测已知/未知区域边界
   - 聚类形成前沿点

4. **目标选择**
   - 评估每个前沿点的信息增益
   - 考虑距离和可达性
   - 选择最优前沿点作为目标

5. **路径规划**
   - ReplanningAStarPlanner规划路径
   - 避开障碍物
   - 生成平滑轨迹

6. **运动控制**
   - 跟踪规划路径
   - 实时避障
   - 到达目标后返回步骤3

7. **完成判断**
   - 检查是否还有可达的前沿点
   - 评估探索覆盖率
   - 决定是否结束探索

## 数据流

```
【感知流】
相机 + 点云 → SpatialMemory → 物体跟踪
                    ↓
            PerceiveLoopSkill → 物体识别

【地图流】
点云传感器 → VoxelGridMapper → 3D体素地图
                    ↓
              CostMapper → 代价地图

【导航流】
代价地图 → WavefrontFrontierExplorer → 前沿点
                    ↓
           PatrollingModule → 探索目标
                    ↓
       ReplanningAStarPlanner → 路径 + 控制指令
                    ↓
                Go2机器人
```

## 故障排查

### 紧急情况处理

#### 机器人失控或行为异常

1. **立即停止**：按 `Ctrl+C` 或使用遥控器紧急停止
2. **检查日志**：查看是否有错误信息
3. **重启系统**：完全停止后重新启动

#### 机器人不响应停止命令

1. **使用遥控器**：L2+R2同时按下（硬件级别停止）
2. **断开电源**：作为最后手段，关闭机器人电源
3. **检查网络**：确认ROBOT_IP连接正常

### 系统无法启动

```bash
# 检查依赖
python -c "import dimos; print(dimos.__version__)"

# 检查Go2连接
export ROBOT_IP=192.168.123.161
ping $ROBOT_IP
```

### 地图未更新

1. 检查点云数据是否正常
2. 查看VoxelGridMapper日志
3. 确认传感器工作正常

```bash
# 监听点云话题
dimos topic echo /lidar
```

### 导航不工作

1. 检查代价地图是否生成
2. 查看路径规划器状态
3. 确认目标点可达

```bash
# 查看导航状态
dimos mcp call get_navigation_state

# 查看日志
dimos log -f
```

### 前沿点未检测到

1. 检查地图是否有未探索区域
2. 调整前沿检测参数
3. 查看WavefrontFrontierExplorer日志

## 性能优化

### CPU优化
- 调整worker数量: `n_workers=9`
- 降低地图更新频率
- 减小体素尺寸

### 内存优化
- 限制地图大小
- 定期清理旧数据
- 使用共享内存传输

### 实时性优化
- 提高控制频率
- 优化路径规划算法
- 减少不必要的计算

## 扩展开发

### 添加新的探索策略

继承`WavefrontFrontierExplorer`并重写目标选择逻辑：

```python
class CustomFrontierExplorer(WavefrontFrontierExplorer):
    def select_best_frontier(self, frontiers):
        # 自定义选择逻辑
        return best_frontier
```

### 集成其他传感器

添加新的传感器数据流：

```python
class CustomMapper(VoxelGridMapper):
    depth_camera: In[Image]
    
    def process_depth(self, image):
        # 处理深度图像
        pass
```

## 参考资料

- [DimOS文档](../../docs/)
- [导航模块文档](../../dimos/navigation/)
- [地图模块文档](../../dimos/mapping/)
- [Unitree Go2文档](../../docs/platforms/quadruped/go2/)

## 许可证

Apache License 2.0

---

**版本**: v2.0 (使用DimOS内置模块)  
**创建日期**: 2026-05-13  
**维护者**: sguanke  
**适用平台**: Unitree Go2 (Pro/Air)  
**DimOS版本**: dev branch
