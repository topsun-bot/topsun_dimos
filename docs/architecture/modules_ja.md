# DimOS モジュールアーキテクチャ(日本語版)

> `dimos/` 配下の 28 個のトップレベルモジュールのセマンティックマップ。`dev` ブランチに対する import グラフとソースコード解析から自動生成。**公開インターフェース**(public surface)のリストは、ソースを掘らずに依存できる最小の安定契約として扱ってください。
>
> *英語の原文は:[`modules.md`](/docs/architecture/modules.md)、中国語訳は:[`modules_cn.md`](/docs/architecture/modules_cn.md)*

`dimos/__init__.py` は `dimos.porcelain.dimos` から **`Dimos`** だけを遅延ロードします。ほとんどのサブパッケージは `__init__.py` での **export 整理を行っていません**(名前空間スタイルの構成)— 明示的な re-export が追加されない限り、**具体的なモジュール**を公開 API として扱ってください。

---

## モジュール一覧表

| モジュール | レイヤ | 役割(一行) |
|--------|-------|-----------------|
| **agents** | 頭脳 | LLM ツールキット:`@skill`、MCP クライアント/サーバー、オプションの VL agent、ナビ/音声などのスキルコンテナ |
| **agents_deprecated** | レガシー | 旧 `OpenAIAgent` / トークナイザ / **chromadb** メモリスタック。古いデモで使用 |
| **skills** | 頭脳(レガシー) | 旧式の **Pydantic `AbstractSkill` + `SkillLibrary`** ツールスキーマ(`@skill` RPC と並列) |
| **control** | アクチュエーション | **100 Hz `ControlCoordinator`**、タスク、tick ループ、アーム関節の調停 |
| **core** | ランタイム | **モジュール、ストリーム、トランスポート、ブループリント、コーディネーター、RPC、ワーカー、グローバル設定** |
| **memory** | データ(レガシー) | embedding + **時系列**ストア(`EmbeddingMemory`、SQLite/Postgres バックエンド) |
| **memory2** | データ | **ストリームベースのメモリ**:Observation、ストア、コーデック、`StreamModule` 統合 |
| **perception** | センシング | 検出、追跡、空間メモリ、実験的な時系列メモリ |
| **manipulation** | 運動 | ピック&プレース、計画(Drake/MJCF)、`control/` 配下の軌道/制御ヘルパー |
| **mapping** | 空間 | 占有グリッド、ボクセル、OSM/Google Maps ヘルパー、**`LatLon`** モデル |
| **navigation** | 運動 | ROS ナビゲーションブリッジ、巡回、**ビジュアルサーボ**、フロンティア/探索 |
| **hardware** | ドライバ | カメラ、LiDAR、マニピュレータ、ドライブトレイン、ホールボディアダプター |
| **robot** | プロダクト | **`dimos run` CLI**、ブループリントレジストリ、Unitree/ドローン/xArm スタック |
| **simulation** | シミュレーション | MuJoCo SHM、Genesis/Isaac/Unity スタブ、**エンジンレジストリ** |
| **teleop** | 入力 | Quest / スマホ / キーボードによるテレオペモジュールとブループリント |
| **models** | ML | **VLM**、CLIP/MobileCLIP embedding、セグメンテーション(EdgeTAM)、HuggingFace ラッパー |
| **stream** | メディア | **ビデオ**(Webcam/RTSP/ROS)と **オーディオ**(マイク、Whisper、TTS)パイプライン |
| **web** | UI | Flask/FastAPI **EdgeIO**、Svelte `dimos_interface`、地図 **websocket_vis** |
| **experimental** | wip | **不安定なデモ**(例:`security_demo` パイプライン) |
| **porcelain** | API | **`Dimos` ファサード**:ローカル/リモートのモジュールソース、**`SkillsProxy`** |
| **spec** | 型付け | **`Spec` マーカープロトコル** + ドメイン別 `spec.perception` / `nav` / `mapping` / `control` |
| **protocol** | トランスポート | **LCM/ROS/SHM/DDS/Redis** パブサブ、**LCMRPC**、TF、サービスコンフィギュレータ |
| **exceptions** | レガシー | **エージェントメモリ専用**の例外階層(レガシー memory パス) |
| **visualization** | 運用 | **Rerun ブリッジ** + 初期化ヘルパー |
| **utils** | 基盤 | ロギング、データパス、CLI ツール(`lcmspy`、`dtop`、replay、foxglove…) |
| **types** | 型付け | **`Vector`**、`Timestamped`、**`RobotLocation`**、weak list、ROS polyfill |
| **msgs** | 型付け | **ROS 風の型付きメッセージ** + `DimosMsg` プロトコル |
| **project** | CI | **リポジトリ衛生テスト**(例:logging `getLogger` ガード);バージョン API ではない |
| **rxpy_backpressure** | 基盤 | **`BackPressure`** ファサード(Latest/Drop/Buffer の observer 戦略) |

### 横断的な共通理解(一度読めば OK)

- **2 つの並列スキルシステム**:`agents/` の `@skill` + MCP 経路 vs `skills/` の Pydantic `AbstractSkill` 経路。**新コードは前者を優先**。
- **`stream/` ≠ `core.stream`**:`dimos/stream/` は*メディアパイプライン*(ビデオ + オーディオ);`dimos.core.stream` は*モジュール I/O* プリミティブ(`In`/`Out`/`Transport`)。**名前が衝突**しています。
- **`memory/` はレガシー**、`memory2/` が現在メンテナンスされている観測/ストアパイプライン。
- **`project/`** は *CI 衛生テスト* の集まりで、**バージョン/メタデータモジュールではない** — バージョン番号は `pyproject.toml` から取得。
- **`exceptions/`** はレガシー memory 系のエラー型のみを格納。**プロジェクト全体のエラー名前空間ではない**。

---

## トップレベル `dimos` パッケージ

> **概要。** 遅延エントリポイント:`dimos/__init__.py` は `Dimos` のみを公開し、それ以外はサブパッケージから明示的にインポートする必要があります。

**公開インターフェース**
- `Dimos`(`dimos.porcelain.dimos`)— ブループリントの構築/実行、オプションのリモート daemon 接続

**主要概念**
- *遅延パッケージ属性* — `__getattr__` が初回アクセス時に `Dimos` をロード

**依存**:(ロード時)`dimos.porcelain`、`dimos.core`、`dimos.robot`…

---

## `dimos/agents/`

> **概要。** Agent *フレームワーク*:LLM **MCP** スタック(`McpClient` / `McpServer`)、**`@skill`** デコレータ、VL agent モジュール、ロボット非依存の**スキルコンテナ**(ナビゲーション、GPS/地図、音声…)。

**公開インターフェース**(パッケージ `__init__.py` なし、典型的な import)
- `skill` / `current_skill_context()` — `dimos/agents/annotation.py` — RPC メソッドを LLM/MCP ツールとしてマーク;オプションで MCP 進捗コンテキスト
- `McpClient`、`McpClientConfig` — LangChain agent + HTTP MCP ツール + LCM ストリーム
- `McpServer` — モジュールの `@skill` を公開する FastAPI JSON-RPC MCP サーバー
- `McpAdapter` — MCP 統合テスト用のアダプタ
- `AgentSpec` — 「agent 命令を受け取れる」プロトコル
- `VLMAgent`、`VLMAgentConfig` — 画像 I/O + VLM 指向のモジュール
- `AgentTestRunner` — agent スタック用テストハーネス
- `SYSTEM_PROMPT` — `dimos/agents/system_prompt.py`、デフォルトの prompt テキスト
- `ensure_ollama_model()`、`ollama_installed()` — `dimos/agents/ollama_agent.py` の Ollama ヘルパー(完全な `Agent` クラスではない)

**主要概念**
- *`@skill`* — `@rpc` + `__skill__`;MCP ツールスキーマを駆動
- *MCP HTTP ブリッジ* — FastAPI 経由の外部ツールプロトコル(デフォルトポートは `GlobalConfig` から)
- *スキルコンテナ* — 1 つのシナリオに複数の `@skill` を一度に公開する `Module` サブクラス

**依存**:`dimos.core`、`dimos.spec`、`dimos.msgs`、`dimos.utils`、よく使われる `dimos.navigation`、`dimos.mapping`、`dimos.models`、`dimos.perception`、`dimos.stream`、`dimos.robot`(テスト/デモ)、`dimos.web`、`dimos.constants`

**サブディレクトリ**
- `mcp/` — `mcp_server.py`、`mcp_client.py`、`mcp_adapter.py`、`tool_stream.py`
- `skills/` — 具体的なスキル `Module`(ナビ、音声、OSM、GPS、Google Maps、人物追跡、デモ)

---

## `dimos/agents_deprecated/`

> **概要。** **廃止された** LangChain スタイルの **`OpenAIAgent`** スタック。**Chroma/OpenAI セマンティックメモリ**、トークナイザヘルパー、prompt ビルダーを含む — 古いスクリプトと `dimos/models/qwen/video_query.py` のためだけに残されています。

**公開インターフェース**
- `OpenAIAgent` および関連クラス — `dimos/agents_deprecated/agent.py`(大型のレガシーモジュール)
- `OpenAISemanticMemory` — `memory/chroma_impl.py`
- `PromptBuilder` — `prompt_builder/impl.py`

**主要概念**
- *レガシー agent ループ* — `McpClient` 以前の RxPy + ツール呼び出し
- *Chroma メモリ* — ベクトル DB ベースの "AgentMemory"(`dimos.exceptions` を使用)

**依存**:`dimos.skills`、`dimos.stream`、`dimos.core`(memory モジュール経由)、`dimos.exceptions`…

**サブディレクトリ**
- `memory/` — Chroma + 空間/視覚記憶コンポーネント
- `prompt_builder/`、`tokenizer/` — レガシー prompt/トークンユーティリティ

---

## `dimos/skills/`

> **概要。** **レガシーな Pydantic スキルライブラリ**(`AbstractSkill`、`SkillLibrary`)。OpenAI スタイルの**関数ツール**を生成 — `@skill` と概念的に重複するが、配線方法が異なる。

**公開インターフェース**
- `SkillLibrary` — `AbstractSkill` サブクラスの収集/実行、JSON ツールエクスポート
- `AbstractSkill`、`AbstractRobotSkill` — Pydantic ベースのツールモデル
- `kill_skill` および manipulation ラッパー — `skills/kill_skill.py`、`skills/manipulation/*`、`visual_navigation_skills.py`、`rest/rest.py` を参照

**主要概念**
- *クラスベーススキル* — サブクラスを発見してツールとして公開
- *Robot スキル* — オプションで `dimos.robot.robot.Robot` ハンドルを保持

**依存**:`dimos.types`、`dimos.utils`

**サブディレクトリ**
- `manipulation/` — 制約 / ピック&プレース風スキル
- `rest/` — REST 指向のスキルヘルパー
- `unitree/` — 例:speak ラッパー

---

## `dimos/control/`

> **概要。** **低レベルマルチアーム制御**:**`ControlCoordinator`** + **100 Hz `TickLoop`**、合成可能な**タスク**(テレオペ、サーボ、軌道、速度)、優先度ベースの調停。

**公開インターフェース**
- `ControlCoordinator`、`ControlCoordinatorConfig` — `coordinator.py` 中央コーディネーター `Module`
- `TickLoop` — 決定論的な周期ループ
- `Task`、`ControlTask` 階層 — `task.py`、`tasks/*`
- `ConnectedHardware`、`HardwareComponent` — `hardware_interface.py`、`components.py`
- `control/blueprints/*` — 既製のブループリント断片(basic、dual、mobile、teleop)

**主要概念**
- *タスク調停* — 複数ライター → 関節コマンドの仲裁
- *マニピュレータアダプタ* — `dimos.hardware.manipulators.spec` との統合

**依存**:`dimos.constants`、`dimos.msgs`、`dimos.hardware.manipulators`、`dimos.manipulation`(IK タスク)、`dimos.utils`

**サブディレクトリ**
- `tasks/` — `velocity_task`、`trajectory_task`、`teleop_task`、`servo_task`、`cartesian_ik_task`
- `blueprints/` — ブループリントプリセット
- `examples/` — キーボードテレオペ、IK jogger デモ

---

## `dimos/core/`

> **概要。** **ランタイムカーネル**:**`Module`**、**`In`/`Out`** ストリーム、**トランスポート**、**`Blueprint`/`autoconnect`**、**`ModuleCoordinator`**、ワーカー、RPC、実行レジストリ、Docker/native モジュール、内省ツール。

**公開インターフェース**
- `Module`、`ModuleBase`、`ModuleConfig`、`SkillInfo`… — `module.py`
- `In`、`Out`、`Transport`、`Stream` — `stream.py`(stream 層;**別パッケージの** `dimos/stream` **ではない**)
- `LCMTransport`、`pLCMTransport`、`ROSTransport`… — `transport.py`
- `rpc` / `T` — `core.py` のデコレータ/型ヘルパー
- `Blueprint`、`BlueprintAtom`、`autoconnect()` — `coordination/blueprints.py`
- `ModuleCoordinator` — `coordination/module_coordinator.py`
- `GlobalConfig`、`global_config` — `global_config.py`
- `WorkerManagerPython`、Docker workers、`PythonWorker` — `coordination/*`
- `RunEntry`、実行レジストリヘルパー — `run_registry.py`
- `RPCClient`、`RpcCall` — `rpc_client.py`

**主要概念**
- *ストリーム配線* — ブループリントが `(name, type)` で `In`/`Out` をマッチング
- *Forkserver workers* — モジュールのプロセス分離
- *LCM 経由の RPC* — `LCMRPC`、`dimos.protocol.rpc.pubsubrpc` 経由

**依存**:`dimos.spec`、`dimos.protocol.*`、`dimos.utils`、`dimos.msgs`(トランスポート経由で間接的);`dimos.models.vl.types`(global config の VL name enum);テストでは `dimos.agents`、`dimos.robot.cli` を import

**サブディレクトリ**
- `coordination/` — ブループリント、コーディネーター、ワーカーマネージャ、プロセスライフサイクル、rpyc、daemon フック
- `introspection/` — モジュール/ブループリントグラフ、ANSI/dot レンダリング
- `resource_monitor/` — リソース統計/ロギング
- `tests/`、`test_*.py` — 大量のユニット/E2E テスト

---

## `dimos/memory/`

> **概要。** **レガシー**:embedding メモリ + **汎用時系列**バックエンド(SQLite/Postgres/pickle/in-memory)。

**公開インターフェース**
- `EmbeddingMemory`、`Config` — `embedding.py` — Rx 上の CLIP 画像パス(costmap フックは stub/実験的)
- `timeseries/*` — `base.py` + `sqlite.py`、`postgres.py`、`pickledir.py`、`inmemory.py`、`legacy.py`

**主要概念**
- *空間 embedding* — 画像 + costmap 融合のアイデア(コードでは "would be cool" と注記されている)
- *時系列ストア* — センサータイムライン用の再利用可能な永続化層

**依存**:`dimos.core`、`dimos.models.embedding`、`dimos.msgs`、`dimos.utils.reactive`

**サブディレクトリ**
- `timeseries/` — 各ストア実装 + テスト

---

## `dimos/memory2/`

> **概要。** **現在のメモリサブシステム**:型付き **Observation**、**変換ストリーム**、プラグイン可能な **blob/ベクトル/observation ストア**、コーデック、`dimos.core` I/O を橋渡しする **`StreamModule`**。

**公開インターフェース**
- `Stream` — `stream.py` — 変換、バックプレッシャ、クエリ
- `StreamModule`、`port_to_stream()`、`stream_to_port()` — `module.py`
- `Backend` — `backend.py` — Observation + Blob + Vector + notifier の複合
- `EmbedImages` — `embed.py`
- `registry.py`、`buffer.py`、`transform.py`、`type/observation.py`、`store/sqlite.py`、`blobstore/*`、`vectorstore/*`、`codecs/*`、`vis/*`

**主要概念**
- *Observation パイプライン* — append → transform → embed → persist → notify
- *コーデックプラグイン* — pickle、LCM、lz4、jpeg など

**依存**:`dimos.core`、`dimos.models.embedding`、`dimos.msgs`、`dimos.utils`、`dimos.agents.annotation`(`module.py` 内のスキルヘルパー)、`dimos.constants`

**サブディレクトリ**
- `store/`、`blobstore/`、`observationstore/`、`vectorstore/` — 各ストアバックエンド
- `codecs/` — blob/payload シリアライズ
- `type/` — `Observation`、フィルタ、クエリ型
- `notifier/` — リアクティブ通知
- `utils/`、`vis/` — 検証、プロット/rerun/svg ヘルパー

---

## `dimos/perception/`

> **概要。** **理解層**:2D/3D 検出/追跡、**空間メモリ**、VLM フック、加えて実験的な**時系列**グラフメモリ。

**公開インターフェース**
- `ObjectTrackingSpec`、`SpatialMemorySpec` — `object_tracking_spec.py`、`spatial_memory_spec.py`
- `SpatialMemory` モジュール — `spatial_perception.py`(まだ **`agents_deprecated`** の visual memory を import)
- `PerceiveLoopSkill` 風コンポーネント — `perceive_loop_skill.py`
- `detection/` — YOLO/SAM/EdgeTAM をラップした検出器とメッセージ型
- `experimental/temporal_memory/` — 時系列グラフ / スライディングウィンドウ動画解析(README あり)

**主要概念**
- *知覚ポートの spec* — `dimos.spec.perception`
- *空間メモリ* — 名前付き `RobotLocation`、`dimos.types.robot_location` 経由

**依存**:`dimos.core`、`dimos.msgs`、`dimos.spec`、`dimos.models`、`dimos.types`、`dimos.agents`、`dimos.agents_deprecated`(レガシーパス)、`dimos.stream`、`dimos.robot`(テスト)、`dimos.utils`

**サブディレクトリ**
- `detection/` — 検出器 + 2D/3D メッセージ型
- `experimental/temporal_memory/` — 進行中の時系列推論スタック

---

## `dimos/manipulation/`

> **概要。** **マニピュレータと運動**:ピック&プレースモジュール、**Drake** ワールドモデル + 軌道生成、Pinocchio IK、**ネストされた `manipulation/control/`**(サーボ、軌道コントローラ、コーディネーター**クライアント**)。

**公開インターフェース**
- `ManipulationModule` / pick-and-place — `manipulation_module.py`、`pick_and_place_module.py`
- `planning/` — Drake `World`、軌道生成器、URDF/mesh ユーティリティ、`planning/spec/*` の設定
- `control/` — `cartesian_motion_controller.py`、`joint_trajectory_controller.py`、`coordinator_client.py`、`arm_driver_spec.py`(`dimos.control` コーディネーターパターンと連携)

**主要概念**
- *WorldSpec / JointTrajectory* — 計画器間の交換形式
- *サーボ vs 軌道* — 異なる control task のバインディング

**依存**:`dimos.core`、`dimos.msgs`、`dimos.perception`(3D 物体型)、`dimos.utils`

**サブディレクトリ**
- `planning/` — ワールド、軌道、運動学、spec(`planning/` に README)
- `control/` — 低レベルアーム運動コントローラ。トップレベルの `dimos/control` コーディネーターと**並列**に位置

---

## `dimos/mapping/`

> **概要。** **地図と地理コンテキスト**:点群からの占有グリッド、**ボクセルパイプライン**、OSM "現在地"、Google Maps 統合、**`LatLon`** モデル。

**公開インターフェース**
- `LatLon` および関連 — `models.py`、`google_maps/*`、`osm/*`
- `VoxelGrid` / `VoxelGridMapper` — `voxels.py`(**`memory2.StreamModule`** を使用)
- `pointclouds/occupancy.py` — グリッド生成ヘルパー
- `occupancy/` — 膨張、勾配(websocket 可視化で使用)

**主要概念**
- *占有融合* — `PointCloud2` / ロボット中心グリッドから
- *地理オーバーレイ* — オプションの地図 + VL クエリ

**依存**:`dimos.core`、`dimos.memory2`、`dimos.msgs`、`dimos.utils`、`dimos.memory`(テストでのレガシー replay)

**サブディレクトリ**
- `pointclouds/`、`occupancy/`、`voxels.py` — マッピングコア
- `osm/`、`google_maps/` — 外部地図プロバイダ + モデル

---

## `dimos/navigation/`

> **概要。** **運動計画と運動実行**:ROS ナビゲーションブリッジ、巡回、フロンティア探索、**ビジュアルサーボ**ヘルパー、ジョイスティック/テレオペ隣接ルーティング。

**公開インターフェース**
- `NavigationState`、`NavigationInterface` — `base.py`
- `NavigationInterfaceSpec` — `navigation_spec.py`
- `ROSNavModule` — `rosnav.py`(スキル + ROS/LCM I/O)
- `visual_servoing/` — 2D サーボ + 検出ナビゲーションユーティリティ
- `visual/query.py` — VLM bbox ヘルパー
- `patrolling/`、`exploration/` — 高次行動
- `global_planner/`、`motion_planning/` — 計画スタック

**主要概念**
- *Navigator spec の注入* — "あの場所へ" を表す型付き RPC spec
- *ビジュアルサーボブリッジ* — 知覚 + 差動駆動 / TF

**依存**:`dimos.core`、`dimos.msgs`、`dimos.perception`、`dimos.models`、`dimos.protocol.tf`、`dimos.utils`、`dimos.agents`(rosnav の `@skill`)

**サブディレクトリ**
- `visual_servoing/`、`visual/`、`patrolling/`、`exploration/`、`global_planner/`、`motion_planning/`、`tests/`…

---

## `dimos/hardware/`

> **概要。** **デバイスドライバとアダプタ**:カメラ(ZED、RealSense、GStreamer、Webcam)、**Livox/FAST-LIO2 LiDAR**、マニピュレータ、ドライブトレイン、ホールボディバス。

**公開インターフェース**
- `Camera` 関連モジュール — `sensors/camera/*`(+ `spec.py` 設定)
- LiDAR `Mid360`、`FastLio2` の **NativeModule** ラッパー — `sensors/lidar/*`
- `ManipulatorAdapter` / フィールドバス — `manipulators/`、`drive_trains/`、`whole_body/`
- `fake_zed_module.py` — replay 向けカメラソース

**主要概念**
- *知覚 spec* — `import dimos.spec.perception as …` でカメラストリームをマーク
- *Native モジュール* — C++/SHM ベースのパブリッシャ、`NativeModule` 基底

**依存**:`dimos.core`、`dimos.msgs`、`dimos.spec`、`dimos.protocol.tf`、`dimos.mapping`、`dimos.visualization`、`dimos.robot`、`dimos.memory`(一部モジュールでレガシーストアを使用)

**サブディレクトリ**
- `sensors/camera/`、`sensors/lidar/` — ビジョン + LiDAR スタック(一部に **C++ README**)
- `manipulators/`、`drive_trains/`、`whole_body/` — アクチュエーション側

---

## `dimos/robot/`

> **概要。** **コードでのロボットプロダクト**:**Typer/Click CLI**(`dimos run`、`mcp`、`log`…)、**自動生成のブループリントレジストリ**、Unitree Go2/G1/B1、ドローン MAVLink/DJI、xArm/piper/openarm ブループリント。

**公開インターフェース**
- `main` / CLI — `cli/dimos.py`
- `get_by_name`、`class_name_to_registry_key` — `get_all_blueprints.py`
- `all_blueprints` — 生成されたリスト(`all_blueprints.py`)
- `Robot` 抽象基底クラス — `robot.py`(レガシーの最小インターフェース)
- `FoxgloveBridge` — `foxglove_bridge.py`
- 大型サブツリー:`unitree/go2|g1|b1/`、`drone/`、`manipulators/*`

**主要概念**
- *ブループリントレジストリ* — 文字列 → 実行可能スタック
- *ロボット固有型* — 例:`unitree/type/lidar.py`、`odometry.py`

**依存**:ほぼすべて(`dimos.agents`、`dimos.core`、`dimos.hardware`、`dimos.simulation`、`dimos.navigation`、`dimos.mapping`、`dimos.msgs`…)

**サブディレクトリ**
- `cli/` — ユーザー向けの **`dimos`** コマンド
- `unitree/` — Go2/G1/B1 接続 + ブループリント
- `drone/` — MAVLink、DJI ビデオ、追跡
- `manipulators/` — xArm、Piper、OpenArm ブループリント断片
- `unitree_webrtc/` — WebRTC 型 shim、`unitree/type` を re-export

---

## `dimos/simulation/`

> **概要。** **物理バックエンドとグルー**:MuJoCo 共有メモリ + policy ループ、オプションの Genesis/Isaac/Unity ストリーム、**`SimulationEngine` レジストリ**(現在のデフォルトは **`mujoco`**)。

**公開インターフェース**
- `get_engine()` — `engines/registry.py`
- `MujocoEngine`、`MujocoSimModule` — `engines/mujoco_engine.py`、`mujoco_shm.py`
- `simulation/mujoco/*` — SHM ライター、深度カメラ、explorer スクリプト
- `simulation/base/*` — 抽象 simulator/stream 基底クラス

**主要概念**
- *エンジン選択* — 文字列 → シミュレーションバックエンドクラス
- *SHM ブリッジ* — `dimos.robot.unitree.mujoco_connection` へ

**依存**:`dimos.msgs`、`dimos.core`(モジュール経由)、`dimos.utils.data`(典型的)、`dimos.robot`(統合)

**サブディレクトリ**
- `engines/` — レジストリ + MuJoCo 統合
- `mujoco/` — プロセス + SHM + カメラヘルパー
- `genesis/`、`isaac/`、`unity/` — 代替シミュレーション統合
- `utils/` — MJCF/XML ヘルパー

---

## `dimos/teleop/`

> **概要。** **人間在中ループ入力**:Meta Quest WebXR、スマホブラウザ UI、キーボード jogger;`RobotWebInterface` と **control** ブループリントへ接続。

**公開インターフェース**
- `QuestTeleopModule`、`ArmTeleopModule`、`quest_extensions` — VR ポーズ / joy
- `PhoneTeleopModule` — タッチ twist テレオペ
- `keyboard/keyboard_teleop_module.py` — `dimos.control.examples` の IK jogger を参照
- `quest/blueprints.py`、`phone/blueprints.py` — `autoconnect` コンポジション

**主要概念**
- *WebXR → ロボット座標系* — `teleop/utils/teleop_transforms.py`
- *Control 統合* — `dimos.control.blueprints.teleop` を import

**依存**:`dimos.core`、`dimos.msgs`、`dimos.web`、`dimos.utils`、`dimos.robot`、`dimos.control`、`dimos.visualization`

**サブディレクトリ**
- `quest/`、`phone/`、`keyboard/` — デバイス固有モジュール + quest/phone の README
- `utils/` — 共通変換

---

## `dimos/models/`

> **概要。** **ML モデルアダプタ**:**Vision-Language モデル**(Qwen/Moondream/Florence/OpenAI)、**CLIP/MobileCLIP/TorchReID** embedding、**EdgeTAM** セグメンテーション、HuggingFace 基底クラス。

**公開インターフェース**
- `VlModel`、`create()` — `vl/base.py`、`vl/create.py`、`vl/types.py`
- 具体的な VLM — `vl/qwen.py`、`vl/moondream.py`、`vl/openai.py`…
- `EmbeddingModel`、`CLIPModel`… — `embedding/*`
- `EdgeTAMProcessor` — `segmentation/edge_tam.py`
- `HuggingFaceModel`、`LocalModel` — `base.py`

**主要概念**
- *リソースライフサイクル* — モデルが `dimos.core.resource.Resource` を継承
- *検出型付け* — `dimos.perception.detection.types` と密接に結合

**依存**:`dimos.core`、`dimos.msgs`、`dimos.perception`、`dimos.protocol.service`、`dimos.utils`、`dimos.types`、`dimos.agents_deprecated`(Qwen 動画クエリ)

**サブディレクトリ**
- `vl/`、`embedding/`、`segmentation/`、`qwen/`
- `vl/README.md` が Vision-Language スタックを文書化

---

## `dimos/stream/`

> **概要。** **補助メディアパイプライン**(**`dimos.core.stream`** とは別物)— **ビデオプロバイダ**と**オーディオグラフ**(マイク、Whisper STT、OpenAI TTS)。

**公開インターフェース**
- `AbstractVideoProvider`、`VideoProvider` — `video_provider.py`
- `RTSP*`、ROS 画像プロバイダ — `rtsp_video_provider.py`、`ros_video_provider.py`
- `FrameProcessor`、`stream_merger`、`video_operators` — 融合ユーティリティ
- `stream/audio/*` — `SounddeviceAudioSource`、`WhisperNode`、`OpenAITTSNode`、各パイプライン

**主要概念**
- *Rx 風メディアグラフ* — 合成可能な音声ノード + スレッドプールスケジューリング
- *Module `In`/`Out` システムではない* — 同名パッケージ;概念は重なるが別物

**依存**:主に `dimos.utils`(一部の音声ノードで `dimos.constants`)

**サブディレクトリ**
- `audio/` — 基本イベント、STT/TTS、マイク、パイプライン

---

## `dimos/web/`

> **概要。** **HTTP + ブラウザ UX**:**Flask/FastAPI EdgeIO** サーバー、Svelte `dimos_interface`、React **command-center** 拡張、**Leaflet websocket 地図**可視化。

**公開インターフェース**
- `RobotWebInterface` — `robot_web_interface.py` — テレオペモジュールを FastAPI UI に橋渡し
- `FastAPIServer` — `dimos_interface/api/server.py`
- `EdgeIO` — `edge_io.py` の pub/sub edge
- `flask_server.py`、`fastapi_server.py` — 代替ホスト
- `websocket_vis/*` — 地図/costmap websocket モジュール + README

**主要概念**
- *Edge IO* — 内部ストリームへの HTTP/SSE ブリッジ
- *Websocket 可視化* — `dimos.mapping` の `OccupancyGrid`、パス、GPS を消費

**依存**:`dimos.core`、`dimos.mapping`、`dimos.msgs`、`dimos.stream.audio`、`dimos.utils`

**サブディレクトリ**
- `dimos_interface/` — Svelte+Vite UI + `api/` FastAPI
- `command-center-extension/` — React Leaflet ビジュアライザ
- `websocket_vis/` — Python モジュール + README
- `templates/` — HTML スケルトン

---

## `dimos/experimental/`

> **概要。** **不安定 / デモ専用コード** — 例:**`security_demo`**(YOLO + EdgeTAM + 深度スタブ)。オプションのブループリントとしてコンパイル。

**公開インターフェース**
- `SecurityModule` — `experimental/security_demo/security_module.py`
- `DepthEstimator` — `depth_estimator.py`

**主要概念**
- *デモパイプライン* — 重い CV スタックを import する可能性あり;**安定 API ではない**

**依存**:`dimos.agents`、`dimos.core`、`dimos.models`、`dimos.perception`

**サブディレクトリ**
- `security_demo/` — モジュール + estimator + テスト

---

## `dimos/porcelain/`

> **概要。** **`ModuleCoordinator`** の上に乗る**安定した「単一オブジェクト」API**:`Dimos.run()`、`SkillsProxy`、ローカル vs **リモート** daemon モジュールソース。

**公開インターフェース**
- `Dimos` — `dimos.py`
- `LocalModuleSource`、`RemoteModuleSource`、`ModuleSource` 抽象基底クラス — プロセス/daemon アタッチ
- `SkillsProxy` — リモートスキル呼び出しヘルパー

**主要概念**
- *Porcelain vs plumbing* — git の比喩:`core` の上に乗る人間工学的レイヤ

**依存**:`dimos.core`、`dimos.robot`(レジストリ)、`dimos.core.run_registry`(コーディネーター経由で間接的)

**サブディレクトリ**
- テストのみ(`test_*.py`)

---

## `dimos/spec/`

> **概要。** **配線契約**:基本の **`Spec` Protocol マーカー**、ヘルパー(`is_spec`、互換性チェック)、**小さなドメインマーカーモジュール**(`spec.perception`、`spec.mapping`…)。

**公開インターフェース**
- `Spec`、`is_spec()`、`spec_structural_compliance()`… — `spec/utils.py`
- `dimos/spec/perception.py`、`nav.py`、`mapping.py`、`control.py` — **`Module` 側の能力マーカー**。`from dimos.spec import perception` パターンで hardware から import

**主要概念**
- *Spec vs 通常の Protocol* — `Spec` が MRO に含まれることで注入可能な RPC 面を区別
- *ドメイン spec* — ストリーム型付け用の狭義セマンティックタグ

**依存**:基本的に typing/inspect + `annotation_protocol`

---

## `dimos/protocol/`

> **概要。** **トランスポートとサービス**:パブサブ(**LCM**、pickled LCM、ROS、SHM、Redis、メモリ)、**RPC**(LCMRPC、RedisRPC)、**TF**、サービス検出/コンフィギュレータ、コーデック。

**公開インターフェース**
- `PubSub`、トピック、エンコーダ — `pubsub/spec.py`、`pubsub/impl/*`
- `LCMRPC`、`PickleLCM` — `rpc/pubsubrpc.py`、pubsub 層に紐づく
- `LCMTF`、`TFSpec` — `tf/tf.py`
- `LCMService`、DDS ミラー — `service/lcmservice.py`、`service/ddsservice.py`
- `Configurable`、`BaseConfig` — `service/spec.py`

**主要概念**
- *プラグイン可能な pubsub バックエンド* — 同一の `PubSub` インターフェース
- *クロスプロセス RPC* — 例外は `rpc_utils` 経由でシリアライズ

**依存**:`dimos.constants`、`dimos.utils`;`pubsub` はオプション extra(DDS、ROS)を使用する場合あり

**サブディレクトリ**
- `pubsub/impl/` — lcm、ros、shm、redis、dds、jpeg…
- `rpc/`、`service/`、`tf/`、`encode/`
- `pubsub/benchmark/` — 性能ベンチマーク

---

## `dimos/exceptions/`

> **概要。** **廃止された** AgentMemory / ベクトル DB レイヤのエラー型。

**公開インターフェース**
- `AgentMemoryError`、`AgentMemoryConnectionError`、`DataNotFoundError`… — `agent_memory_exceptions.py` のみ

**主要概念**
- *型付きの取得失敗* — ベクトル ID 欠如、接続問題

**依存**:標準ライブラリのみ(このファイルに `dimos.*` import なし)

---

## `dimos/visualization/`

> **概要。** **Rerun 統合**:LCM スタイルのトラフィックを購読し、**`to_rerun()`** を実装するメッセージを変換、ビューアを起動。

**公開インターフェース**
- `RerunBridgeModule` — `rerun/bridge.py`(外部の `rerun.blueprint` と DimOS の `Blueprint` の名前衝突に注意)
- `rerun_init` とヘルパー — `rerun/init.py`

**主要概念**
- *PubSub のタップイン* — トピックパターンマッチ
- *オプションの grpc/web ビューアポート* — モジュールの先頭で定数として export

**依存**:`dimos.core`、`dimos.protocol.pubsub`、`dimos.utils`

**サブディレクトリ**
- `rerun/` — bridge + テスト + `init.py`

---

## `dimos/utils/`

> **概要。** **共有基盤**:ロギング設定、**データ**パスヘルパー(`get_data`)、数学(`Vector`、変換)、**Rx ヘルパー**、CLI 診断(**`lcmspy`**、**`dtop`**、agentspy、foxglove bridge launcher)、replay/moment テストユーティリティ。

**公開インターフェース**(代表的な例)
- `setup_logger`、実行ごとのログディレクトリ — `logging_config.py`
- `get_data`、`get_data_dir` — `data.py`
- `backpressure` Rx ユーティリティ — `reactive.py`
- CLI エントリモジュール — `cli/lcmspy/lcmspy.py`、`cli/dtop.py`、`cli/agentspy/agentspy.py`…
- `transform_utils`、`trigonometry`、`path_utils`、`urdf.py`…

**主要概念**
- *テスト replay* — `utils/testing/replay.py`、`moment.py`、決定論的なセンサーを提供
- *運用 CLI* — 完全な `dimos` スタックを起動せずに内省可能

**依存**:`dimos.msgs`、`dimos.core`、`dimos.memory*` を広く参照、テストヘルパーで `dimos.robot`、`dimos.protocol`、`dimos.types` も import

**サブディレクトリ**
- `cli/` — 運用コマンド
- `testing/` — replay/moment フィクスチャ
- `decorators/`、`docs/` — 横断的ヘルパー

---

## `dimos/types/`

> **概要。** **軽量な共有型**(完全な **`msgs`** ではない):numpy の **`Vector`**、タイムスタンプヘルパー、ロボット能力、weak コンテナ。

**公開インターフェース**
- `Vector` — `vector.py`
- `Timestamped`、`to_timestamp` — `timestamped.py`
- `WeakList` — `weaklist.py`
- `Sample` — `sample.py`
- `RobotLocation` — `robot_location.py`
- `Vector3` polyfill — `ros_polyfill.py`
- `RobotCapability` — `robot_capabilities.py`(`dimos.robot.robot.Robot` で使用)
- `Colors` — `types/constants` 領域(存在する場合)

**主要概念**
- *非 ROS な幾何ヘルパー* — 純粋な Python 数学に重い `geometry_msgs` を持ち込まない
- *空間メモリ ID* — `RobotLocation` が名前 ↔ pose メタデータを結びつける

**依存**:主に `numpy`;一部の変換で `dimos.msgs` を使用

---

## `dimos/msgs/`

> **概要。** ROS 1 形状をミラーした**型付きメッセージモデル**(geometry、sensor、nav、trajectory、vision_msgs)+ **`DimosMsg`**、Foxglove オーバーレイ、大量のラウンドトリップテスト。

**公開インターフェース**
- 各トピックのクラス — 例:`sensor_msgs/Image.py`、`geometry_msgs/PoseStamped.py`、`nav_msgs/OccupancyGrid.py`…
- `DimosMsg` プロトコル — `msgs/protocol.py`
- 共通ヘルパー — `msgs/helpers.py`

**主要概念**
- *シリアライズに優しい dataclass / pydantic 風パターン* — LCM/ROS ブリッジで使用
- *画像バリア* — `Image.py` の `sharpness_barrier` フックで負荷削減

**依存**:`msgs/*` 内部での self-reference(他の `dimos.*` はほぼなし)

**サブディレクトリ**
- `geometry_msgs/`、`sensor_msgs/`、`nav_msgs/`、`std_msgs/`、`trajectory_msgs/`、`vision_msgs/`、`tf2_msgs/`、`foxglove_msgs/`…

---

## `dimos/project/`

> **概要。** **メタテスト**でリポジトリ規約を守る — **ランタイムの「バージョン」モジュールではない**(バージョンメタデータには **`pyproject.toml`** / packaging を使用)。

**公開インターフェース**
- `test_get_logger.py`、`test_no_init_files.py`… — ロギングとレイアウトのポリシーを強制

**主要概念**
- *CI 衛生* — ツリーへの静的スキャン

**依存**:`dimos.constants`(パスルート)

---

## `dimos/rxpy_backpressure/`

> **概要。** observer のための **Latest / Drop / Buffer** 戦略を提供する小型の **RxPy オペレータキット**(`BackPressure` 名前空間)。

**公開インターフェース**
- `BackPressure` — `backpressure.py` — `drop.py`、`latest.py` モジュールを参照
- `wrap_observer_with_*` — 各戦略のヘルパー

**主要概念**
- *Observer 側のフロー制御* — `dimos.utils.reactive` を補完

**依存**:パッケージ内部のみ(`rxpy_backpressure/` 兄弟ファイルを re-import)

---

## このドキュメントの読み方(コードナビゲーション)

1. **特定の振る舞いを探す?** `agents/skills/` から開始 → どの `Module` が対応する `@skill` を公開しているかを辿る → 支持層(`navigation`、`mapping`、`manipulation` など)へ。
2. **I/O 契約を探す?** `spec/`(能力)と `msgs/`(payload)から始める。次にトランスポートの `protocol/` を確認。
3. **ランタイムを探す?** `core/` がカーネル;`porcelain/` が人間工学レイヤ;`robot/cli/` がユーザーエントリポイント。
4. **典型的な sensor → action チェーン:** `hardware/sensors` → `perception` → `memory2`(+ `mapping`)→ `agents`(計画)→ `skills` / `navigation` / `manipulation` → `control` → `hardware/manipulators|drive_trains`。
5. **新コードでは避けるべきレガシーモジュール:** `agents_deprecated/`、`skills/`(Pydantic 経路)、`memory/`、`exceptions/`。
