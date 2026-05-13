# DimOS Modul-Architektur (Deutsche Version)

> Semantische Karte der 28 Top-Level-Module unter `dimos/`. Automatisch erstellt aus Import-Graph- und Quellcode-Analyse auf dem `dev`-Branch. Behandeln Sie die **öffentliche API** (public surface) als den kleinsten stabilen Vertrag, auf den Sie sich verlassen können, ohne den Quellcode zu durchforsten.
>
> *Englische Originalversion: [`modules.md`](/docs/architecture/modules.md) · 中文: [`modules_cn.md`](/docs/architecture/modules_cn.md) · 日本語: [`modules_ja.md`](/docs/architecture/modules_ja.md)*

`dimos/__init__.py` lädt nur **`Dimos`** aus `dimos.porcelain.dimos` lazy. Die meisten Subpakete besitzen **keine kuratierten Exports in der `__init__.py`** (Namespace-artiges Layout) — solange keine expliziten Re-Exports hinzugefügt werden, behandeln Sie **konkrete Module** als die öffentliche API.

---

## Modulübersicht (auf einen Blick)

| Modul | Schicht | Aufgabe (eine Zeile) |
|--------|-------|-----------------|
| **agents** | Gehirn | LLM-Werkzeugkasten: `@skill`, MCP-Client/Server, optionaler VL-Agent, Skill-Container für Navigation/Sprache |
| **agents_deprecated** | Legacy | Alter `OpenAIAgent` / Tokenizer / **chromadb**-Memory-Stack. Wird von älteren Demos verwendet |
| **skills** | Gehirn (Legacy) | Älteres **Pydantic `AbstractSkill` + `SkillLibrary`** Tool-Schema (parallel zu `@skill` RPC) |
| **control** | Aktuierung | **100 Hz `ControlCoordinator`**, Tasks, Tick-Loop, Gelenk-Arbitrierung für Arme |
| **core** | Laufzeit | **Module, Streams, Transports, Blueprints, Koordinator, RPC, Worker, globale Konfiguration** |
| **memory** | Daten (Legacy) | Embedding + **Zeitreihen-Stores** (`EmbeddingMemory`, SQLite/Postgres-Backends) |
| **memory2** | Daten | **Stream-basiertes Memory-System**: Beobachtungen, Stores, Codecs, `StreamModule`-Integration |
| **perception** | Sensorik | Erkennung, Tracking, räumliches Gedächtnis, experimentelles temporales Gedächtnis |
| **manipulation** | Bewegung | Pick-and-Place, Planung (Drake/MJCF), Trajektorie-/Steuerungs-Helfer unter `control/` |
| **mapping** | Räumlich | Belegungsgitter, Voxel, OSM/Google Maps-Helfer, **`LatLon`**-Modelle |
| **navigation** | Bewegung | ROS-Navigations-Brücken, Patrouillieren, **visuelles Servoing**, Frontier/Exploration |
| **hardware** | Treiber | Kameras, LiDAR, Manipulatoren, Antriebsstränge, Whole-Body-Adapter |
| **robot** | Produkt | **`dimos run` CLI**, Blueprint-Registry, Unitree/Drohnen/xArm-Stacks |
| **simulation** | Simulation | MuJoCo SHM, Genesis/Isaac/Unity-Stubs, **Engine-Registry** |
| **teleop** | Eingabe | Quest-/Telefon-/Tastatur-Teleop-Module und Blueprints |
| **models** | ML | **VLM**, CLIP/MobileCLIP-Embeddings, Segmentierung (EdgeTAM), HuggingFace-Wrapper |
| **stream** | Medien | **Video** (Webcam/RTSP/ROS) und **Audio** (Mikrofon, Whisper, TTS) Pipelines |
| **web** | UI | Flask/FastAPI **EdgeIO**, Svelte `dimos_interface`, Karten-**`websocket_vis`** |
| **experimental** | wip | **Instabile Demos** (z. B. `security_demo`-Pipeline) |
| **porcelain** | API | **`Dimos`-Fassade**: lokale/remote Modul-Quellen, **`SkillsProxy`** |
| **spec** | Typisierung | **`Spec`-Marker-Protocol** + Domänen-`spec.perception` / `nav` / `mapping` / `control` |
| **protocol** | Transport | **LCM/ROS/SHM/DDS/Redis** Pub/Sub, **LCMRPC**, TF, Service-Konfigurator |
| **exceptions** | Legacy | **Agent-Memory-spezifische** Ausnahmehierarchie (Legacy-Memory-Pfad) |
| **visualization** | Betrieb | **Rerun-Brücke** + Initialisierungs-Helfer |
| **utils** | Infrastruktur | Logging, Datenpfade, CLI-Tools (`lcmspy`, `dtop`, replay, foxglove, …) |
| **types** | Typisierung | **`Vector`**, `Timestamped`, **`RobotLocation`**, weak list, ROS-Polyfills |
| **msgs** | Typisierung | **ROS-ähnliche typisierte Nachrichten** + `DimosMsg`-Protocol |
| **project** | CI | **Repository-Hygiene-Tests** (z. B. `getLogger`-Logging-Wächter); keine Versions-API |
| **rxpy_backpressure** | Infrastruktur | **`BackPressure`**-Fassade (Latest/Drop/Buffer Observer-Strategien) |

### Querschnittliche Klarstellungen (einmal lesen)

- **Zwei parallele Skill-Systeme**: Der `@skill` + MCP-Pfad in `agents/` vs. der Pydantic `AbstractSkill`-Pfad in `skills/`. **Neuer Code sollte den ersteren bevorzugen.**
- **`stream/` ≠ `core.stream`**: `dimos/stream/` ist *Medien-Pipelines* (Video + Audio); `dimos.core.stream` ist *Modul-I/O*-Primitiven (`In`/`Out`/`Transport`). **Namenskollision.**
- **`memory/` ist Legacy**, `memory2/` ist die aktuell gepflegte Beobachtungs-/Speicher-Pipeline.
- **`project/`** ist eine Sammlung von *CI-Hygiene-Tests*, **kein** Versions-/Metadatenmodul — die Versionsnummer kommt aus `pyproject.toml`.
- **`exceptions/`** enthält nur Fehlertypen des Legacy-Memory-Systems, **nicht** den projektweiten Fehler-Namespace.

---

## Top-Level `dimos`-Paket

> **Kurzbeschreibung.** Lazy-Eintrittspunkt: `dimos/__init__.py` exportiert nur `Dimos`; alles andere muss explizit aus Subpaketen importiert werden.

**Öffentliche API**
- `Dimos` (`dimos.porcelain.dimos`) — Blueprints bauen/ausführen, optional Remote-Daemon-Verkabelung

**Kernkonzepte**
- *Lazy-Paket-Attribut* — `__getattr__` lädt `Dimos` beim ersten Zugriff

**Abhängig von**: (beim Laden) `dimos.porcelain`, `dimos.core`, `dimos.robot`, …

---

## `dimos/agents/`

> **Kurzbeschreibung.** Agent-*Framework*: LLM-**MCP**-Stack (`McpClient` / `McpServer`), **`@skill`**-Dekorator, VL-Agent-Modul und roboter-agnostische **Skill-Container** (Navigation, GPS/Karten, Sprache, …).

**Öffentliche API** (kein Paket-`__init__.py`; typische Imports)
- `skill` / `current_skill_context()` — `dimos/agents/annotation.py` — markiert RPC-Methoden als LLM/MCP-Tools; optionaler MCP-Fortschrittskontext
- `McpClient`, `McpClientConfig` — LangChain-Agent + HTTP-MCP-Tools + LCM-Streams
- `McpServer` — FastAPI-JSON-RPC-MCP-Server, der die `@skill`s eines Moduls bereitstellt
- `McpAdapter` — Adapter-Verkabelung für MCP-Integrationstests
- `AgentSpec` — Protocol für "etwas, das Agent-Anweisungen entgegennimmt"
- `VLMAgent`, `VLMAgentConfig` — Bild-I/O + VLM-orientiertes Modul
- `AgentTestRunner` — Test-Harness-Modul für Agent-Stacks
- `SYSTEM_PROMPT` — `dimos/agents/system_prompt.py`, Standard-Prompt-Text
- `ensure_ollama_model()`, `ollama_installed()` — `dimos/agents/ollama_agent.py`-Ollama-Helfer (kein vollständiger `Agent`-Klasse)

**Kernkonzepte**
- *`@skill`* — `@rpc` + `__skill__`; treibt MCP-Tool-Schemas
- *MCP HTTP-Brücke* — externes Tool-Protocol über FastAPI (Standard-Port aus `GlobalConfig`)
- *Skill-Container* — `Module`-Subklassen, die mehrere `@skill`s für ein Szenario auf einmal bereitstellen

**Abhängig von**: `dimos.core`, `dimos.spec`, `dimos.msgs`, `dimos.utils`, häufig auch `dimos.navigation`, `dimos.mapping`, `dimos.models`, `dimos.perception`, `dimos.stream`, `dimos.robot` (in Tests/Demos), `dimos.web`, `dimos.constants`

**Unterverzeichnisse**
- `mcp/` — `mcp_server.py`, `mcp_client.py`, `mcp_adapter.py`, `tool_stream.py`
- `skills/` — konkrete Skill-`Module` (Navigation, Sprache, OSM, GPS, Google Maps, Personenverfolgung, Demos)

---

## `dimos/agents_deprecated/`

> **Kurzbeschreibung.** **Veralteter** LangChain-artiger **`OpenAIAgent`**-Stack mit **Chroma/OpenAI semantischem Memory**, Tokenizer-Helfern und Prompt-Builder — bleibt erhalten für ältere Skripte und `dimos/models/qwen/video_query.py`.

**Öffentliche API**
- `OpenAIAgent` und verwandte Klassen — `dimos/agents_deprecated/agent.py` (großes Legacy-Modul)
- `OpenAISemanticMemory` — `memory/chroma_impl.py`
- `PromptBuilder` — `prompt_builder/impl.py`

**Kernkonzepte**
- *Legacy-Agent-Loop* — RxPy + Tool-Calling vor `McpClient`
- *Chroma-Memory* — Vektor-DB-gestütztes "AgentMemory" (verwendet `dimos.exceptions`)

**Abhängig von**: `dimos.skills`, `dimos.stream`, `dimos.core` (über Memory-Module), `dimos.exceptions`, …

**Unterverzeichnisse**
- `memory/` — Chroma + räumliches/visuelles Memory-Komponenten
- `prompt_builder/`, `tokenizer/` — Legacy-Prompt-/Token-Helfer

---

## `dimos/skills/`

> **Kurzbeschreibung.** **Legacy-Pydantic-Skill-Bibliothek** (`AbstractSkill`, `SkillLibrary`), erzeugt OpenAI-artige **Function-Tools** — überschneidet sich konzeptionell mit `@skill`, verwendet aber andere Verkabelung.

**Öffentliche API**
- `SkillLibrary` — sammelt/führt `AbstractSkill`-Subklassen aus, JSON-Tool-Export
- `AbstractSkill`, `AbstractRobotSkill` — Pydantic-basierte Tool-Modelle
- `kill_skill` und Manipulation-Wrapper — siehe `skills/kill_skill.py`, `skills/manipulation/*`, `visual_navigation_skills.py`, `rest/rest.py`

**Kernkonzepte**
- *Klassen-basierte Skills* — Subklassen entdecken und als Tools veröffentlichen
- *Roboter-Skill* — optionaler `dimos.robot.robot.Robot`-Handle

**Abhängig von**: `dimos.types`, `dimos.utils`

**Unterverzeichnisse**
- `manipulation/` — Constraint- / Pick-and-Place-artige Skills
- `rest/` — REST-orientierter Skill-Helfer
- `unitree/` — z. B. Sprach-Wrapper

---

## `dimos/control/`

> **Kurzbeschreibung.** **Niedrige Multi-Arm-Steuerung**: **`ControlCoordinator`** + **100 Hz `TickLoop`**, komponierbare **Tasks** (Teleop, Servo, Trajektorie, Geschwindigkeit), Prioritäts-Arbitrierung.

**Öffentliche API**
- `ControlCoordinator`, `ControlCoordinatorConfig` — `coordinator.py` zentrales Koordinator-`Module`
- `TickLoop` — deterministische Periodenschleife
- `Task`, `ControlTask`-Hierarchie — `task.py`, `tasks/*`
- `ConnectedHardware`, `HardwareComponent`s — `hardware_interface.py`, `components.py`
- `control/blueprints/*` — fertige Blueprint-Fragmente (basic, dual, mobile, teleop)

**Kernkonzepte**
- *Task-Arbitrierung* — mehrere Schreiber → Gelenkbefehl-Auflösung
- *Manipulator-Adapter* — Integration mit `dimos.hardware.manipulators.spec`

**Abhängig von**: `dimos.constants`, `dimos.msgs`, `dimos.hardware.manipulators`, `dimos.manipulation` (IK-Tasks), `dimos.utils`

**Unterverzeichnisse**
- `tasks/` — `velocity_task`, `trajectory_task`, `teleop_task`, `servo_task`, `cartesian_ik_task`
- `blueprints/` — Blueprint-Voreinstellungen
- `examples/` — Tastatur-Teleop, IK-Jogger-Demos

---

## `dimos/core/`

> **Kurzbeschreibung.** **Laufzeit-Kernel**: **`Module`**, **`In`/`Out`**-Streams, **Transports**, **`Blueprint`/`autoconnect`**, **`ModuleCoordinator`**, Worker, RPC, Run-Registry, Docker/Native-Module, Introspektion.

**Öffentliche API**
- `Module`, `ModuleBase`, `ModuleConfig`, `SkillInfo`, … — `module.py`
- `In`, `Out`, `Transport`, `Stream` — `stream.py` (Stream-Schicht; **nicht** das separate `dimos/stream`-Paket)
- `LCMTransport`, `pLCMTransport`, `ROSTransport`, … — `transport.py`
- `rpc` / `T` — `core.py`-Dekorator-/Typ-Helfer
- `Blueprint`, `BlueprintAtom`, `autoconnect()` — `coordination/blueprints.py`
- `ModuleCoordinator` — `coordination/module_coordinator.py`
- `GlobalConfig`, `global_config` — `global_config.py`
- `WorkerManagerPython`, Docker-Worker, `PythonWorker` — `coordination/*`
- `RunEntry`, Run-Registry-Helfer — `run_registry.py`
- `RPCClient`, `RpcCall` — `rpc_client.py`

**Kernkonzepte**
- *Stream-Verkabelung* — Blueprint paart `In`/`Out` über `(name, type)`
- *Forkserver-Worker* — Prozess-Isolation für Module
- *RPC über LCM* — `LCMRPC` über `dimos.protocol.rpc.pubsubrpc`

**Abhängig von**: `dimos.spec`, `dimos.protocol.*`, `dimos.utils`, `dimos.msgs` (indirekt über Transports), `dimos.models.vl.types` (VL-Name-Enum in der globalen Konfiguration); Tests importieren `dimos.agents`, `dimos.robot.cli`

**Unterverzeichnisse**
- `coordination/` — Blueprints, Koordinator, Worker-Manager, Prozess-Lebenszyklus, rpyc, Daemon-Hooks
- `introspection/` — Modul-/Blueprint-Graph & ANSI/dot-Rendering
- `resource_monitor/` — Ressourcen-Statistiken/Logging
- `tests/`, `test_*.py` — umfangreiche Unit-/E2E-Tests

---

## `dimos/memory/`

> **Kurzbeschreibung.** **Legacy** Embedding-Memory + **generische Zeitreihen**-Backends (SQLite/Postgres/pickle/in-memory).

**Öffentliche API**
- `EmbeddingMemory`, `Config` — `embedding.py` — CLIP-auf-Rx-Bildpfad (Costmap-Hooks sind Stubs/experimentell)
- `timeseries/*` — `base.py` + `sqlite.py`, `postgres.py`, `pickledir.py`, `inmemory.py`, `legacy.py`

**Kernkonzepte**
- *Räumliche Embeddings* — Bild+Costmap-Fusion-Idee (im Code noch als "would be cool" markiert)
- *Zeitreihen-Store* — wiederverwendbare Persistenz für Sensor-Zeitlinien

**Abhängig von**: `dimos.core`, `dimos.models.embedding`, `dimos.msgs`, `dimos.utils.reactive`

**Unterverzeichnisse**
- `timeseries/` — Store-Implementierungen + Tests

---

## `dimos/memory2/`

> **Kurzbeschreibung.** **Aktuelles Memory-Subsystem**: typisierte **Beobachtungen**, **Transformations-Streams**, austauschbare **Blob-/Vektor-/Beobachtungs-Stores**, Codecs, **`StreamModule`** als Brücke zu `dimos.core`-I/O.

**Öffentliche API**
- `Stream` — `stream.py` — Transformationen, Backpressure, Abfragen
- `StreamModule`, `port_to_stream()`, `stream_to_port()` — `module.py`
- `Backend` — `backend.py` — komposites Beobachtung + Blob + Vektor + Notifier
- `EmbedImages` — `embed.py`
- `registry.py`, `buffer.py`, `transform.py`, `type/observation.py`, `store/sqlite.py`, `blobstore/*`, `vectorstore/*`, `codecs/*`, `vis/*`

**Kernkonzepte**
- *Beobachtungs-Pipeline* — append → transform → embed → persist → notify
- *Codec-Plugins* — pickle, LCM, lz4, jpeg etc.

**Abhängig von**: `dimos.core`, `dimos.models.embedding`, `dimos.msgs`, `dimos.utils`, `dimos.agents.annotation` (Skill-Helfer in `module.py`), `dimos.constants`

**Unterverzeichnisse**
- `store/`, `blobstore/`, `observationstore/`, `vectorstore/` — Storage-Backends
- `codecs/` — Serialisierung für Blobs/Payloads
- `type/` — `Observation`, Filter, Query-Typen
- `notifier/` — reaktive Benachrichtigung
- `utils/`, `vis/` — Validierung, Plotting/rerun/svg-Helfer

---

## `dimos/perception/`

> **Kurzbeschreibung.** **Verstehen**: 2D/3D-Erkennung/Tracking, **räumliches Gedächtnis**, VLM-Hooks, plus **experimentelles temporales** Graph-Gedächtnis.

**Öffentliche API**
- `ObjectTrackingSpec`, `SpatialMemorySpec` — `object_tracking_spec.py`, `spatial_memory_spec.py`
- `SpatialMemory`-Modul — `spatial_perception.py` (importiert noch das **`agents_deprecated`** Visual-Memory)
- `PerceiveLoopSkill`-artige Komponenten — `perceive_loop_skill.py`
- `detection/` — YOLO/SAM/EdgeTAM-gewrappte Detektoren und Nachrichtentypen
- `experimental/temporal_memory/` — temporaler Graph / fensterbasierte Videoanalyse (hat README)

**Kernkonzepte**
- *Specs für Wahrnehmungs-Ports* — `dimos.spec.perception`
- *Räumliches Gedächtnis* — benannte `RobotLocation`s über `dimos.types.robot_location`

**Abhängig von**: `dimos.core`, `dimos.msgs`, `dimos.spec`, `dimos.models`, `dimos.types`, `dimos.agents`, `dimos.agents_deprecated` (Legacy-Pfad), `dimos.stream`, `dimos.robot` (Tests), `dimos.utils`

**Unterverzeichnisse**
- `detection/` — Detektoren + 2D/3D-Nachrichtentypen
- `experimental/temporal_memory/` — laufender temporaler Reasoning-Stack

---

## `dimos/manipulation/`

> **Kurzbeschreibung.** **Arme und Bewegung**: Pick-and-Place-Module, **Drake**-Weltmodell + Trajektoriengenerierung, Pinocchio IK, und **verschachtelte `manipulation/control/`** (Servo, Trajektorien-Controller, Koordinator-**Client**).

**Öffentliche API**
- `ManipulationModule` / Pick-and-Place — `manipulation_module.py`, `pick_and_place_module.py`
- `planning/` — Drake-`World`, Trajektoriengenerator, URDF/Mesh-Utilities, Konfigurationen in `planning/spec/*`
- `control/` — `cartesian_motion_controller.py`, `joint_trajectory_controller.py`, `coordinator_client.py`, `arm_driver_spec.py` (verbindet sich mit `dimos.control`-Koordinator-Mustern)

**Kernkonzepte**
- *WorldSpec / JointTrajectory* — Austausch zwischen Planern
- *Servo vs. Trajektorie* — unterschiedliche Control-Task-Bindungen

**Abhängig von**: `dimos.core`, `dimos.msgs`, `dimos.perception` (3D-Objekttypen), `dimos.utils`

**Unterverzeichnisse**
- `planning/` — Welt, Trajektorie, Kinematik, Specs (README in `planning/`)
- `control/` — Low-Level-Arm-Bewegungssteuerungen, **direkt neben** dem Top-Level-`dimos/control`-Koordinator

---

## `dimos/mapping/`

> **Kurzbeschreibung.** **Karten und geografischer Kontext**: Belegungsgitter aus Punktwolken, **Voxel-Pipeline**, OSM "aktuelle Position", Google-Maps-Integration, **`LatLon`**-Modelle.

**Öffentliche API**
- `LatLon` und Verwandte — `models.py`, `google_maps/*`, `osm/*`
- `VoxelGrid` / `VoxelGridMapper` — `voxels.py` (verwendet **`memory2.StreamModule`**)
- `pointclouds/occupancy.py` — Gitter-Erzeugungs-Helfer
- `occupancy/` — Inflation, Gradienten (von `websocket_vis` verwendet)

**Kernkonzepte**
- *Belegungs-Fusion* — aus `PointCloud2` / roboterzentrischen Gittern
- *Geo-Overlays* — optionale Karten + VL-Abfragen

**Abhängig von**: `dimos.core`, `dimos.memory2`, `dimos.msgs`, `dimos.utils`, `dimos.memory` (Legacy-Replay in Tests)

**Unterverzeichnisse**
- `pointclouds/`, `occupancy/`, `voxels.py` — Mapping-Kern
- `osm/`, `google_maps/` — externe Kartenanbieter + Modelle

---

## `dimos/navigation/`

> **Kurzbeschreibung.** **Bewegungsplanung & Bewegungsausführung**: ROS-Navigations-Brücken, Patrouillieren, Frontier-Exploration, **Visual-Servoing**-Helfer, Joystick/Teleop-benachbartes Routing.

**Öffentliche API**
- `NavigationState`, `NavigationInterface` — `base.py`
- `NavigationInterfaceSpec` — `navigation_spec.py`
- `ROSNavModule` — `rosnav.py` (Skills + ROS/LCM-IO)
- `visual_servoing/` — 2D-Servo + Detection-Navigation-Utilities
- `visual/query.py` — VLM-Bbox-Helfer
- `patrolling/`, `exploration/` — höhere Verhaltensschichten
- `global_planner/`, `motion_planning/` — Planungs-Stacks

**Kernkonzepte**
- *Navigator-Spec-Injection* — typisierte RPC-Specs für "geh dorthin"
- *Visual-Servoing-Brücken* — Wahrnehmung + Differentialantrieb / TF

**Abhängig von**: `dimos.core`, `dimos.msgs`, `dimos.perception`, `dimos.models`, `dimos.protocol.tf`, `dimos.utils`, `dimos.agents` (rosnav `@skill`)

**Unterverzeichnisse**
- `visual_servoing/`, `visual/`, `patrolling/`, `exploration/`, `global_planner/`, `motion_planning/`, `tests/`, …

---

## `dimos/hardware/`

> **Kurzbeschreibung.** **Gerätetreiber und Adapter**: Kameras (ZED, RealSense, GStreamer, Webcam), **Livox/FAST-LIO2-LiDAR**, Manipulatoren, Antriebsstränge, Whole-Body-Busse.

**Öffentliche API**
- `Camera`-bezogene Module — `sensors/camera/*` (+ `spec.py`-Konfigurationen)
- LiDAR-`Mid360`, `FastLio2`-**`NativeModule`**-Wrapper — `sensors/lidar/*`
- `ManipulatorAdapter` / Feldbus — `manipulators/`, `drive_trains/`, `whole_body/`
- `fake_zed_module.py` — replay-freundliche Kamera-Quelle

**Kernkonzepte**
- *Wahrnehmungs-Specs* — `import dimos.spec.perception as …` markiert Kamera-Streams
- *Native-Module* — C++/SHM-gestützte Publisher mit `NativeModule`

**Abhängig von**: `dimos.core`, `dimos.msgs`, `dimos.spec`, `dimos.protocol.tf`, `dimos.mapping`, `dimos.visualization`, `dimos.robot`, `dimos.memory` (Legacy-Stores in einigen Modulen)

**Unterverzeichnisse**
- `sensors/camera/`, `sensors/lidar/` — Vision- + LiDAR-Stacks (einige mit **C++-READMEs**)
- `manipulators/`, `drive_trains/`, `whole_body/` — Aktuierungs-Seite

---

## `dimos/robot/`

> **Kurzbeschreibung.** **Roboterprodukte im Code**: **Typer/Click-CLI** (`dimos run`, `mcp`, `log`, …), **automatisch generierte Blueprint-Registry**, Unitree Go2/G1/B1, Drohne MAVLink/DJI, xArm/piper/openarm-Blueprints.

**Öffentliche API**
- `main` / CLI — `cli/dimos.py`
- `get_by_name`, `class_name_to_registry_key` — `get_all_blueprints.py`
- `all_blueprints` — generierte Liste (`all_blueprints.py`)
- `Robot`-ABC — `robot.py` (Legacy, minimale Schnittstelle)
- `FoxgloveBridge` — `foxglove_bridge.py`
- Große Subtrees: `unitree/go2|g1|b1/`, `drone/`, `manipulators/*`

**Kernkonzepte**
- *Blueprint-Registry* — String → ausführbarer Stack
- *Roboter-spezifische Typen* — z. B. `unitree/type/lidar.py`, `odometry.py`

**Abhängig von**: nahezu allem (`dimos.agents`, `dimos.core`, `dimos.hardware`, `dimos.simulation`, `dimos.navigation`, `dimos.mapping`, `dimos.msgs`, …)

**Unterverzeichnisse**
- `cli/` — benutzergerichteter **`dimos`**-Befehl
- `unitree/` — Go2/G1/B1-Verbindung + Blueprints
- `drone/` — MAVLink, DJI-Video, Tracking
- `manipulators/` — xArm-, Piper-, OpenArm-Blueprint-Fragmente
- `unitree_webrtc/` — WebRTC-Typ-Shims, re-exportieren `unitree/type`

---

## `dimos/simulation/`

> **Kurzbeschreibung.** **Physik-Backends und Klebstoff**: MuJoCo Shared Memory + Policy-Loops, optionale Genesis/Isaac/Unity-Streams, **`SimulationEngine`-Registry** (aktuell **`mujoco`**).

**Öffentliche API**
- `get_engine()` — `engines/registry.py`
- `MujocoEngine`, `MujocoSimModule` — `engines/mujoco_engine.py`, `mujoco_shm.py`
- `simulation/mujoco/*` — SHM-Writer, Tiefenkamera, Explorer-Skripte
- `simulation/base/*` — abstrakte Simulator/Stream-Basisklassen

**Kernkonzepte**
- *Engine-Auswahl* — String → Simulations-Backend-Klasse
- *SHM-Brücke* — zu `dimos.robot.unitree.mujoco_connection`

**Abhängig von**: `dimos.msgs`, `dimos.core` (über Module), `dimos.utils.data` (typisch), `dimos.robot` (Integration)

**Unterverzeichnisse**
- `engines/` — Registry + MuJoCo-Integration
- `mujoco/` — Prozess + SHM + Kamera-Helfer
- `genesis/`, `isaac/`, `unity/` — alternative Sim-Integrationen
- `utils/` — MJCF/XML-Helfer

---

## `dimos/teleop/`

> **Kurzbeschreibung.** **Mensch-im-Loop-Eingabe**: Meta Quest WebXR, Telefon-Browser-UIs, Tastatur-Jogger, verbunden mit `RobotWebInterface` und **`control`**-Blueprints.

**Öffentliche API**
- `QuestTeleopModule`, `ArmTeleopModule`, `quest_extensions` — VR-Posen / Joy
- `PhoneTeleopModule` — Touch-Twist-Teleop
- `keyboard/keyboard_teleop_module.py` — referenziert IK-Jogger in `dimos.control.examples`
- `quest/blueprints.py`, `phone/blueprints.py` — `autoconnect`-Kompositionen

**Kernkonzepte**
- *WebXR → Roboter-Frame* — `teleop/utils/teleop_transforms.py`
- *Control-Integration* — importiert `dimos.control.blueprints.teleop`

**Abhängig von**: `dimos.core`, `dimos.msgs`, `dimos.web`, `dimos.utils`, `dimos.robot`, `dimos.control`, `dimos.visualization`

**Unterverzeichnisse**
- `quest/`, `phone/`, `keyboard/` — gerätspezifische Module + READMEs in quest/phone
- `utils/` — gemeinsame Transformationen

---

## `dimos/models/`

> **Kurzbeschreibung.** **ML-Modell-Adapter**: **Vision-Language-Modelle** (Qwen/Moondream/Florence/OpenAI), **CLIP/MobileCLIP/TorchReID**-Embeddings, **EdgeTAM**-Segmentierung, HuggingFace-Basisklassen.

**Öffentliche API**
- `VlModel`, `create()` — `vl/base.py`, `vl/create.py`, `vl/types.py`
- Konkrete VLMs — `vl/qwen.py`, `vl/moondream.py`, `vl/openai.py`, …
- `EmbeddingModel`, `CLIPModel`, … — `embedding/*`
- `EdgeTAMProcessor` — `segmentation/edge_tam.py`
- `HuggingFaceModel`, `LocalModel` — `base.py`

**Kernkonzepte**
- *Ressourcen-Lebenszyklus* — Modelle erben von `dimos.core.resource.Resource`
- *Erkennungs-Typisierung* — eng gekoppelt an `dimos.perception.detection.types`

**Abhängig von**: `dimos.core`, `dimos.msgs`, `dimos.perception`, `dimos.protocol.service`, `dimos.utils`, `dimos.types`, `dimos.agents_deprecated` (Qwen-Video-Query)

**Unterverzeichnisse**
- `vl/`, `embedding/`, `segmentation/`, `qwen/`
- `vl/README.md` dokumentiert den Vision-Language-Stack

---

## `dimos/stream/`

> **Kurzbeschreibung.** **Hilfsmedien-Pipelines** (verschieden von **`dimos.core.stream`**) — **Video-Anbieter** und **Audio-Graphen** (Mikrofon, Whisper STT, OpenAI TTS).

**Öffentliche API**
- `AbstractVideoProvider`, `VideoProvider` — `video_provider.py`
- `RTSP*`, ROS-Bild-Anbieter — `rtsp_video_provider.py`, `ros_video_provider.py`
- `FrameProcessor`, `stream_merger`, `video_operators` — Fusions-Utilities
- `stream/audio/*` — `SounddeviceAudioSource`, `WhisperNode`, `OpenAITTSNode`, Pipelines

**Kernkonzepte**
- *RX-artige Medien-Graphen* — komponierbare Audio-Knoten + Threadpool-Scheduling
- *Nicht das Module-`In`/`Out`-System* — gleichnamiges Paket; konzeptionell überlappend, aber separat

**Abhängig von**: hauptsächlich `dimos.utils` (+ `dimos.constants` in einigen Audio-Knoten)

**Unterverzeichnisse**
- `audio/` — Basis-Events, STT/TTS, Mikrofon, Pipelines

---

## `dimos/web/`

> **Kurzbeschreibung.** **HTTP + Browser-UX**: **Flask/FastAPI EdgeIO**-Server, Svelte `dimos_interface`, React **`command-center`**-Erweiterung, **Leaflet-Websocket-Karte**-Visualisierung.

**Öffentliche API**
- `RobotWebInterface` — `robot_web_interface.py` — Brücke von Teleop-Modulen zur FastAPI-UI
- `FastAPIServer` — `dimos_interface/api/server.py`
- `EdgeIO` — `edge_io.py`-Pub/Sub-Edge
- `flask_server.py`, `fastapi_server.py` — alternative Hosts
- `websocket_vis/*` — Karten-/Costmap-Websocket-Modul + README

**Kernkonzepte**
- *Edge-IO* — HTTP/SSE-Brücke zu internen Streams
- *Websocket-Visualisierung* — konsumiert `OccupancyGrid`, Pfade, GPS aus `dimos.mapping`

**Abhängig von**: `dimos.core`, `dimos.mapping`, `dimos.msgs`, `dimos.stream.audio`, `dimos.utils`

**Unterverzeichnisse**
- `dimos_interface/` — Svelte+Vite-UI + `api/` FastAPI
- `command-center-extension/` — React-Leaflet-Visualisierer
- `websocket_vis/` — Python-Modul + README
- `templates/` — HTML-Schalen

---

## `dimos/experimental/`

> **Kurzbeschreibung.** **Instabiler / nur-Demo-Code** — z. B. **`security_demo`** (YOLO + EdgeTAM + Tiefen-Stub) als optionale Blueprints kompiliert.

**Öffentliche API**
- `SecurityModule` — `experimental/security_demo/security_module.py`
- `DepthEstimator` — `depth_estimator.py`

**Kernkonzepte**
- *Demo-Pipelines* — können schwere CV-Stacks importieren; **keine stabile API**

**Abhängig von**: `dimos.agents`, `dimos.core`, `dimos.models`, `dimos.perception`

**Unterverzeichnisse**
- `security_demo/` — Modul + Estimator + Tests

---

## `dimos/porcelain/`

> **Kurzbeschreibung.** **Stabile "Ein-Objekt"-API** über **`ModuleCoordinator`**: `Dimos.run()`, `SkillsProxy`, lokale vs. **remote** Daemon-Modul-Quellen.

**Öffentliche API**
- `Dimos` — `dimos.py`
- `LocalModuleSource`, `RemoteModuleSource`, `ModuleSource`-ABC — Prozess-/Daemon-Anbindung
- `SkillsProxy` — Remote-Skill-Aufruf-Helfer

**Kernkonzepte**
- *Porcelain vs. Plumbing* — analog zu git: ergonomische Schicht über `core`

**Abhängig von**: `dimos.core`, `dimos.robot` (Registry), `dimos.core.run_registry` (indirekt über Koordinator)

**Unterverzeichnisse**
- nur Tests (`test_*.py`)

---

## `dimos/spec/`

> **Kurzbeschreibung.** **Verkabelungs-Verträge**: Basis-**`Spec`-Protocol-Marker**, Helfer (`is_spec`, Compliance-Checks) und **kleine Domänen-Marker-Module** (`spec.perception`, `spec.mapping`, …).

**Öffentliche API**
- `Spec`, `is_spec()`, `spec_structural_compliance()`, … — `spec/utils.py`
- `dimos/spec/perception.py`, `nav.py`, `mapping.py`, `control.py` — **`Module`-seitige Capability-Marker**, importiert mit dem `from dimos.spec import perception`-Muster in Hardware

**Kernkonzepte**
- *Spec vs. einfaches Protocol* — `Spec` in der MRO unterscheidet injizierbare RPC-Faces
- *Domänen-Specs* — schmale semantische Tags für Stream-Typisierung

**Abhängig von**: hauptsächlich typing/inspect + `annotation_protocol`

---

## `dimos/protocol/`

> **Kurzbeschreibung.** **Transports & Services**: Pub/Sub (**LCM**, pickled LCM, ROS, SHM, Redis, Memory), **RPC** (LCMRPC, RedisRPC), **TF**, Service-Discovery/Konfigurator, Codecs.

**Öffentliche API**
- `PubSub`, Topics, Encoder — `pubsub/spec.py`, `pubsub/impl/*`
- `LCMRPC`, `PickleLCM` — `rpc/pubsubrpc.py`, an die Pub/Sub-Schicht gebunden
- `LCMTF`, `TFSpec` — `tf/tf.py`
- `LCMService`, DDS-Spiegel — `service/lcmservice.py`, `service/ddsservice.py`
- `Configurable`, `BaseConfig` — `service/spec.py`

**Kernkonzepte**
- *Austauschbare Pub/Sub-Backends* — gleiches `PubSub`-Interface
- *Cross-Process-RPC* — Ausnahmen werden über `rpc_utils` serialisiert

**Abhängig von**: `dimos.constants`, `dimos.utils`; `pubsub` kann optionale Extras (DDS, ROS) verwenden

**Unterverzeichnisse**
- `pubsub/impl/` — lcm, ros, shm, redis, dds, jpeg, …
- `rpc/`, `service/`, `tf/`, `encode/`
- `pubsub/benchmark/` — Performance-Harness

---

## `dimos/exceptions/`

> **Kurzbeschreibung.** Fehlertypen für die **veraltete** AgentMemory-/Vektor-DB-Schicht.

**Öffentliche API**
- `AgentMemoryError`, `AgentMemoryConnectionError`, `DataNotFoundError`, … — nur `agent_memory_exceptions.py`

**Kernkonzepte**
- *Typisierte Abfrage-Fehler* — Vektor-ID fehlt, Verbindungsprobleme

**Abhängig von**: nur Standardbibliothek (in dieser Datei keine `dimos.*`-Imports)

---

## `dimos/visualization/`

> **Kurzbeschreibung.** **Rerun-Integration**: abonniert LCM-artigen Verkehr, konvertiert Nachrichten, die **`to_rerun()`** implementieren, startet Viewer.

**Öffentliche API**
- `RerunBridgeModule` — `rerun/bridge.py` (Achtung: externer `rerun.blueprint` vs. DimOS-`Blueprint`)
- `rerun_init` und Helfer — `rerun/init.py`

**Kernkonzepte**
- *Pub/Sub-Anzapfung* — Pattern-Matching auf Topics
- *Optionale grpc/web-Viewer-Ports* — als Konstanten am Modul-Anfang exportiert

**Abhängig von**: `dimos.core`, `dimos.protocol.pubsub`, `dimos.utils`

**Unterverzeichnisse**
- `rerun/` — Brücke + Tests + `init.py`

---

## `dimos/utils/`

> **Kurzbeschreibung.** **Geteilte Infrastruktur**: Logging-Setup, **Daten**-Pfad-Helfer (`get_data`), Mathematik (`Vector`, Transformationen), **Rx-Helfer**, CLI-Diagnose (**`lcmspy`**, **`dtop`**, agentspy, foxglove-bridge launcher), Replay-/Moment-Test-Utilities.

**Öffentliche API** (repräsentativ)
- `setup_logger`, run-spezifische Log-Verzeichnisse — `logging_config.py`
- `get_data`, `get_data_dir` — `data.py`
- `backpressure`-Rx-Utilities — `reactive.py`
- CLI-Eintrittsmodule — `cli/lcmspy/lcmspy.py`, `cli/dtop.py`, `cli/agentspy/agentspy.py`, …
- `transform_utils`, `trigonometry`, `path_utils`, `urdf.py`, …

**Kernkonzepte**
- *Test-Replay* — `utils/testing/replay.py`, `moment.py` für deterministische Sensoren
- *Operative CLI* — Introspektion ohne Start eines vollständigen `dimos`-Stacks

**Abhängig von**: referenziert weit gefächert `dimos.msgs`, `dimos.core`, `dimos.memory*`, in Test-Helfern auch `dimos.robot`, `dimos.protocol`, `dimos.types`

**Unterverzeichnisse**
- `cli/` — Operator-Befehle
- `testing/` — Replay-/Moment-Fixtures
- `decorators/`, `docs/` — Querschnittliche Helfer

---

## `dimos/types/`

> **Kurzbeschreibung.** **Leichte gemeinsame Typen**, die nicht volle **`msgs`** sind: numpy-**`Vector`**, Zeitstempel-Helfer, Roboter-Capabilities, Weak-Container.

**Öffentliche API**
- `Vector` — `vector.py`
- `Timestamped`, `to_timestamp` — `timestamped.py`
- `WeakList` — `weaklist.py`
- `Sample` — `sample.py`
- `RobotLocation` — `robot_location.py`
- `Vector3`-Polyfill — `ros_polyfill.py`
- `RobotCapability` — `robot_capabilities.py` (verwendet von `dimos.robot.robot.Robot`)
- `Colors` — Re-Export-Bereich in `types/constants` falls vorhanden

**Kernkonzepte**
- *Nicht-ROS-Geometrie-Helfer* — Vermeidet schweres `geometry_msgs` für reine Python-Mathematik
- *Räumliche-Memory-Identität* — `RobotLocation` verbindet Name ↔ Pose-Metadaten

**Abhängig von**: hauptsächlich `numpy`; optional `dimos.msgs` in einigen Konvertierungen

---

## `dimos/msgs/`

> **Kurzbeschreibung.** **Typisierte Nachrichtenmodelle**, die ROS-1-Formen widerspiegeln (geometry, sensor, nav, trajectory, vision_msgs) plus **`DimosMsg`**, Foxglove-Overlays und umfangreiche Roundtrip-Tests.

**Öffentliche API**
- Pro-Topic-Klassen — z. B. `sensor_msgs/Image.py`, `geometry_msgs/PoseStamped.py`, `nav_msgs/OccupancyGrid.py`, …
- `DimosMsg`-Protocol — `msgs/protocol.py`
- gemeinsame Helfer — `msgs/helpers.py`

**Kernkonzepte**
- *Serialisierungs-freundliche dataclass-/pydantic-artige Muster* — verwendet über LCM-/ROS-Brücken
- *Bild-Barrier* — `sharpness_barrier`-Hooks in `Image.py` für Load-Shedding

**Abhängig von**: interne Selbstreferenzen unter `msgs/*` (minimal `dimos.*` ansonsten)

**Unterverzeichnisse**
- `geometry_msgs/`, `sensor_msgs/`, `nav_msgs/`, `std_msgs/`, `trajectory_msgs/`, `vision_msgs/`, `tf2_msgs/`, `foxglove_msgs/`, …

---

## `dimos/project/`

> **Kurzbeschreibung.** **Meta-Tests**, die Repository-Konventionen schützen — **kein** Laufzeit-"Versions"-Modul (für Versions-Metadaten **`pyproject.toml`** / Packaging verwenden).

**Öffentliche API**
- `test_get_logger.py`, `test_no_init_files.py`, … — erzwingen Logging- und Layout-Richtlinien

**Kernkonzepte**
- *CI-Hygiene* — statische Scans über den Baum

**Abhängig von**: `dimos.constants` (Pfad-Roots)

---

## `dimos/rxpy_backpressure/`

> **Kurzbeschreibung.** Kleines **RxPy-Operator-Kit** für **Latest / Drop / Buffer**-Strategien auf Observers (`BackPressure`-Namespace).

**Öffentliche API**
- `BackPressure` — `backpressure.py` — referenziert `drop.py`, `latest.py`-Module
- `wrap_observer_with_*` — einzelne Strategie-Helfer

**Kernkonzepte**
- *Observer-seitige Flusskontrolle* — Ergänzung zu `dimos.utils.reactive`

**Abhängig von**: nur paketintern (re-importiert benachbarte Dateien unter `rxpy_backpressure/`)

---

## So liest man dieses Dokument beim Navigieren des Codes

1. **Auf der Suche nach einem Verhalten?** Beginnen Sie in `agents/skills/` → finden Sie heraus, welches `Module` die `@skill` bereitstellt → gehen Sie in die unterstützende Schicht (z. B. `navigation`, `mapping`, `manipulation`).
2. **Auf der Suche nach einem I/O-Vertrag?** Beginnen Sie in `spec/` (Capability) und `msgs/` (Payload), dann `protocol/` für den Transport.
3. **Auf der Suche nach der Laufzeit?** `core/` ist der Kernel; `porcelain/` ist die ergonomische Schicht; `robot/cli/` ist der Benutzer-Eintrittspunkt.
4. **Typische Sensor → Aktion-Kette:** `hardware/sensors` → `perception` → `memory2` (+ `mapping`) → `agents` (Planung) → `skills` / `navigation` / `manipulation` → `control` → `hardware/manipulators|drive_trains`.
5. **Als Legacy behandeln & in neuem Code vermeiden:** `agents_deprecated/`, `skills/` (Pydantic-Geschmack), `memory/`, `exceptions/`.
