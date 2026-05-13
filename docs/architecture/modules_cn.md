# DimOS 模块架构(中文版)

> `dimos/` 下 28 个顶层模块的语义地图。基于 `dev` 分支的 import 图与源码分析自动整理。**对外接口**(public surface)列表是您可以依赖、不必深挖源码的最小稳定契约。
>
> *英文原版见:[`modules.md`](/docs/architecture/modules.md)*

`dimos/__init__.py` 仅惰性加载 `dimos.porcelain.dimos` 中的 **`Dimos`**。绝大多数子包**没有在 `__init__.py` 里精心整理 export**(命名空间式布局)—— 除非显式增加了 re-export,否则请把**具体模块**当作公开 surface 来看。

---

## 模块总表(一览)

| 模块 | 层 | 一句话职责 |
|--------|-------|-----------------|
| **agents** | 大脑 | LLM 工具栈:`@skill` 装饰器、MCP 客户端/服务端、可选的 VL agent、导航/语音等技能容器 |
| **agents_deprecated** | 遗留 | 旧的 `OpenAIAgent` / 分词器 / **chromadb** 内存栈,被早期 demo 使用 |
| **skills** | 大脑(遗留) | 较旧的 **Pydantic `AbstractSkill` + `SkillLibrary`** 工具 schema(与 `@skill` RPC 路线并行) |
| **control** | 执行 | **100 Hz `ControlCoordinator`**、任务、tick 循环、机械臂关节裁决 |
| **core** | 运行时 | **模块、流、传输、蓝图、协调器、RPC、worker、全局配置** |
| **memory** | 数据(遗留) | embedding + **时间序列**存储(`EmbeddingMemory`,SQLite/Postgres 后端) |
| **memory2** | 数据 | **基于流的内存系统**:观测数据、存储、编解码器、`StreamModule` 集成 |
| **perception** | 感知 | 检测、跟踪、空间记忆、试验性的时序记忆 |
| **manipulation** | 运动 | 抓取/放置、规划(Drake/MJCF)、`control/` 下的轨迹/控制辅助器 |
| **mapping** | 空间 | 占据栅格、体素、OSM/Google Maps 工具、**`LatLon`** 模型 |
| **navigation** | 运动 | ROS 导航桥、巡逻、**视觉伺服**、前沿/探索 |
| **hardware** | 驱动 | 相机、激光雷达、机械臂、底盘、整机适配器 |
| **robot** | 产品 | **`dimos run` CLI**、蓝图注册表、Unitree/无人机/xArm 等机器人栈 |
| **simulation** | 仿真 | MuJoCo SHM、Genesis/Isaac/Unity 桩、**引擎注册表** |
| **teleop** | 输入 | Quest / 手机 / 键盘遥操作模块和蓝图 |
| **models** | ml | **VLM**、CLIP/MobileCLIP embedding、分割(EdgeTAM)、HuggingFace 包装器 |
| **stream** | 媒体 | **视频**(Webcam/RTSP/ROS)和**音频**(麦克、Whisper、TTS)管道 |
| **web** | UI | Flask/FastAPI **EdgeIO**、Svelte `dimos_interface`、地图 **websocket_vis** |
| **experimental** | wip | **不稳定 demo**(例如 `security_demo` 管道) |
| **porcelain** | api | **`Dimos` 门面对象**:本地/远程模块源、**`SkillsProxy`** |
| **spec** | 类型 | **`Spec` 标记协议** + 领域 `spec.perception` / `nav` / `mapping` / `control` |
| **protocol** | 传输 | **LCM/ROS/SHM/DDS/Redis** 发布-订阅、**LCMRPC**、TF、服务配置器 |
| **exceptions** | 遗留 | **专属 agent 内存**的异常类型层级(遗留 memory 路径) |
| **visualization** | 运维 | **Rerun 桥** + 初始化辅助器 |
| **utils** | 基础设施 | 日志、数据路径、CLI 工具(`lcmspy`、`dtop`、replay、foxglove……) |
| **types** | 类型 | **`Vector`**、`Timestamped`、**`RobotLocation`**、weak list、ROS polyfill |
| **msgs** | 类型 | **类 ROS 的强类型消息** + `DimosMsg` 协议 |
| **project** | ci | **仓库卫生测试**(例如日志 `getLogger` 守卫);不是版本号 API |
| **rxpy_backpressure** | 基础设施 | **`BackPressure`** 门面(Latest/Drop/Buffer 三种 observer 策略) |

### 跨模块共识(读完一次即可)

- **两套并行的 skill 系统**:`agents/` 中的 `@skill` + MCP 路线 vs `skills/` 中的 Pydantic `AbstractSkill` 路线。**新代码请优先用前者**。
- **`stream/` ≠ `core.stream`**:`dimos/stream/` 是*媒体管道*(视频 + 音频);`dimos.core.stream` 是*模块 I/O*原语(`In`/`Out`/`Transport`)。**两者命名相撞**。
- **`memory/` 是遗留**,`memory2/` 是当前在维护的观测/存储管道。
- **`project/`** 是一袋*CI 卫生测试*,**不是**版本/元数据模块 —— 版本号来自 `pyproject.toml`。
- **`exceptions/`** 只装遗留 memory 系统的错误类型,**不是**项目级错误命名空间。

---

## 顶层 `dimos` 包

> **简介。** 惰性入口:`dimos/__init__.py` 只对外暴露 `Dimos`,其它一切都需要从子包显式导入。

**对外接口**
- `Dimos`(`dimos.porcelain.dimos`)—— 构建/运行蓝图,可选远程 daemon 接线

**核心概念**
- *惰性包属性* —— `__getattr__` 在首次访问 `Dimos` 时才加载

**依赖**:(加载时)`dimos.porcelain`、`dimos.core`、`dimos.robot`……

---

## `dimos/agents/`

> **简介。** Agent *框架*:LLM **MCP** 栈(`McpClient` / `McpServer`)、**`@skill`** 装饰器、VL agent 模块,以及与具体机器人无关的**技能容器**(导航、GPS/地图、语音……)。

**对外接口**(无 `__init__.py`,以下是常见 import 方式)
- `skill` / `current_skill_context()` —— `dimos/agents/annotation.py` —— 把 RPC 方法标记为 LLM/MCP 工具;可选 MCP 进度上下文
- `McpClient`、`McpClientConfig` —— LangChain agent + HTTP MCP 工具 + LCM 流
- `McpServer` —— FastAPI JSON-RPC MCP 服务端,把模块的 `@skill` 暴露出去
- `McpAdapter` —— MCP 集成测试用的 adapter
- `AgentSpec` —— 表示"能接受 agent 指令"的 protocol
- `VLMAgent`、`VLMAgentConfig` —— 图像 I/O + 面向 VLM 的模块
- `AgentTestRunner` —— agent 栈的测试 harness 模块
- `SYSTEM_PROMPT` —— `dimos/agents/system_prompt.py`,默认 prompt 文本
- `ensure_ollama_model()`、`ollama_installed()` —— `dimos/agents/ollama_agent.py` 的 Ollama 工具(**不是**完整的 `Agent` 类)

**核心概念**
- *`@skill`* —— `@rpc` + `__skill__`;驱动 MCP 工具 schema 生成
- *MCP HTTP 桥* —— 通过 FastAPI 的外部工具协议(默认端口取自 `GlobalConfig`)
- *技能容器* —— `Module` 的子类,为某个场景一次性暴露多个 `@skill`

**依赖**:`dimos.core`、`dimos.spec`、`dimos.msgs`、`dimos.utils`,常常还有 `dimos.navigation`、`dimos.mapping`、`dimos.models`、`dimos.perception`、`dimos.stream`、`dimos.robot`(测试/demo 中)、`dimos.web`、`dimos.constants`

**子目录**
- `mcp/` —— `mcp_server.py`、`mcp_client.py`、`mcp_adapter.py`、`tool_stream.py`
- `skills/` —— 具体的技能 `Module`(导航、语音、OSM、GPS、Google Maps、人物跟随、demo)

---

## `dimos/agents_deprecated/`

> **简介。** **已废弃**的 LangChain 风格 **`OpenAIAgent`** 栈,带 **Chroma/OpenAI 语义记忆**、分词器辅助器、prompt 构造器 —— 留下来仅供老脚本和 `dimos/models/qwen/video_query.py` 使用。

**对外接口**
- `OpenAIAgent` 及相关类 —— `dimos/agents_deprecated/agent.py`(大型遗留模块)
- `OpenAISemanticMemory` —— `memory/chroma_impl.py`
- `PromptBuilder` —— `prompt_builder/impl.py`

**核心概念**
- *遗留 agent 循环* —— 早于 `McpClient` 的 RxPy + 工具调用形态
- *Chroma 内存* —— 向量数据库支持的 "AgentMemory"(使用 `dimos.exceptions`)

**依赖**:`dimos.skills`、`dimos.stream`、`dimos.core`(经由 memory 模块)、`dimos.exceptions`……

**子目录**
- `memory/` —— Chroma + 空间/视觉记忆部件
- `prompt_builder/`、`tokenizer/` —— 遗留的 prompt/token 工具

---

## `dimos/skills/`

> **简介。** **遗留的 Pydantic 技能库**(`AbstractSkill`、`SkillLibrary`),产出 OpenAI 风格的**函数工具** —— 概念上与 `@skill` 重叠,但走不同接线。

**对外接口**
- `SkillLibrary` —— 收集/调度 `AbstractSkill` 子类,导出 JSON 工具 schema
- `AbstractSkill`、`AbstractRobotSkill` —— 基于 Pydantic 的工具模型
- `kill_skill` 与 manipulation 包装器 —— 见 `skills/kill_skill.py`、`skills/manipulation/*`、`visual_navigation_skills.py`、`rest/rest.py`

**核心概念**
- *基于类的 skill* —— 通过子类发现机制把它们注册为工具
- *Robot skill* —— 可选持有 `dimos.robot.robot.Robot` 句柄

**依赖**:`dimos.types`、`dimos.utils`

**子目录**
- `manipulation/` —— 约束 / 抓放风格的 skill
- `rest/` —— 面向 REST 的 skill 辅助器
- `unitree/` —— 例如 speak 包装器

---

## `dimos/control/`

> **简介。** **底层多臂控制**:**`ControlCoordinator`** + **100 Hz `TickLoop`**、可组合的**任务**(遥操作、伺服、轨迹、速度)、按优先级裁决。

**对外接口**
- `ControlCoordinator`、`ControlCoordinatorConfig` —— `coordinator.py` 的中枢协调器 `Module`
- `TickLoop` —— 确定性周期循环
- `Task`、`ControlTask` 层级 —— `task.py`、`tasks/*`
- `ConnectedHardware`、`HardwareComponent` —— `hardware_interface.py`、`components.py`
- `control/blueprints/*` —— 现成的蓝图片段(basic、dual、mobile、teleop)

**核心概念**
- *任务裁决* —— 多写者 → 关节命令仲裁
- *机械臂适配器* —— 与 `dimos.hardware.manipulators.spec` 对接

**依赖**:`dimos.constants`、`dimos.msgs`、`dimos.hardware.manipulators`、`dimos.manipulation`(IK 任务)、`dimos.utils`

**子目录**
- `tasks/` —— `velocity_task`、`trajectory_task`、`teleop_task`、`servo_task`、`cartesian_ik_task`
- `blueprints/` —— 蓝图预设
- `examples/` —— 键盘遥操作、IK jogger demo

---

## `dimos/core/`

> **简介。** **运行时内核**:**`Module`**、**`In`/`Out`** 流、**传输层**、**`Blueprint`/`autoconnect`**、**`ModuleCoordinator`**、worker、RPC、运行注册表、Docker/native 模块、内省工具。

**对外接口**
- `Module`、`ModuleBase`、`ModuleConfig`、`SkillInfo`…… —— `module.py`
- `In`、`Out`、`Transport`、`Stream` —— `stream.py`(stream 层;**不是**单独的 `dimos/stream` 包)
- `LCMTransport`、`pLCMTransport`、`ROSTransport`…… —— `transport.py`
- `rpc` / `T` —— `core.py` 装饰器/类型工具
- `Blueprint`、`BlueprintAtom`、`autoconnect()` —— `coordination/blueprints.py`
- `ModuleCoordinator` —— `coordination/module_coordinator.py`
- `GlobalConfig`、`global_config` —— `global_config.py`
- `WorkerManagerPython`、Docker workers、`PythonWorker` —— `coordination/*`
- `RunEntry`,运行注册表辅助器 —— `run_registry.py`
- `RPCClient`、`RpcCall` —— `rpc_client.py`

**核心概念**
- *流接线* —— 蓝图按 `(name, type)` 配对 `In`/`Out`
- *Forkserver workers* —— 模块进程隔离
- *基于 LCM 的 RPC* —— `LCMRPC`,经由 `dimos.protocol.rpc.pubsubrpc`

**依赖**:`dimos.spec`、`dimos.protocol.*`、`dimos.utils`、`dimos.msgs`(通过传输层间接);`dimos.models.vl.types`(全局配置中的 VL name 枚举);测试中 import `dimos.agents`、`dimos.robot.cli`

**子目录**
- `coordination/` —— 蓝图、协调器、worker manager、进程生命周期、rpyc、daemon hook
- `introspection/` —— 模块/蓝图图,ANSI/dot 渲染
- `resource_monitor/` —— 资源统计/日志
- `tests/`、`test_*.py` —— 大量单元/端到端测试

---

## `dimos/memory/`

> **简介。** **遗留**:embedding 内存 + **通用时间序列**后端(SQLite/Postgres/pickle/in-memory)。

**对外接口**
- `EmbeddingMemory`、`Config` —— `embedding.py` —— Rx 上的 CLIP 图像路径(costmap 钩子仍是 stub/试验性)
- `timeseries/*` —— `base.py` + `sqlite.py`、`postgres.py`、`pickledir.py`、`inmemory.py`、`legacy.py`

**核心概念**
- *空间 embedding* —— 图像+costmap 融合的想法(代码里仍标着 "would be cool")
- *时间序列存储* —— 给传感器时间线用的复用持久化层

**依赖**:`dimos.core`、`dimos.models.embedding`、`dimos.msgs`、`dimos.utils.reactive`

**子目录**
- `timeseries/` —— 各存储实现 + 测试

---

## `dimos/memory2/`

> **简介。** **当前的内存子系统**:类型化**观测**、**变换流**、可插拔的 **blob/向量/observation 存储**、编解码器、把 `dimos.core` I/O 桥接进来的 **`StreamModule`**。

**对外接口**
- `Stream` —— `stream.py` —— 变换、背压、查询
- `StreamModule`、`port_to_stream()`、`stream_to_port()` —— `module.py`
- `Backend` —— `backend.py` —— 复合的 Observation + Blob + Vector + notifier
- `EmbedImages` —— `embed.py`
- `registry.py`、`buffer.py`、`transform.py`、`type/observation.py`、`store/sqlite.py`、`blobstore/*`、`vectorstore/*`、`codecs/*`、`vis/*`

**核心概念**
- *观测管道* —— append → transform → embed → persist → notify
- *编解码器插件* —— pickle、LCM、lz4、jpeg 等

**依赖**:`dimos.core`、`dimos.models.embedding`、`dimos.msgs`、`dimos.utils`、`dimos.agents.annotation`(`module.py` 中的 skill 辅助)、`dimos.constants`

**子目录**
- `store/`、`blobstore/`、`observationstore/`、`vectorstore/` —— 各存储后端
- `codecs/` —— blob/payload 序列化
- `type/` —— `Observation`、过滤器、查询类型
- `notifier/` —— 反应式通知
- `utils/`、`vis/` —— 校验、绘图/rerun/svg 辅助器

---

## `dimos/perception/`

> **简介。** **理解层**:2D/3D 检测/跟踪、**空间记忆**、VLM 钩子,以及试验性**时序**图记忆。

**对外接口**
- `ObjectTrackingSpec`、`SpatialMemorySpec` —— `object_tracking_spec.py`、`spatial_memory_spec.py`
- `SpatialMemory` 模块 —— `spatial_perception.py`(仍 import **`agents_deprecated`** 的 visual memory)
- `PerceiveLoopSkill` 风格的部件 —— `perceive_loop_skill.py`
- `detection/` —— YOLO/SAM/EdgeTAM 包装的检测器与消息类型
- `experimental/temporal_memory/` —— 时序图 / 滑窗视频分析(有 README)

**核心概念**
- *感知端口的 spec* —— `dimos.spec.perception`
- *空间记忆* —— 通过 `dimos.types.robot_location` 表示有名字的 `RobotLocation`

**依赖**:`dimos.core`、`dimos.msgs`、`dimos.spec`、`dimos.models`、`dimos.types`、`dimos.agents`、`dimos.agents_deprecated`(遗留路径)、`dimos.stream`、`dimos.robot`(测试)、`dimos.utils`

**子目录**
- `detection/` —— 检测器 + 2D/3D 消息类型
- `experimental/temporal_memory/` —— 进行中的时序推理栈

---

## `dimos/manipulation/`

> **简介。** **机械臂与运动**:抓取/放置模块、**Drake** 世界模型 + 轨迹生成、Pinocchio IK,以及**嵌套的 `manipulation/control/`**(伺服、轨迹控制器、协调器**客户端**)。

**对外接口**
- `ManipulationModule` / 抓放 —— `manipulation_module.py`、`pick_and_place_module.py`
- `planning/` —— Drake `World`、轨迹生成器、URDF/mesh 工具,配置在 `planning/spec/*`
- `control/` —— `cartesian_motion_controller.py`、`joint_trajectory_controller.py`、`coordinator_client.py`、`arm_driver_spec.py`(与 `dimos.control` 协调器协同)

**核心概念**
- *WorldSpec / JointTrajectory* —— 规划器之间的交换格式
- *伺服 vs 轨迹* —— 不同的 control task 绑定

**依赖**:`dimos.core`、`dimos.msgs`、`dimos.perception`(3D 物体类型)、`dimos.utils`

**子目录**
- `planning/` —— 世界、轨迹、运动学、spec(`planning/` 下有 README)
- `control/` —— 底层机械臂运动控制器,**与**顶层 `dimos/control` 协调器**并列**

---

## `dimos/mapping/`

> **简介。** **地图与地理上下文**:从点云生成占据栅格、**体素管线**、OSM "当前位置"、Google Maps 集成、**`LatLon`** 模型。

**对外接口**
- `LatLon` 与相关类 —— `models.py`、`google_maps/*`、`osm/*`
- `VoxelGrid` / `VoxelGridMapper` —— `voxels.py`(用了 **`memory2.StreamModule`**)
- `pointclouds/occupancy.py` —— 栅格生成辅助器
- `occupancy/` —— 膨胀、梯度(被 websocket 可视化使用)

**核心概念**
- *占据融合* —— 来自 `PointCloud2` / 机器人本体栅格
- *地理叠加* —— 可选的地图 + VL 查询

**依赖**:`dimos.core`、`dimos.memory2`、`dimos.msgs`、`dimos.utils`、`dimos.memory`(测试中的遗留 replay)

**子目录**
- `pointclouds/`、`occupancy/`、`voxels.py` —— 地图核心
- `osm/`、`google_maps/` —— 外部地图提供方 + 模型

---

## `dimos/navigation/`

> **简介。** **运动规划与运动执行**:ROS 导航桥、巡逻、前沿探索、**视觉伺服**辅助器、与摇杆/遥操作相邻的路由。

**对外接口**
- `NavigationState`、`NavigationInterface` —— `base.py`
- `NavigationInterfaceSpec` —— `navigation_spec.py`
- `ROSNavModule` —— `rosnav.py`(skill + ROS/LCM I/O)
- `visual_servoing/` —— 2D 伺服 + 检测导航工具
- `visual/query.py` —— VLM bbox 辅助器
- `patrolling/`、`exploration/` —— 更高层行为
- `global_planner/`、`motion_planning/` —— 规划栈

**核心概念**
- *Navigator spec 注入* —— 类型化 RPC spec 表达 "去哪里"
- *视觉伺服桥* —— 感知 + 差速底盘 / TF

**依赖**:`dimos.core`、`dimos.msgs`、`dimos.perception`、`dimos.models`、`dimos.protocol.tf`、`dimos.utils`、`dimos.agents`(rosnav 的 `@skill`)

**子目录**
- `visual_servoing/`、`visual/`、`patrolling/`、`exploration/`、`global_planner/`、`motion_planning/`、`tests/`……

---

## `dimos/hardware/`

> **简介。** **设备驱动与适配器**:相机(ZED、RealSense、GStreamer、webcam)、**Livox/FAST-LIO2 激光雷达**、机械臂、底盘、整机总线。

**对外接口**
- `Camera` 相关模块 —— `sensors/camera/*`(+ `spec.py` 配置)
- 激光雷达 `Mid360`、`FastLio2` 的 **NativeModule** 包装 —— `sensors/lidar/*`
- `ManipulatorAdapter` / 现场总线 —— `manipulators/`、`drive_trains/`、`whole_body/`
- `fake_zed_module.py` —— 适合 replay 的相机数据源

**核心概念**
- *感知 spec* —— 通过 `import dimos.spec.perception as …` 给相机流打标
- *Native 模块* —— C++/SHM 支撑的发布器,使用 `NativeModule` 基类

**依赖**:`dimos.core`、`dimos.msgs`、`dimos.spec`、`dimos.protocol.tf`、`dimos.mapping`、`dimos.visualization`、`dimos.robot`、`dimos.memory`(部分模块用了遗留存储)

**子目录**
- `sensors/camera/`、`sensors/lidar/` —— 视觉 + 激光雷达栈(部分有 **C++ README**)
- `manipulators/`、`drive_trains/`、`whole_body/` —— 执行端

---

## `dimos/robot/`

> **简介。** **机器人产品在代码里的体现**:**Typer/Click CLI**(`dimos run`、`mcp`、`log`……)、**自动生成的蓝图注册表**、Unitree Go2/G1/B1、无人机 MAVLink/DJI、xArm/piper/openarm 蓝图。

**对外接口**
- `main` / CLI —— `cli/dimos.py`
- `get_by_name`、`class_name_to_registry_key` —— `get_all_blueprints.py`
- `all_blueprints` —— 生成的列表(`all_blueprints.py`)
- `Robot` 抽象基类 —— `robot.py`(遗留的极简接口)
- `FoxgloveBridge` —— `foxglove_bridge.py`
- 大型子树:`unitree/go2|g1|b1/`、`drone/`、`manipulators/*`

**核心概念**
- *蓝图注册表* —— 字符串 → 可运行的栈
- *机器人专属类型* —— 例如 `unitree/type/lidar.py`、`odometry.py`

**依赖**:几乎依赖一切(`dimos.agents`、`dimos.core`、`dimos.hardware`、`dimos.simulation`、`dimos.navigation`、`dimos.mapping`、`dimos.msgs`……)

**子目录**
- `cli/` —— 面向用户的 **`dimos`** 命令
- `unitree/` —— Go2/G1/B1 连接 + 蓝图
- `drone/` —— MAVLink、DJI 视频、跟踪
- `manipulators/` —— xArm、Piper、OpenArm 蓝图片段
- `unitree_webrtc/` —— WebRTC 类型 shim,re-export `unitree/type`

---

## `dimos/simulation/`

> **简介。** **物理后端与胶水**:MuJoCo 共享内存 + policy 循环、可选的 Genesis/Isaac/Unity 流、**`SimulationEngine` 注册表**(当前默认 **`mujoco`**)。

**对外接口**
- `get_engine()` —— `engines/registry.py`
- `MujocoEngine`、`MujocoSimModule` —— `engines/mujoco_engine.py`、`mujoco_shm.py`
- `simulation/mujoco/*` —— SHM 写入、深度相机、explorer 脚本
- `simulation/base/*` —— 抽象的 simulator/stream 基类

**核心概念**
- *引擎选择* —— 字符串 → 仿真后端类
- *SHM 桥* —— 桥到 `dimos.robot.unitree.mujoco_connection`

**依赖**:`dimos.msgs`、`dimos.core`(经由模块)、`dimos.utils.data`(典型)、`dimos.robot`(集成)

**子目录**
- `engines/` —— 注册表 + MuJoCo 集成
- `mujoco/` —— 进程 + SHM + 相机辅助
- `genesis/`、`isaac/`、`unity/` —— 备选仿真集成
- `utils/` —— MJCF/XML 辅助

---

## `dimos/teleop/`

> **简介。** **人在回路输入**:Meta Quest WebXR、手机浏览器 UI、键盘 jogger;接到 `RobotWebInterface` 与 **control** 蓝图。

**对外接口**
- `QuestTeleopModule`、`ArmTeleopModule`、`quest_extensions` —— VR pose / joy
- `PhoneTeleopModule` —— 触摸 twist 遥操作
- `keyboard/keyboard_teleop_module.py` —— 引用 `dimos.control.examples` 中的 IK jogger
- `quest/blueprints.py`、`phone/blueprints.py` —— `autoconnect` 组合

**核心概念**
- *WebXR → 机器人坐标系* —— `teleop/utils/teleop_transforms.py`
- *Control 集成* —— import `dimos.control.blueprints.teleop`

**依赖**:`dimos.core`、`dimos.msgs`、`dimos.web`、`dimos.utils`、`dimos.robot`、`dimos.control`、`dimos.visualization`

**子目录**
- `quest/`、`phone/`、`keyboard/` —— 设备专属模块 + quest/phone 下有 README
- `utils/` —— 共享变换

---

## `dimos/models/`

> **简介。** **机器学习模型适配器**:**视觉-语言模型**(Qwen/Moondream/Florence/OpenAI)、**CLIP/MobileCLIP/TorchReID** embedding、**EdgeTAM** 分割、HuggingFace 基类。

**对外接口**
- `VlModel`、`create()` —— `vl/base.py`、`vl/create.py`、`vl/types.py`
- 具体 VLM —— `vl/qwen.py`、`vl/moondream.py`、`vl/openai.py`……
- `EmbeddingModel`、`CLIPModel`…… —— `embedding/*`
- `EdgeTAMProcessor` —— `segmentation/edge_tam.py`
- `HuggingFaceModel`、`LocalModel` —— `base.py`

**核心概念**
- *资源生命周期* —— 模型继承 `dimos.core.resource.Resource`
- *检测类型* —— 与 `dimos.perception.detection.types` 紧耦合

**依赖**:`dimos.core`、`dimos.msgs`、`dimos.perception`、`dimos.protocol.service`、`dimos.utils`、`dimos.types`、`dimos.agents_deprecated`(Qwen 视频查询)

**子目录**
- `vl/`、`embedding/`、`segmentation/`、`qwen/`
- `vl/README.md` 文档化了视觉-语言栈

---

## `dimos/stream/`

> **简介。** **辅助媒体管道**(与 **`dimos.core.stream`** 不同) —— **视频提供方**和**音频图**(麦克、Whisper STT、OpenAI TTS)。

**对外接口**
- `AbstractVideoProvider`、`VideoProvider` —— `video_provider.py`
- `RTSP*`、ROS 图像提供方 —— `rtsp_video_provider.py`、`ros_video_provider.py`
- `FrameProcessor`、`stream_merger`、`video_operators` —— 融合工具
- `stream/audio/*` —— `SounddeviceAudioSource`、`WhisperNode`、`OpenAITTSNode`、各 pipeline

**核心概念**
- *Rx 风格的媒体图* —— 可组合的音频节点 + 线程池调度
- *不是* Module `In`/`Out` 系统 —— 同名包,概念有重叠但是分开的

**依赖**:大多是 `dimos.utils`(部分音频节点用了 `dimos.constants`)

**子目录**
- `audio/` —— 基础事件、STT/TTS、麦克风、pipeline

---

## `dimos/web/`

> **简介。** **HTTP + 浏览器 UX**:**Flask/FastAPI EdgeIO** 服务器、Svelte `dimos_interface`、React **command-center** 扩展、**Leaflet websocket 地图**可视化。

**对外接口**
- `RobotWebInterface` —— `robot_web_interface.py` —— 把遥操作模块桥接到 FastAPI UI
- `FastAPIServer` —— `dimos_interface/api/server.py`
- `EdgeIO` —— `edge_io.py` 的 pub/sub edge
- `flask_server.py`、`fastapi_server.py` —— 备选托管方式
- `websocket_vis/*` —— 地图/costmap websocket 模块 + README

**核心概念**
- *Edge IO* —— 内部流的 HTTP/SSE 桥
- *Websocket 可视化* —— 消费 `dimos.mapping` 的 `OccupancyGrid`、路径、GPS

**依赖**:`dimos.core`、`dimos.mapping`、`dimos.msgs`、`dimos.stream.audio`、`dimos.utils`

**子目录**
- `dimos_interface/` —— Svelte+Vite UI + `api/` FastAPI
- `command-center-extension/` —— React Leaflet 可视化器
- `websocket_vis/` —— Python 模块 + README
- `templates/` —— HTML 骨架

---

## `dimos/experimental/`

> **简介。** **不稳定 / 仅用于 demo 的代码** —— 例如 **`security_demo`**(YOLO + EdgeTAM + 深度桩),被编译进可选蓝图。

**对外接口**
- `SecurityModule` —— `experimental/security_demo/security_module.py`
- `DepthEstimator` —— `depth_estimator.py`

**核心概念**
- *Demo 管道* —— 可能 import 重型 CV 栈;**不是**稳定 API

**依赖**:`dimos.agents`、`dimos.core`、`dimos.models`、`dimos.perception`

**子目录**
- `security_demo/` —— 模块 + estimator + 测试

---

## `dimos/porcelain/`

> **简介。** 在 **`ModuleCoordinator`** 之上的**稳定的"单对象"API**:`Dimos.run()`、`SkillsProxy`、本地 vs **远程**daemon 模块源。

**对外接口**
- `Dimos` —— `dimos.py`
- `LocalModuleSource`、`RemoteModuleSource`、`ModuleSource` 抽象基类 —— 进程/daemon 接入
- `SkillsProxy` —— 远程 skill 调用辅助

**核心概念**
- *Porcelain vs plumbing* —— 类比 git:在 `core` 之上的人体工学层

**依赖**:`dimos.core`、`dimos.robot`(注册表)、`dimos.core.run_registry`(经由协调器间接)

**子目录**
- 仅测试(`test_*.py`)

---

## `dimos/spec/`

> **简介。** **接线契约**:基础 **`Spec` Protocol 标记**、辅助器(`is_spec`、合规检查),以及**几个小型领域标记模块**(`spec.perception`、`spec.mapping`……)。

**对外接口**
- `Spec`、`is_spec()`、`spec_structural_compliance()`…… —— `spec/utils.py`
- `dimos/spec/perception.py`、`nav.py`、`mapping.py`、`control.py` —— **`Module` 端的能力标记**,在 hardware 中以 `from dimos.spec import perception` 模式 import

**核心概念**
- *Spec vs 普通 Protocol* —— `Spec` 出现在 MRO 中,用来区分可注入的 RPC 面
- *领域 spec* —— 用于流类型化的窄语义标签

**依赖**:基本只用 typing/inspect + `annotation_protocol`

---

## `dimos/protocol/`

> **简介。** **传输与服务**:发布-订阅(**LCM**、pickled LCM、ROS、SHM、Redis、内存)、**RPC**(LCMRPC、RedisRPC)、**TF**、服务发现/配置器、编解码器。

**对外接口**
- `PubSub`、topic、encoder —— `pubsub/spec.py`、`pubsub/impl/*`
- `LCMRPC`、`PickleLCM` —— `rpc/pubsubrpc.py`,绑定到 pubsub 层
- `LCMTF`、`TFSpec` —— `tf/tf.py`
- `LCMService`、DDS 镜像 —— `service/lcmservice.py`、`service/ddsservice.py`
- `Configurable`、`BaseConfig` —— `service/spec.py`

**核心概念**
- *可插拔的 pubsub 后端* —— 同一套 `PubSub` 接口
- *跨进程 RPC* —— 异常通过 `rpc_utils` 序列化

**依赖**:`dimos.constants`、`dimos.utils`;`pubsub` 可能用可选 extra(DDS、ROS)

**子目录**
- `pubsub/impl/` —— lcm、ros、shm、redis、dds、jpeg……
- `rpc/`、`service/`、`tf/`、`encode/`
- `pubsub/benchmark/` —— 性能压测

---

## `dimos/exceptions/`

> **简介。** **已废弃**的 AgentMemory / 向量库层的错误类型。

**对外接口**
- `AgentMemoryError`、`AgentMemoryConnectionError`、`DataNotFoundError`…… —— 仅 `agent_memory_exceptions.py`

**核心概念**
- *类型化的检索失败* —— 向量 ID 缺失、连接问题

**依赖**:仅标准库(该文件没有 `dimos.*` import)

---

## `dimos/visualization/`

> **简介。** **Rerun 集成**:订阅 LCM 风格的流量,把实现了 **`to_rerun()`** 的消息转过去,启动可视化器。

**对外接口**
- `RerunBridgeModule` —— `rerun/bridge.py`(注意外部 `rerun.blueprint` 与 DimOS `Blueprint` 同名)
- `rerun_init` 与辅助器 —— `rerun/init.py`

**核心概念**
- *PubSub 旁路接入* —— 按 topic 模式匹配
- *可选 grpc/web 查看器端口* —— 模块顶部导出常量

**依赖**:`dimos.core`、`dimos.protocol.pubsub`、`dimos.utils`

**子目录**
- `rerun/` —— bridge + 测试 + `init.py`

---

## `dimos/utils/`

> **简介。** **共享基础设施**:日志配置、**数据**路径辅助器(`get_data`)、数学(`Vector`、变换)、**Rx 辅助器**、CLI 诊断(**`lcmspy`**、**`dtop`**、agentspy、foxglove bridge launcher)、replay/moment 测试工具。

**对外接口**(代表性)
- `setup_logger`、按运行的日志目录 —— `logging_config.py`
- `get_data`、`get_data_dir` —— `data.py`
- `backpressure` Rx 工具 —— `reactive.py`
- CLI 入口模块 —— `cli/lcmspy/lcmspy.py`、`cli/dtop.py`、`cli/agentspy/agentspy.py`……
- `transform_utils`、`trigonometry`、`path_utils`、`urdf.py`……

**核心概念**
- *测试 replay* —— `utils/testing/replay.py`、`moment.py`,提供确定性传感器
- *运维 CLI* —— 不需要启动完整 `dimos` 栈也能内省

**依赖**:广泛引用 `dimos.msgs`、`dimos.core`、`dimos.memory*`,测试辅助中也 import `dimos.robot`、`dimos.protocol`、`dimos.types`

**子目录**
- `cli/` —— 运维命令
- `testing/` —— replay/moment fixture
- `decorators/`、`docs/` —— 横切辅助器

---

## `dimos/types/`

> **简介。** **轻量级共享类型**(不是完整的 **`msgs`**):numpy 的 **`Vector`**、时间戳辅助、机器人能力、weak 容器。

**对外接口**
- `Vector` —— `vector.py`
- `Timestamped`、`to_timestamp` —— `timestamped.py`
- `WeakList` —— `weaklist.py`
- `Sample` —— `sample.py`
- `RobotLocation` —— `robot_location.py`
- `Vector3` polyfill —— `ros_polyfill.py`
- `RobotCapability` —— `robot_capabilities.py`(被 `dimos.robot.robot.Robot` 使用)
- `Colors` —— `types/constants` 区域(若存在)

**核心概念**
- *非 ROS 的几何辅助* —— 避免为纯 Python 数学引入沉重的 `geometry_msgs`
- *空间记忆身份* —— `RobotLocation` 把名字 ↔ pose 元数据绑定

**依赖**:大多依赖 `numpy`;少数转换用到 `dimos.msgs`

---

## `dimos/msgs/`

> **简介。** **类型化消息模型**,镜像 ROS 1 形状(geometry、sensor、nav、trajectory、vision_msgs)+ **`DimosMsg`**、Foxglove 叠加层,以及大量往返测试。

**对外接口**
- 每个 topic 对应的类 —— 例如 `sensor_msgs/Image.py`、`geometry_msgs/PoseStamped.py`、`nav_msgs/OccupancyGrid.py`……
- `DimosMsg` 协议 —— `msgs/protocol.py`
- 共享辅助 —— `msgs/helpers.py`

**核心概念**
- *序列化友好的 dataclass / pydantic 风模式* —— 用于 LCM/ROS 桥
- *图像 barrier* —— `Image.py` 中的 `sharpness_barrier` 钩子,用于 load shedding

**依赖**:`msgs/*` 内部互相 self-reference(其他 `dimos.*` 极少)

**子目录**
- `geometry_msgs/`、`sensor_msgs/`、`nav_msgs/`、`std_msgs/`、`trajectory_msgs/`、`vision_msgs/`、`tf2_msgs/`、`foxglove_msgs/`……

---

## `dimos/project/`

> **简介。** **元测试**,守护仓库约定 —— **不是**运行时的"版本"模块(版本号请用 **`pyproject.toml`** / packaging)。

**对外接口**
- `test_get_logger.py`、`test_no_init_files.py`…… —— 强制日志和布局策略

**核心概念**
- *CI 卫生* —— 对树做静态扫描

**依赖**:`dimos.constants`(路径 root)

---

## `dimos/rxpy_backpressure/`

> **简介。** 小型 **RxPy 算子工具集**,为 observer 提供 **Latest / Drop / Buffer** 三种策略(`BackPressure` 命名空间)。

**对外接口**
- `BackPressure` —— `backpressure.py` —— 引用 `drop.py`、`latest.py` 模块
- `wrap_observer_with_*` —— 各策略的辅助器

**核心概念**
- *Observer 端流控* —— 与 `dimos.utils.reactive` 互补

**依赖**:仅本包内部(re-import `rxpy_backpressure/` 同级文件)

---

## 怎么用这份文档读代码

1. **找某个行为?** 从 `agents/skills/` 开始 → 找出哪个 `Module` 暴露了对应的 `@skill` → 进入支撑层(例如 `navigation`、`mapping`、`manipulation`)。
2. **找某个 I/O 契约?** 从 `spec/`(能力)和 `msgs/`(payload)起,然后到 `protocol/` 看传输。
3. **找运行时?** `core/` 是内核;`porcelain/` 是人体工学层;`robot/cli/` 是用户入口。
4. **典型的 sensor → action 链路:** `hardware/sensors` → `perception` → `memory2`(+ `mapping`)→ `agents`(规划)→ `skills` / `navigation` / `manipulation` → `control` → `hardware/manipulators|drive_trains`。
5. **新代码请避开的遗留模块:** `agents_deprecated/`、`skills/`(Pydantic 路线)、`memory/`、`exceptions/`。
