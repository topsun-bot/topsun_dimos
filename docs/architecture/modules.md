# DimOS Module Architecture

> Semantic map of the 28 top-level modules under `dimos/`. Auto-generated from import-graph + source analysis on the `dev` branch. Treat the **public surface** lists as the smallest stable contract you can rely on without spelunking.

`dimos/__init__.py` only lazy-loads **`Dimos`** from `dimos.porcelain.dimos`. Most subpackages have **no `__init__.py` curated exports** (namespace-style layout) — treat **concrete modules** as the public surface unless explicit re-exports are added.

---

## Module matrix (at a glance)

| Module | Layer | Role (one line) |
|--------|-------|-----------------|
| **agents** | brain | LLM tooling: `@skill`, MCP client/server, optional VL agent, nav/speak skill containers |
| **agents_deprecated** | legacy | Old `OpenAIAgent` / tokenizer / **chromadb** memory stack used by older demos |
| **skills** | brain (legacy) | Older **Pydantic `AbstractSkill` + `SkillLibrary`** tool schema (parallel to `@skill` RPC) |
| **control** | actuation | **100 Hz `ControlCoordinator`**, tasks, tick loop, joint arbitration for arms |
| **core** | runtime | **Modules, streams, transports, blueprints, coordinator, RPC, workers, global config** |
| **memory** | data (legacy) | Embedding + **timeseries** stores (`EmbeddingMemory`, SQLite/Postgres backends) |
| **memory2** | data | **Stream-based memory**: observations, stores, codecs, `StreamModule` integration |
| **perception** | sensing | Detection, tracking, spatial memory, experimental temporal memory |
| **manipulation** | motion | Pick/place, planning (Drake/MJCF), trajectory/control helpers under `control/` |
| **mapping** | spatial | Occupancy, voxels, OSM/Google Maps helpers, **LatLon** models |
| **navigation** | motion | ROS nav bridges, patrolling, **visual servoing**, frontier/exploration |
| **hardware** | drivers | Cameras, lidar, manipulators, drive trains, whole-body adapters |
| **robot** | product | **`dimos run` CLI**, blueprint registry, Unitree/drone/xArm/etc. stacks |
| **simulation** | sim | MuJoCo SHM, Genesis/Isaac/Unity stubs, **engine registry** |
| **teleop** | input | Quest / phone / keyboard teleop modules and blueprints |
| **models** | ml | **VLM**, CLIP/MobileCLIP embeddings, segmentation (EdgeTAM), HuggingFace wrappers |
| **stream** | media | **Video** (Webcam/RTSP/ROS) and **audio** (mic, Whisper, TTS) pipelines |
| **web** | ui | Flask/FastAPI **EdgeIO**, Svelte `dimos_interface`, map **websocket_vis** |
| **experimental** | wip | **Unstable demos** (e.g. `security_demo` pipeline) |
| **porcelain** | api | **`Dimos` façade**: local/remote module sources, **`SkillsProxy`** |
| **spec** | typing | **`Spec` marker protocol** + domain `spec.perception` / `nav` / `mapping` / `control` |
| **protocol** | transport | **LCM/ROS/SHM/DDS/Redis** pubsub, **LCMRPC**, TF, service configurator |
| **exceptions** | legacy | **Agent memory–specific** exception hierarchy (legacy memory path) |
| **visualization** | ops | **Rerun bridge** + init helpers |
| **utils** | infra | Logging, data paths, CLI tools (`lcmspy`, `dtop`, replay, foxglove, …) |
| **types** | typing | **`Vector`**, `Timestamped`, **`RobotLocation`**, weak list, ROS polyfills |
| **msgs** | typing | **Typed ROS-like messages** + `DimosMsg` protocol |
| **project** | ci | **Repo hygiene tests** (e.g. logging `getLogger` guard); not a version API |
| **rxpy_backpressure** | infra | **`BackPressure`** façade (Latest/Drop/Buffer observer strategies) |

### Cross-cutting clarifications (read these once)

- **Two parallel skill systems**: `@skill` + MCP (`agents/`) vs Pydantic `AbstractSkill` (`skills/`). New code should prefer the former.
- **`stream/` ≠ `core.stream`**: `dimos/stream/` is *media pipelines* (video + audio); `dimos.core.stream` is the *Module I/O* primitives (`In`/`Out`/`Transport`).
- **`memory/` is legacy**, `memory2/` is the actively maintained observation/store pipeline.
- **`project/`** is a *CI hygiene test bag*, not a version/metadata module — version comes from `pyproject.toml`.
- **`exceptions/`** only carries legacy-memory errors; it isn't the project-wide error namespace.

---

## Top-level `dimos` package

> **Tagline.** Lazy entrypoint: only `Dimos` is exposed from `dimos/__init__.py`; everything else is imported from subpackages explicitly.

**Public surface**
- `Dimos` (`dimos.porcelain.dimos`) — build/run blueprints, optional remote daemon wiring

**Key concepts**
- *Lazy package attribute* — `__getattr__` loads `Dimos` on first access

**Depends on**: (loads) `dimos.porcelain`, `dimos.core`, `dimos.robot`, …

---

## `dimos/agents/`

> **Tagline.** Agent *framework*: LLM **MCP** stack (`McpClient` / `McpServer`), **`@skill`** decorator, VL agent module, and robot-agnostic **skill containers** (navigation, GPS/maps, speak, …).

**Public surface** (no package `__init__.py`; typical imports)
- `skill` / `current_skill_context()` — `dimos/agents/annotation.py` — marks RPC methods as LLM/MCP tools; optional MCP progress context
- `McpClient`, `McpClientConfig` — LangChain agent + HTTP MCP tools + LCM streams
- `McpServer` — FastAPI JSON-RPC MCP server exposing module `@skill`s
- `McpAdapter` — adapter plumbing for MCP integration tests
- `AgentSpec` — protocol for "something that accepts agent instructions"
- `VLMAgent`, `VLMAgentConfig` — image I/O + VLM-oriented module
- `AgentTestRunner` — test harness module for agent stacks
- `SYSTEM_PROMPT` — `dimos/agents/system_prompt.py` default prompt text
- `ensure_ollama_model()`, `ollama_installed()` — `dimos/agents/ollama_agent.py` Ollama helpers (not a full `Agent` class)

**Key concepts**
- *`@skill`* — `@rpc` + `__skill__`; drives MCP tool schemas
- *MCP HTTP bridge* — external tool protocol over FastAPI (default port from `GlobalConfig`)
- *Skill containers* — `Module` subclasses exposing many `@skill`s for a scenario

**Depends on**: `dimos.core`, `dimos.spec`, `dimos.msgs`, `dimos.utils`, and often `dimos.navigation`, `dimos.mapping`, `dimos.models`, `dimos.perception`, `dimos.stream`, `dimos.robot` (in tests/demos), `dimos.web`, `dimos.constants`

**Subdirectories**
- `mcp/` — `mcp_server.py`, `mcp_client.py`, `mcp_adapter.py`, `tool_stream.py`
- `skills/` — concrete skill `Module`s (nav, speak, OSM, GPS, Google Maps, person follow, demos)

---

## `dimos/agents_deprecated/`

> **Tagline.** **Deprecated** LangChain-style **`OpenAIAgent`** stack with **Chroma/OpenAI semantic memory**, tokenizer helpers, and prompt builder — kept for older scripts and `dimos/models/qwen/video_query.py`.

**Public surface**
- `OpenAIAgent` and related classes — `dimos/agents_deprecated/agent.py` (large legacy module)
- `OpenAISemanticMemory` — `memory/chroma_impl.py`
- `PromptBuilder` — `prompt_builder/impl.py`

**Key concepts**
- *Legacy agent loop* — RxPy + tool calling predating `McpClient`
- *Chroma memory* — vector DB–backed "AgentMemory" (uses `dimos.exceptions`)

**Depends on**: `dimos.skills`, `dimos.stream`, `dimos.core` (via memory modules), `dimos.exceptions`, …

**Subdirectories**
- `memory/` — Chroma + spatial/visual memory pieces
- `prompt_builder/`, `tokenizer/` — legacy prompt/token utilities

---

## `dimos/skills/`

> **Tagline.** **Legacy Pydantic skill library** (`AbstractSkill`, `SkillLibrary`) producing OpenAI-style **function tools** — overlaps conceptually with `@skill` but uses different wiring.

**Public surface**
- `SkillLibrary` — collects/runs `AbstractSkill` subclasses, JSON tool export
- `AbstractSkill`, `AbstractRobotSkill` — Pydantic-based tool models
- `kill_skill` and manipulation wrappers — see `skills/kill_skill.py`, `skills/manipulation/*`, `visual_navigation_skills.py`, `rest/rest.py`

**Key concepts**
- *Class-based skills* — discover subclasses and expose as tools
- *Robot skill* — optional `dimos.robot.robot.Robot` handle

**Depends on**: `dimos.types`, `dimos.utils`

**Subdirectories**
- `manipulation/` — constraint / pick-and-place style skills
- `rest/` — REST-oriented skill helper
- `unitree/` — e.g. speak wrapper

---

## `dimos/control/`

> **Tagline.** **Low-level multi-arm control**: **`ControlCoordinator`** + **100 Hz `TickLoop`**, composable **tasks** (teleop, servo, trajectory, velocity), priority arbitration.

**Public surface**
- `ControlCoordinator`, `ControlCoordinatorConfig` — `coordinator.py` central coordinator `Module`
- `TickLoop` — deterministic periodic loop
- `Task`, `ControlTask` hierarchy — `task.py`, `tasks/*`
- `ConnectedHardware`, `HardwareComponent`s — `hardware_interface.py`, `components.py`
- `control/blueprints/*` — ready-made blueprint fragments (basic, dual, mobile, teleop)

**Key concepts**
- *Task arbitration* — multiple writers → joint command resolution
- *Manipulator adapters* — `dimos.hardware.manipulators.spec` integration

**Depends on**: `dimos.constants`, `dimos.msgs`, `dimos.hardware.manipulators`, `dimos.manipulation` (IK tasks), `dimos.utils`

**Subdirectories**
- `tasks/` — `velocity_task`, `trajectory_task`, `teleop_task`, `servo_task`, `cartesian_ik_task`
- `blueprints/` — blueprint presets
- `examples/` — keyboard teleop, IK jogger demos

---

## `dimos/core/`

> **Tagline.** **Runtime kernel**: **`Module`**, **`In`/`Out`** streams, **transports**, **`Blueprint`/`autoconnect`**, **`ModuleCoordinator`**, workers, RPC, run registry, Docker/native modules, introspection.

**Public surface**
- `Module`, `ModuleBase`, `ModuleConfig`, `SkillInfo`, … — `module.py`
- `In`, `Out`, `Transport`, `Stream` — `stream.py` (stream layer; **not** the separate `dimos/stream` package)
- `LCMTransport`, `pLCMTransport`, `ROSTransport`, … — `transport.py`
- `rpc` / `T` — `core.py` decorator typing helpers
- `Blueprint`, `BlueprintAtom`, `autoconnect()` — `coordination/blueprints.py`
- `ModuleCoordinator` — `coordination/module_coordinator.py`
- `GlobalConfig`, `global_config` — `global_config.py`
- `WorkerManagerPython`, Docker workers, `PythonWorker` — `coordination/*`
- `RunEntry`, run registry helpers — `run_registry.py`
- `RPCClient`, `RpcCall` — `rpc_client.py`

**Key concepts**
- *Stream wiring* — blueprint matches `(name, type)` on `In`/`Out`
- *Forkserver workers* — process isolation for modules
- *RPC over LCM* — `LCMRPC` via `dimos.protocol.rpc.pubsubrpc`

**Depends on**: `dimos.spec`, `dimos.protocol.*`, `dimos.utils`, `dimos.msgs` (indirectly via transports), `dimos.models.vl.types` (VL name enum in global config); tests import `dimos.agents`, `dimos.robot.cli`

**Subdirectories**
- `coordination/` — blueprints, coordinator, worker managers, process lifecycle, rpyc, daemon hooks
- `introspection/` — module/blueprint graph & ANSI/dot rendering
- `resource_monitor/` — resource stats/logging
- `tests/`, `test_*.py` — extensive unit/e2e tests

---

## `dimos/memory/`

> **Tagline.** **Legacy** embedding memory + **generic timeseries** backends (SQLite/Postgres/pickle/in-memory).

**Public surface**
- `EmbeddingMemory`, `Config` — `embedding.py` — CLIP-on-Rx image path (costmap hookups are stubbed/experimental)
- `timeseries/*` — `base.py` + `sqlite.py`, `postgres.py`, `pickledir.py`, `inmemory.py`, `legacy.py`

**Key concepts**
- *Spatial embeddings* — image+costmap fusion idea (still marked "would be cool" in code)
- *Timeseries store* — reusable persistence for sensor timelines

**Depends on**: `dimos.core`, `dimos.models.embedding`, `dimos.msgs`, `dimos.utils.reactive`

**Subdirectories**
- `timeseries/` — store implementations + tests

---

## `dimos/memory2/`

> **Tagline.** **Current memory subsystem**: typed **observations**, **transform streams**, pluggable **blob/vector/observation stores**, codecs, **`StreamModule`** bridge to `dimos.core` I/O.

**Public surface**
- `Stream` — `stream.py` — transforms, backpressure, querying
- `StreamModule`, `port_to_stream()`, `stream_to_port()` — `module.py`
- `Backend` — `backend.py` — composite Observation + Blob + Vector + notifier
- `EmbedImages` — `embed.py`
- `registry.py`, `buffer.py`, `transform.py`, `type/observation.py`, `store/sqlite.py`, `blobstore/*`, `vectorstore/*`, `codecs/*`, `vis/*`

**Key concepts**
- *Observation pipeline* — append → transform → embed → persist → notify
- *Codec plugins* — pickle, LCM, lz4, jpeg, etc.

**Depends on**: `dimos.core`, `dimos.models.embedding`, `dimos.msgs`, `dimos.utils`, `dimos.agents.annotation` (skill helpers in `module.py`), `dimos.constants`

**Subdirectories**
- `store/`, `blobstore/`, `observationstore/`, `vectorstore/` — storage backends
- `codecs/` — serialization for blobs/payloads
- `type/` — `Observation`, filters, query types
- `notifier/` — reactive notification
- `utils/`, `vis/` — validation, plotting/rerun/svg helpers

---

## `dimos/perception/`

> **Tagline.** **Understanding**: 2D/3D detection/tracking, **spatial memory**, VLM hooks, plus **experimental temporal** graph memory.

**Public surface**
- `ObjectTrackingSpec`, `SpatialMemorySpec` — `object_tracking_spec.py`, `spatial_memory_spec.py`
- `SpatialMemory` module — `spatial_perception.py` (still imports **`agents_deprecated`** visual memory)
- `PerceiveLoopSkill`-style pieces — `perceive_loop_skill.py`
- `detection/` — YOLO/SAM/EdgeTAM-wrapped detectors and message types
- `experimental/temporal_memory/` — temporal graph / windowed video analysis (has README)

**Key concepts**
- *Specs for perception ports* — `dimos.spec.perception`
- *Spatial memory* — named `RobotLocation`s via `dimos.types.robot_location`

**Depends on**: `dimos.core`, `dimos.msgs`, `dimos.spec`, `dimos.models`, `dimos.types`, `dimos.agents`, `dimos.agents_deprecated` (legacy path), `dimos.stream`, `dimos.robot` (tests), `dimos.utils`

**Subdirectories**
- `detection/` — detectors + 2D/3D message types
- `experimental/temporal_memory/` — WIP temporal reasoning stack

---

## `dimos/manipulation/`

> **Tagline.** **Arms and motion**: pick/place modules, **Drake** world model + trajectory generation, Pinocchio IK, and **nested `manipulation/control/`** (servo, trajectory controller, coordinator **client**).

**Public surface**
- `ManipulationModule` / pick-and-place — `manipulation_module.py`, `pick_and_place_module.py`
- `planning/` — Drake `World`, trajectory generator, URDF/mesh utilities, configs in `planning/spec/*`
- `control/` — `cartesian_motion_controller.py`, `joint_trajectory_controller.py`, `coordinator_client.py`, `arm_driver_spec.py` (talks to `dimos.control` coordinator patterns)

**Key concepts**
- *WorldSpec / JointTrajectory* — interchange for planners
- *Servo vs trajectory* — different control task bindings

**Depends on**: `dimos.core`, `dimos.msgs`, `dimos.perception` (3D object types), `dimos.utils`

**Subdirectories**
- `planning/` — world, trajectory, kinematics, specs (README in `planning/`)
- `control/` — low-level arm motion controllers **adjacent to** top-level `dimos/control` coordinator

---

## `dimos/mapping/`

> **Tagline.** **Maps and geospatial context**: occupancy from point clouds, **voxel pipeline**, OSM "current location", Google Maps integration, **`LatLon`** models.

**Public surface**
- `LatLon` and friends — `models.py`, `google_maps/*`, `osm/*`
- `VoxelGrid` / `VoxelGridMapper` — `voxels.py` (uses **`memory2.StreamModule`**)
- `pointclouds/occupancy.py` — grid generation helpers
- `occupancy/` — inflation, gradients (used by websocket viz)

**Key concepts**
- *Occupancy fusion* — from `PointCloud2` / robot-centric grids
- *Geo overlays* — optional maps + VL querying

**Depends on**: `dimos.core`, `dimos.memory2`, `dimos.msgs`, `dimos.utils`, `dimos.memory` (legacy replay in tests)

**Subdirectories**
- `pointclouds/`, `occupancy/`, `voxels.py` — mapping core
- `osm/`, `google_maps/` — external map providers + models

---

## `dimos/navigation/`

> **Tagline.** **Motion planning & motion execution**: ROS navigation bridges, patrolling, frontier exploration, **visual servoing** helpers, joystick/teleop-adjacent routing.

**Public surface**
- `NavigationState`, `NavigationInterface` — `base.py`
- `NavigationInterfaceSpec` — `navigation_spec.py`
- `ROSNavModule` — `rosnav.py` (skills + ROS/LCM IO)
- `visual_servoing/` — 2D servo + detection navigation utilities
- `visual/query.py` — VLM bbox helpers
- `patrolling/`, `exploration/` — higher-level behaviors
- `global_planner/`, `motion_planning/` — planning stacks

**Key concepts**
- *Navigator spec injection* — typed RPC specs for "go here"
- *Visual servoing bridges* — perception + differential drive / TF

**Depends on**: `dimos.core`, `dimos.msgs`, `dimos.perception`, `dimos.models`, `dimos.protocol.tf`, `dimos.utils`, `dimos.agents` (rosnav `@skill`)

**Subdirectories**
- `visual_servoing/`, `visual/`, `patrolling/`, `exploration/`, `global_planner/`, `motion_planning/`, `tests/`, …

---

## `dimos/hardware/`

> **Tagline.** **Device drivers and adapters**: cameras (ZED, RealSense, GStreamer, webcam), **Livox/FAST-LIO2 lidar**, manipulators, drive trains, whole-body buses.

**Public surface**
- `Camera`-related modules — `sensors/camera/*` (+ `spec.py` configs)
- Lidar `Mid360`, `FastLio2` **NativeModule** wrappers — `sensors/lidar/*`
- `ManipulatorAdapter` / fieldbus — `manipulators/`, `drive_trains/`, `whole_body/`
- `fake_zed_module.py` — replay-friendly camera source

**Key concepts**
- *Perception specs* — `import dimos.spec.perception as …` marks camera streams
- *Native modules* — C++/SHM-backed publishers with `NativeModule`

**Depends on**: `dimos.core`, `dimos.msgs`, `dimos.spec`, `dimos.protocol.tf`, `dimos.mapping`, `dimos.visualization`, `dimos.robot`, `dimos.memory` (legacy stores in some modules)

**Subdirectories**
- `sensors/camera/`, `sensors/lidar/` — vision + LiDAR stacks (some **cpp READMEs**)
- `manipulators/`, `drive_trains/`, `whole_body/` — actuation side

---

## `dimos/robot/`

> **Tagline.** **Robot products in code**: **Typer/Click CLI** (`dimos run`, `mcp`, `log`, …), **auto-generated blueprint registry**, Unitree Go2/G1/B1, drone MAVLink/DJI, xArm/piper/openarm blueprints.

**Public surface**
- `main` / CLI — `cli/dimos.py`
- `get_by_name`, `class_name_to_registry_key` — `get_all_blueprints.py`
- `all_blueprints` — generated list (`all_blueprints.py`)
- `Robot` ABC — `robot.py` (legacy minimal interface)
- `FoxgloveBridge` — `foxglove_bridge.py`
- Large subtrees: `unitree/go2|g1|b1/`, `drone/`, `manipulators/*`

**Key concepts**
- *Blueprint registry* — string → runnable stack
- *Robot-specific types* — e.g. `unitree/type/lidar.py`, `odometry.py`

**Depends on**: nearly everything (`dimos.agents`, `dimos.core`, `dimos.hardware`, `dimos.simulation`, `dimos.navigation`, `dimos.mapping`, `dimos.msgs`, …)

**Subdirectories**
- `cli/` — user-facing **`dimos`** command
- `unitree/` — Go2/G1/B1 connection + blueprints
- `drone/` — MAVLink, DJI video, tracking
- `manipulators/` — xArm, Piper, OpenArm blueprint snippets
- `unitree_webrtc/` — WebRTC type shims re-exporting `unitree/type`

---

## `dimos/simulation/`

> **Tagline.** **Physics backends and glue**: MuJoCo shared memory + policy loops, optional Genesis/Isaac/Unity streams, **`SimulationEngine` registry** (currently **`mujoco`**).

**Public surface**
- `get_engine()` — `engines/registry.py`
- `MujocoEngine`, `MujocoSimModule` — `engines/mujoco_engine.py`, `mujoco_shm.py`
- `simulation/mujoco/*` — SHM writers, depth cam, explorer scripts
- `simulation/base/*` — abstract simulator/stream base classes

**Key concepts**
- *Engine selection* — string → simulation backend class
- *SHM bridge* — to `dimos.robot.unitree.mujoco_connection`

**Depends on**: `dimos.msgs`, `dimos.core` (via modules), `dimos.utils.data` (typical), `dimos.robot` (integration)

**Subdirectories**
- `engines/` — registry + MuJoCo integration
- `mujoco/` — process + SHM + camera helpers
- `genesis/`, `isaac/`, `unity/` — alternate sim integrations
- `utils/` — MJCF/XML helpers

---

## `dimos/teleop/`

> **Tagline.** **Human-in-the-loop input**: Meta Quest WebXR, phone browser UIs, keyboard joggers, wired to `RobotWebInterface` and **control** blueprints.

**Public surface**
- `QuestTeleopModule`, `ArmTeleopModule`, `quest_extensions` — VR poses / joy
- `PhoneTeleopModule` — touch twist teleop
- `keyboard/keyboard_teleop_module.py` — references `dimos.control.examples` IK jogger
- `quest/blueprints.py`, `phone/blueprints.py` — `autoconnect` compositions

**Key concepts**
- *WebXR → robot frame* — `teleop/utils/teleop_transforms.py`
- *Control integration* — imports `dimos.control.blueprints.teleop`

**Depends on**: `dimos.core`, `dimos.msgs`, `dimos.web`, `dimos.utils`, `dimos.robot`, `dimos.control`, `dimos.visualization`

**Subdirectories**
- `quest/`, `phone/`, `keyboard/` — device-specific modules + READMEs in quest/phone
- `utils/` — shared transforms

---

## `dimos/models/`

> **Tagline.** **ML model adapters**: **vision-language models** (Qwen/Moondream/Florence/OpenAI), **CLIP/MobileCLIP/TorchReID** embeddings, **EdgeTAM** segmentation, HuggingFace base classes.

**Public surface**
- `VlModel`, `create()` — `vl/base.py`, `vl/create.py`, `vl/types.py`
- Concrete VLMs — `vl/qwen.py`, `vl/moondream.py`, `vl/openai.py`, …
- `EmbeddingModel`, `CLIPModel`, … — `embedding/*`
- `EdgeTAMProcessor` — `segmentation/edge_tam.py`
- `HuggingFaceModel`, `LocalModel` — `base.py`

**Key concepts**
- *Resource lifecycle* — models subclass `dimos.core.resource.Resource`
- *Detection typing* — tight coupling to `dimos.perception.detection.types`

**Depends on**: `dimos.core`, `dimos.msgs`, `dimos.perception`, `dimos.protocol.service`, `dimos.utils`, `dimos.types`, `dimos.agents_deprecated` (Qwen video query)

**Subdirectories**
- `vl/`, `embedding/`, `segmentation/`, `qwen/`
- `vl/README.md` documents the vision-language stack

---

## `dimos/stream/`

> **Tagline.** **Auxiliary media pipelines** (distinct from **`dimos.core.stream`**) — **video providers** and **audio graphs** (mic, Whisper STT, OpenAI TTS).

**Public surface**
- `AbstractVideoProvider`, `VideoProvider` — `video_provider.py`
- `RTSP*`, ROS image providers — `rtsp_video_provider.py`, `ros_video_provider.py`
- `FrameProcessor`, `stream_merger`, `video_operators` — fusion utilities
- `stream/audio/*` — `SounddeviceAudioSource`, `WhisperNode`, `OpenAITTSNode`, pipelines

**Key concepts**
- *RX-style media graphs* — composable audio nodes + threadpool scheduling
- *Not the Module `In`/`Out` system* — separate package name; overlaps conceptually

**Depends on**: mostly `dimos.utils` (+ `dimos.constants` in some audio nodes)

**Subdirectories**
- `audio/` — base events, STT/TTS, microphone, pipelines

---

## `dimos/web/`

> **Tagline.** **HTTP + browser UX**: **Flask/FastAPI EdgeIO** servers, Svelte `dimos_interface`, React **command-center** extension, **Leaflet websocket map** visualization.

**Public surface**
- `RobotWebInterface` — `robot_web_interface.py` — bridges teleop modules to FastAPI UI
- `FastAPIServer` — `dimos_interface/api/server.py`
- `EdgeIO` — `edge_io.py` pub/sub edge
- `flask_server.py`, `fastapi_server.py` — alternate hosts
- `websocket_vis/*` — map/costmap websocket module + README

**Key concepts**
- *Edge IO* — HTTP/SSE bridge to internal streams
- *Websocket visualization* — consumes `OccupancyGrid`, paths, GPS from `dimos.mapping`

**Depends on**: `dimos.core`, `dimos.mapping`, `dimos.msgs`, `dimos.stream.audio`, `dimos.utils`

**Subdirectories**
- `dimos_interface/` — Svelte+Vite UI + `api/` FastAPI
- `command-center-extension/` — React Leaflet visualizer
- `websocket_vis/` — Python module + README
- `templates/` — HTML shells

---

## `dimos/experimental/`

> **Tagline.** **Unstable / demo-only code** — e.g. **`security_demo`** (YOLO + EdgeTAM + depth stub) compiled into optional blueprints.

**Public surface**
- `SecurityModule` — `experimental/security_demo/security_module.py`
- `DepthEstimator` — `depth_estimator.py`

**Key concepts**
- *Demo pipelines* — may import heavy CV stacks; not treated as stable API

**Depends on**: `dimos.agents`, `dimos.core`, `dimos.models`, `dimos.perception`

**Subdirectories**
- `security_demo/` — module + estimator + tests

---

## `dimos/porcelain/`

> **Tagline.** **Stable "single object" API** over **`ModuleCoordinator`**: `Dimos.run()`, `SkillsProxy`, local vs **remote** daemon module sources.

**Public surface**
- `Dimos` — `dimos.py`
- `LocalModuleSource`, `RemoteModuleSource`, `ModuleSource` ABC — process/daemon attachment
- `SkillsProxy` — remote skill invocation helper

**Key concepts**
- *Porcelain vs plumbing* — analogous to git: ergonomic layer over `core`

**Depends on**: `dimos.core`, `dimos.robot` (registry), `dimos.core.run_registry` (indirect via coordinator)

**Subdirectories**
- tests only (`test_*.py`)

---

## `dimos/spec/`

> **Tagline.** **Wiring contracts**: base **`Spec` Protocol marker**, helpers (`is_spec`, compliance checks), and **small domain marker modules** (`spec.perception`, `spec.mapping`, …).

**Public surface**
- `Spec`, `is_spec()`, `spec_structural_compliance()`, … — `spec/utils.py`
- `dimos/spec/perception.py`, `nav.py`, `mapping.py`, `control.py` — **`Module`-side capability markers** imported as `from dimos.spec import perception` pattern in hardware

**Key concepts**
- *Spec vs plain Protocol* — `Spec` in MRO distinguishes injectable RPC faces
- *Domain specs* — narrow semantic tags for stream typing

**Depends on**: mostly typing/inspect + `annotation_protocol`

---

## `dimos/protocol/`

> **Tagline.** **Transports & services**: pubsub (**LCM**, pickled LCM, ROS, SHM, Redis, memory), **RPC** (LCMRPC, RedisRPC), **TF**, service discovery/configurator, codecs.

**Public surface**
- `PubSub`, topics, encoders — `pubsub/spec.py`, `pubsub/impl/*`
- `LCMRPC`, `PickleLCM` — `rpc/pubsubrpc.py`, ties into pubsub layer
- `LCMTF`, `TFSpec` — `tf/tf.py`
- `LCMService`, DDS mirror — `service/lcmservice.py`, `service/ddsservice.py`
- `Configurable`, `BaseConfig` — `service/spec.py`

**Key concepts**
- *Pluggable pubsub backends* — same `PubSub` interface
- *Cross-process RPC* — exceptions serialized via `rpc_utils`

**Depends on**: `dimos.constants`, `dimos.utils`; `pubsub` may use optional extras (DDS, ROS)

**Subdirectories**
- `pubsub/impl/` — lcm, ros, shm, redis, dds, jpeg, …
- `rpc/`, `service/`, `tf/`, `encode/`
- `pubsub/benchmark/` — perf harness

---

## `dimos/exceptions/`

> **Tagline.** Error types for the **deprecated AgentMemory / vector DB** layer.

**Public surface**
- `AgentMemoryError`, `AgentMemoryConnectionError`, `DataNotFoundError`, … — `agent_memory_exceptions.py` only

**Key concepts**
- *Typed retrieval failures* — vector ID missing, connection issues

**Depends on**: stdlib only (no `dimos.*` imports in that file)

---

## `dimos/visualization/`

> **Tagline.** **Rerun integration**: subscribe to LCM-style traffic, convert messages implementing **`to_rerun()`**, launch viewers.

**Public surface**
- `RerunBridgeModule` — `rerun/bridge.py` (note external `rerun.blueprint` vs DimOS `Blueprint`)
- `rerun_init` and helpers — `rerun/init.py`

**Key concepts**
- *PubSub tap-in* — pattern matching on topics
- *Optional grpc/web viewer ports* — exported constants near module top

**Depends on**: `dimos.core`, `dimos.protocol.pubsub`, `dimos.utils`

**Subdirectories**
- `rerun/` — bridge + tests + `init.py`

---

## `dimos/utils/`

> **Tagline.** **Shared infrastructure**: logging setup, **data** path helpers (`get_data`), math (`Vector`, transforms), **Rx helpers**, CLI diagnostics (**`lcmspy`**, **`dtop`**, agentspy, foxglove bridge launcher), replay/moment testing utilities.

**Public surface** (representative)
- `setup_logger`, per-run log dirs — `logging_config.py`
- `get_data`, `get_data_dir` — `data.py`
- `backpressure` Rx utilities — `reactive.py`
- CLI entry modules — `cli/lcmspy/lcmspy.py`, `cli/dtop.py`, `cli/agentspy/agentspy.py`, …
- `transform_utils`, `trigonometry`, `path_utils`, `urdf.py`, …

**Key concepts**
- *Test replay* — `utils/testing/replay.py`, `moment.py` for deterministic sensors
- *Operational CLI* — introspection without starting a full `dimos` stack

**Depends on**: widely references `dimos.msgs`, `dimos.core`, `dimos.memory*`, `dimos.robot` in test helpers, `dimos.protocol`, `dimos.types`

**Subdirectories**
- `cli/` — operator commands
- `testing/` — replay/moment fixtures
- `decorators/`, `docs/` — cross-cutting helpers

---

## `dimos/types/`

> **Tagline.** **Lightweight shared types** that are not full **`msgs`**: numpy **`Vector`**, timestamp helpers, robot capabilities, weak containers.

**Public surface**
- `Vector` — `vector.py`
- `Timestamped`, `to_timestamp` — `timestamped.py`
- `WeakList` — `weaklist.py`
- `Sample` — `sample.py`
- `RobotLocation` — `robot_location.py`
- `Vector3` polyfill — `ros_polyfill.py`
- `RobotCapability` — `robot_capabilities.py` (used by `dimos.robot.robot.Robot`)
- `Colors` — re-export area in `types/constants` if present

**Key concepts**
- *Non-ROS geometry helpers* — avoid heavy `geometry_msgs` for pure-Python math
- *Spatial memory identity* — `RobotLocation` ties name↔pose metadata

**Depends on**: mostly `numpy`; optionally `dimos.msgs` in a few conversions

---

## `dimos/msgs/`

> **Tagline.** **Typed message models** mirroring ROS 1 shapes (geometry, sensor, nav, trajectory, vision_msgs) plus **`DimosMsg`**, Foxglove overlays, and extensive round-trip tests.

**Public surface**
- Per-topic classes — e.g. `sensor_msgs/Image.py`, `geometry_msgs/PoseStamped.py`, `nav_msgs/OccupancyGrid.py`, …
- `DimosMsg` protocol — `msgs/protocol.py`
- shared helpers — `msgs/helpers.py`

**Key concepts**
- *Serialization-friendly dataclasses/pydantic-ish patterns* — used across LCM/ROS bridges
- *Image barriers* — `sharpness_barrier` hooks in `Image.py` for load shedding

**Depends on**: internal self-references among `msgs/*` packages (minimal `dimos.*` otherwise)

**Subdirectories**
- `geometry_msgs/`, `sensor_msgs/`, `nav_msgs/`, `std_msgs/`, `trajectory_msgs/`, `vision_msgs/`, `tf2_msgs/`, `foxglove_msgs/`, …

---

## `dimos/project/`

> **Tagline.** **Meta-tests** guarding repo conventions — **not** a runtime "version" module (use **`pyproject.toml`** / packaging for version metadata).

**Public surface**
- `test_get_logger.py`, `test_no_init_files.py`, … — enforce logging and layout policies

**Key concepts**
- *CI hygiene* — static scans over tree

**Depends on**: `dimos.constants` (path roots)

---

## `dimos/rxpy_backpressure/`

> **Tagline.** Small **RxPy operator kit** for **Latest / Drop / Buffer** strategies on observers (`BackPressure` namespace).

**Public surface**
- `BackPressure` — `backpressure.py` — references `drop.py`, `latest.py` modules
- `wrap_observer_with_*` — individual strategy helpers

**Key concepts**
- *Observer-side flow control* — complement to `dimos.utils.reactive`

**Depends on**: internal package only (re-imports sibling files under `rxpy_backpressure/`)

---

## How to read this doc when navigating the codebase

1. **Looking for a behavior?** Start in `agents/skills/` → trace which `Module` exposes the `@skill` → walk into the supporting layer (e.g. `navigation`, `mapping`, `manipulation`).
2. **Looking for an I/O contract?** Start in `spec/` (capability) and `msgs/` (payload), then `protocol/` for transport.
3. **Looking for the runtime?** `core/` is the kernel; `porcelain/` is the ergonomic layer; `robot/cli/` is the user-facing entrypoint.
4. **Sensor → action chain (typical):** `hardware/sensors` → `perception` → `memory2` (+ `mapping`) → `agents` (planning) → `skills` / `navigation` / `manipulation` → `control` → `hardware/manipulators|drive_trains`.
5. **Treat as legacy & avoid in new code:** `agents_deprecated/`, `skills/` (Pydantic flavor), `memory/`, `exceptions/`.
