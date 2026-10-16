# SECURITY_CHECK_2026-10-15 — topsun_dimos

静态扫描(正则,非完整审计)。严重度 H/M/L。真实密钥值未写入本报告,仅列位置。

## 高危 (4)

| 文件:行 | 类别 | 问题 | 建议修法 |
|---|---|---|---|
| `.github/workflows/auto-merge.yml:0` | workflow | pull_request_target trigger | 见下方通用建议 |
| `.github/workflows/backport.yml:0` | workflow | pull_request_target trigger | 见下方通用建议 |
| `.github/workflows/pr-labels.yml:0` | workflow | pull_request_target trigger | 见下方通用建议 |
| `dimos/core/coordination/blueprint_config/test_parser.py:254` | secret | Hardcoded secret assignment | 删除并轮换该凭据,改用环境变量/GitHub Secrets,必要时清理 git 历史 |

## 中危 (74)

| 文件:行 | 类别 | 问题 | 建议修法 |
|---|---|---|---|
| `ai_longrun_harness/run_pr_pipeline.sh:58` | code | shell eval of variable | 避免 shell=True/不安全反序列化,改用参数列表、safe_load、json 等 |
| `ai_longrun_harness/run_pr_pipeline.sh:59` | code | shell eval of variable | 避免 shell=True/不安全反序列化,改用参数列表、safe_load、json 等 |
| `dimos/core/test_baked_host.py:186` | code | pickle load | 避免 shell=True/不安全反序列化,改用参数列表、safe_load、json 等 |
| `dimos/core/test_baked_host.py:188` | code | pickle load | 避免 shell=True/不安全反序列化,改用参数列表、safe_load、json 等 |
| `dimos/core/test_zenoh_transport.py:230` | code | pickle load | 避免 shell=True/不安全反序列化,改用参数列表、safe_load、json 等 |
| `dimos/core/test_zenoh_transport.py:237` | code | pickle load | 避免 shell=True/不安全反序列化,改用参数列表、safe_load、json 等 |
| `dimos/core/coordination/test_blueprints.py:224` | code | pickle load | 避免 shell=True/不安全反序列化,改用参数列表、safe_load、json 等 |
| `dimos/core/coordination/test_blueprints.py:237` | code | pickle load | 避免 shell=True/不安全反序列化,改用参数列表、safe_load、json 等 |
| `dimos/core/coordination/test_module_coordinator.py:1062` | code | pickle load | 避免 shell=True/不安全反序列化,改用参数列表、safe_load、json 等 |
| `dimos/e2e_tests/lcm_spy.py:149` | code | pickle load | 避免 shell=True/不安全反序列化,改用参数列表、safe_load、json 等 |
| `dimos/e2e_tests/test_dimos_cli_e2e.py:36` | code | pickle load | 避免 shell=True/不安全反序列化,改用参数列表、safe_load、json 等 |
| `dimos/experimental/isolated_python/bootstrap.py:94` | code | pickle load | 避免 shell=True/不安全反序列化,改用参数列表、safe_load、json 等 |
| `dimos/manipulation/test_manipulation_spec.py:118` | code | pickle load | 避免 shell=True/不安全反序列化,改用参数列表、safe_load、json 等 |
| `dimos/manipulation/test_manipulation_unit.py:1033` | code | pickle load | 避免 shell=True/不安全反序列化,改用参数列表、safe_load、json 等 |
| `dimos/manipulation/test_manipulation_unit.py:1047` | code | pickle load | 避免 shell=True/不安全反序列化,改用参数列表、safe_load、json 等 |
| `dimos/mapping/pointclouds/test_occupancy_speed.py:29` | code | pickle load | 避免 shell=True/不安全反序列化,改用参数列表、safe_load、json 等 |
| `dimos/memory/tool_visualizer.py:156` | code | pickle load | 避免 shell=True/不安全反序列化,改用参数列表、safe_load、json 等 |
| `dimos/memory/codecs/pickle.py:28` | code | pickle load | 避免 shell=True/不安全反序列化,改用参数列表、safe_load、json 等 |
| `dimos/msgs/geometry_msgs/PoseArray.py:96` | code | pickle load | 避免 shell=True/不安全反序列化,改用参数列表、safe_load、json 等 |
| `dimos/msgs/geometry_msgs/test_Pose.py:574` | code | pickle load | 避免 shell=True/不安全反序列化,改用参数列表、safe_load、json 等 |
| `dimos/msgs/geometry_msgs/test_PoseStamped.py:52` | code | pickle load | 避免 shell=True/不安全反序列化,改用参数列表、safe_load、json 等 |
| `dimos/msgs/geometry_msgs/test_TwistStamped.py:51` | code | pickle load | 避免 shell=True/不安全反序列化,改用参数列表、safe_load、json 等 |
| `dimos/msgs/geometry_msgs/test_WrenchStamped.py:137` | code | pickle load | 避免 shell=True/不安全反序列化,改用参数列表、safe_load、json 等 |
| `dimos/msgs/manipulation_msgs/GraspCandidate.py:39` | code | pickle load | 避免 shell=True/不安全反序列化,改用参数列表、safe_load、json 等 |
| `dimos/msgs/manipulation_msgs/GraspCandidateArray.py:47` | code | pickle load | 避免 shell=True/不安全反序列化,改用参数列表、safe_load、json 等 |
| `dimos/msgs/nav_msgs/test_OccupancyGrid.py:200` | code | pickle load | 避免 shell=True/不安全反序列化,改用参数列表、safe_load、json 等 |
| `dimos/perception/experimental/visual_memory.py:175` | code | pickle load | 避免 shell=True/不安全反序列化,改用参数列表、safe_load、json 等 |
| `dimos/protocol/pubsub/encoders.py:94` | code | pickle load | 避免 shell=True/不安全反序列化,改用参数列表、safe_load、json 等 |
| `dimos/protocol/pubsub/benchmark/test_replay_bench.py:92` | code | pickle load | 避免 shell=True/不安全反序列化,改用参数列表、safe_load、json 等 |
| `dimos/protocol/pubsub/impl/webrtc/test_transport.py:196` | code | pickle load | 避免 shell=True/不安全反序列化,改用参数列表、safe_load、json 等 |
| `dimos/protocol/pubsub/impl/webrtc/test_transport.py:295` | code | pickle load | 避免 shell=True/不安全反序列化,改用参数列表、safe_load、json 等 |
| `dimos/protocol/rpc/zenohrpc.py:134` | code | pickle load | 避免 shell=True/不安全反序列化,改用参数列表、safe_load、json 等 |
| `dimos/protocol/rpc/zenohrpc.py:139` | code | pickle load | 避免 shell=True/不安全反序列化,改用参数列表、safe_load、json 等 |
| `dimos/protocol/rpc/zenohrpc.py:196` | code | pickle load | 避免 shell=True/不安全反序列化,改用参数列表、safe_load、json 等 |
| `dimos/protocol/service/test_lcmservice.py:198` | code | pickle load | 避免 shell=True/不安全反序列化,改用参数列表、safe_load、json 等 |
| `dimos/protocol/service/test_lcmservice.py:214` | code | pickle load | 避免 shell=True/不安全反序列化,改用参数列表、safe_load、json 等 |
| `dimos/protocol/service/test_zenohservice.py:139` | code | pickle load | 避免 shell=True/不安全反序列化,改用参数列表、safe_load、json 等 |
| `dimos/protocol/service/test_zenohservice.py:151` | code | pickle load | 避免 shell=True/不安全反序列化,改用参数列表、safe_load、json 等 |
| `dimos/protocol/service/test_zenohservice.py:156` | code | pickle load | 避免 shell=True/不安全反序列化,改用参数列表、safe_load、json 等 |
| `dimos/robot/assets/test_model.py:709` | code | pickle load | 避免 shell=True/不安全反序列化,改用参数列表、safe_load、json 等 |
| `dimos/robot/assets/test_source.py:126` | code | pickle load | 避免 shell=True/不安全反序列化,改用参数列表、safe_load、json 等 |
| `dimos/simulation/mujoco/mujoco_process.py:233` | code | pickle load | 避免 shell=True/不安全反序列化,改用参数列表、safe_load、json 等 |
| `dimos/simulation/mujoco/shared_memory.py:261` | code | pickle load | 避免 shell=True/不安全反序列化,改用参数列表、safe_load、json 等 |
| `dimos/utils/testing/legacy_pickle.py:160` | code | pickle load | 避免 shell=True/不安全反序列化,改用参数列表、safe_load、json 等 |
| `dimos/utils/testing/legacy_pickle.py:256` | code | pickle load | 避免 shell=True/不安全反序列化,改用参数列表、safe_load、json 等 |
| `dimos/web/test_cockpit.py:149` | code | pickle load | 避免 shell=True/不安全反序列化,改用参数列表、safe_load、json 等 |
| `dimos/web/test_cockpit.py:384` | code | pickle load | 避免 shell=True/不安全反序列化,改用参数列表、safe_load、json 等 |
| `dimos/web/test_cockpit.py:444` | code | pickle load | 避免 shell=True/不安全反序列化,改用参数列表、safe_load、json 等 |
| `dimos/web/test_cockpit.py:482` | code | pickle load | 避免 shell=True/不安全反序列化,改用参数列表、safe_load、json 等 |
| `dimos/web/test_cockpit.py:518` | code | pickle load | 避免 shell=True/不安全反序列化,改用参数列表、safe_load、json 等 |
| `dimos/web/relay_bridge/test_dynamic.py:134` | code | pickle load | 避免 shell=True/不安全反序列化,改用参数列表、safe_load、json 等 |
| `dimos/web/relay_bridge/test_dynamic.py:142` | code | pickle load | 避免 shell=True/不安全反序列化,改用参数列表、safe_load、json 等 |
| `dimos/web/relay_bridge/test_dynamic.py:143` | code | pickle load | 避免 shell=True/不安全反序列化,改用参数列表、safe_load、json 等 |
| `dimos/web/relay_bridge/test_dynamic.py:180` | code | pickle load | 避免 shell=True/不安全反序列化,改用参数列表、safe_load、json 等 |
| `dimos/web/relay_bridge/test_dynamic.py:181` | code | pickle load | 避免 shell=True/不安全反序列化,改用参数列表、safe_load、json 等 |
| `dimos/web/relay_bridge/test_dynamic.py:283` | code | pickle load | 避免 shell=True/不安全反序列化,改用参数列表、safe_load、json 等 |
| `dimos/web/relay_bridge/test_relay_bridge_authoring.py:180` | code | pickle load | 避免 shell=True/不安全反序列化,改用参数列表、safe_load、json 等 |
| `docker/dev/bash.sh:68` | code | shell eval of variable | 避免 shell=True/不安全反序列化,改用参数列表、safe_load、json 等 |
| `docker/dev/bash.sh:133` | code | shell eval of variable | 避免 shell=True/不安全反序列化,改用参数列表、safe_load、json 等 |
| `docker/dev/bash.sh:141` | code | shell eval of variable | 避免 shell=True/不安全反序列化,改用参数列表、safe_load、json 等 |
| … | | 另有 14 条 | |

## 低危 (53)

| 文件:行 | 类别 | 问题 | 建议修法 |
|---|---|---|---|
| `.github/workflows/_docker-build-template.yml:119` | workflow | third-party action not SHA-pinned: docker/login-action@v4.6.0 | 见下方通用建议 |
| `.github/workflows/_docker-build-template.yml:127` | workflow | third-party action not SHA-pinned: crazy-max/ghaction-github-runtime@v4 | 见下方通用建议 |
| `.github/workflows/_docker-build-template.yml:131` | workflow | third-party action not SHA-pinned: docker/setup-buildx-action@v4.3.0 | 见下方通用建议 |
| `.github/workflows/_docker-build-template.yml:137` | workflow | third-party action not SHA-pinned: docker/build-push-action@v7.3.0 | 见下方通用建议 |
| `.github/workflows/_docker-build-template.yml:0` | workflow | no top-level permissions block (defaults may be broad) | 见下方通用建议 |
| `.github/workflows/auto-merge.yml:15` | workflow | third-party action not SHA-pinned: dependabot/fetch-metadata@v3 | 见下方通用建议 |
| `.github/workflows/autofix.yml:20` | workflow | third-party action not SHA-pinned: astral-sh/setup-uv@v10.0.1 | 见下方通用建议 |
| `.github/workflows/autofix.yml:34` | workflow | third-party action not SHA-pinned: autofix-ci/action@v1 | 见下方通用建议 |
| `.github/workflows/backport.yml:24` | workflow | third-party action not SHA-pinned: korthout/backport-action@v4 | 见下方通用建议 |
| `.github/workflows/ci.yml:116` | workflow | third-party action not SHA-pinned: BRAINSia/free-disk-space@v2.1.3 | 见下方通用建议 |
| `.github/workflows/ci.yml:139` | workflow | third-party action not SHA-pinned: cachix/cachix-action@v17 | 见下方通用建议 |
| `.github/workflows/ci.yml:176` | workflow | third-party action not SHA-pinned: astral-sh/setup-uv@v10.0.1 | 见下方通用建议 |
| `.github/workflows/ci.yml:191` | workflow | third-party action not SHA-pinned: denoland/setup-deno@v2 | 见下方通用建议 |
| `.github/workflows/ci.yml:197` | workflow | third-party action not SHA-pinned: pre-commit/action@v3.0.1 | 见下方通用建议 |
| `.github/workflows/ci.yml:212` | workflow | third-party action not SHA-pinned: dtolnay/rust-toolchain@stable | 见下方通用建议 |
| `.github/workflows/ci.yml:216` | workflow | third-party action not SHA-pinned: Swatinem/rust-cache@v2 | 见下方通用建议 |
| `.github/workflows/ci.yml:228` | workflow | third-party action not SHA-pinned: astral-sh/setup-uv@v10.0.1 | 见下方通用建议 |
| `.github/workflows/ci.yml:299` | workflow | third-party action not SHA-pinned: BRAINSia/free-disk-space@v2.1.3 | 见下方通用建议 |
| `.github/workflows/ci.yml:352` | workflow | third-party action not SHA-pinned: astral-sh/setup-uv@v10.0.1 | 见下方通用建议 |
| `.github/workflows/ci.yml:418` | workflow | third-party action not SHA-pinned: denoland/setup-deno@v2 | 见下方通用建议 |
| `.github/workflows/ci.yml:469` | workflow | third-party action not SHA-pinned: astral-sh/setup-uv@v10.0.1 | 见下方通用建议 |
| `.github/workflows/ci.yml:533` | workflow | third-party action not SHA-pinned: astral-sh/setup-uv@v10.0.1 | 见下方通用建议 |
| `.github/workflows/ci.yml:546` | workflow | third-party action not SHA-pinned: denoland/setup-deno@v2 | 见下方通用建议 |
| `.github/workflows/ci.yml:591` | workflow | third-party action not SHA-pinned: codecov/codecov-action@v7 | 见下方通用建议 |
| `.github/workflows/ci.yml:606` | workflow | third-party action not SHA-pinned: codecov/codecov-action@v7 | 见下方通用建议 |
| `.github/workflows/ci.yml:682` | workflow | third-party action not SHA-pinned: astral-sh/setup-uv@v10.0.1 | 见下方通用建议 |
| `.github/workflows/ci.yml:768` | workflow | third-party action not SHA-pinned: codecov/codecov-action@v7 | 见下方通用建议 |
| `.github/workflows/ci.yml:781` | workflow | third-party action not SHA-pinned: codecov/codecov-action@v7 | 见下方通用建议 |
| `.github/workflows/ci.yml:852` | workflow | third-party action not SHA-pinned: astral-sh/setup-uv@v10.0.1 | 见下方通用建议 |
| `.github/workflows/ci.yml:913` | workflow | third-party action not SHA-pinned: codecov/codecov-action@v7 | 见下方通用建议 |
| `.github/workflows/ci.yml:926` | workflow | third-party action not SHA-pinned: codecov/codecov-action@v7 | 见下方通用建议 |
| `.github/workflows/ci.yml:980` | workflow | third-party action not SHA-pinned: re-actors/alls-green@release/v1 | 见下方通用建议 |
| `.github/workflows/ci.yml:985` | workflow | third-party action not SHA-pinned: codecov/codecov-action@v7 | 见下方通用建议 |
| `.github/workflows/dimsim-check.yml:38` | workflow | third-party action not SHA-pinned: denoland/setup-deno@v2 | 见下方通用建议 |
| `.github/workflows/docker-build.yml:45` | workflow | third-party action not SHA-pinned: dorny/paths-filter@v4.0.3 | 见下方通用建议 |
| `.github/workflows/docs-deploy.yml:40` | workflow | third-party action not SHA-pinned: astral-sh/setup-uv@v10.0.1 | 见下方通用建议 |
| `.github/workflows/release-build-check.yml:41` | workflow | third-party action not SHA-pinned: pypa/cibuildwheel@v4.2.0 | 见下方通用建议 |
| `.github/workflows/release-build-check.yml:55` | workflow | third-party action not SHA-pinned: astral-sh/setup-uv@v10.0.1 | 见下方通用建议 |
| `.github/workflows/release.yml:34` | workflow | third-party action not SHA-pinned: astral-sh/setup-uv@v10.0.1 | 见下方通用建议 |
| `.github/workflows/release.yml:83` | workflow | third-party action not SHA-pinned: astral-sh/setup-uv@v10.0.1 | 见下方通用建议 |
| `.github/workflows/release.yml:133` | workflow | third-party action not SHA-pinned: pypa/cibuildwheel@v4.2.0 | 见下方通用建议 |
| `.github/workflows/release.yml:161` | workflow | third-party action not SHA-pinned: astral-sh/setup-uv@v10.0.1 | 见下方通用建议 |
| `.github/workflows/release.yml:167` | workflow | third-party action not SHA-pinned: pypa/gh-action-pypi-publish@release/v1 | 见下方通用建议 |
| `.github/workflows/release.yml:190` | workflow | third-party action not SHA-pinned: astral-sh/setup-uv@v10.0.1 | 见下方通用建议 |
| `.github/workflows/release.yml:196` | workflow | third-party action not SHA-pinned: pypa/gh-action-pypi-publish@release/v1 | 见下方通用建议 |
| `dimos/cli/topic.py:156` | code | eval/exec | 避免 shell=True/不安全反序列化,改用参数列表、safe_load、json 等 |
| `dimos/manipulation/grasping/docker_context/Dockerfile:0` | docker | no non-root USER (runs as root) | 添加 USER 非 root |
| `dimos/simulation/dimsim/scene_client.py:19` | code | eval/exec | 避免 shell=True/不安全反序列化,改用参数列表、safe_load、json 等 |
| `dimos/simulation/dimsim/scene_client.py:224` | code | eval/exec | 避免 shell=True/不安全反序列化,改用参数列表、safe_load、json 等 |
| `docker/dev/Dockerfile:0` | docker | no non-root USER (runs as root) | 添加 USER 非 root |
| `docker/python/Dockerfile:0` | docker | no non-root USER (runs as root) | 添加 USER 非 root |
| `docker/ros-dev-pin/Dockerfile:0` | docker | no non-root USER (runs as root) | 添加 USER 非 root |
| `docker/ros/Dockerfile:0` | docker | no non-root USER (runs as root) | 添加 USER 非 root |

## Dependabot 未关闭告警

| 严重度 | 包 | GHSA | manifest |
|---|---|---|---|
| high | hydra-core | GHSA-c3wx-c55w-pxjq | uv.lock |
| medium | Werkzeug | GHSA-g6x2-hccm-hh4m | uv.lock |
| high | langgraph-sdk | GHSA-fvww-7h3r-vfhp | uv.lock |
| high | virtualenv | GHSA-p58f-9548-mpm2 | uv.lock |
| high | virtualenv | GHSA-x78j-v8h9-3j2q | uv.lock |
| high | virtualenv | GHSA-94p9-xgh2-xp45 | uv.lock |
| medium | virtualenv | GHSA-9h9j-4vrj-gf7g | uv.lock |
| medium | PyJWT | GHSA-gvp8-978c-rx2q | uv.lock |
| medium | tornado | GHSA-3hv7-mjh2-fv65 | uv.lock |
| high | tornado | GHSA-chx6-46f5-w4vp | uv.lock |
| high | tornado | GHSA-c2m8-h5v5-343r | uv.lock |
| medium | GitPython | GHSA-59cr-6r3x-644w | uv.lock |
| high | transformers | GHSA-x9r9-c232-4q39 | uv.lock |
| high | GitPython | GHSA-g5vv-9gxw-82hx | uv.lock |
| high | gitpython | GHSA-239g-whfq-7xj9 | uv.lock |
| medium | PyJWT | GHSA-42vr-xj54-vc7v | uv.lock |
| medium | urllib3 | GHSA-gh4c-6fx4-qh6g | uv.lock |
| high | urllib3 | GHSA-vxq7-64xx-v4gw | uv.lock |
| high | urllib3 | GHSA-8988-9cw3-xx77 | uv.lock |
| medium | pyjwt | GHSA-jwrc-g2q2-pq5p | uv.lock |
| high | PyJWT | GHSA-9j54-fg26-wv3r | uv.lock |
| medium | pyJWT | GHSA-8wjv-2p76-3863 | uv.lock |
| medium | PyJWT | GHSA-hxm8-2xgr-2p9m | uv.lock |
| critical | PyJWT | GHSA-ffc3-869f-jxw9 | uv.lock |
| high | PyJWT | GHSA-w2cx-738m-mc7w | uv.lock |
| high | PyJWT | GHSA-9v7f-9g4p-ffgj | uv.lock |
| high | pyjwt | GHSA-p4g4-x82p-q773 | uv.lock |
| high | PyJWT | GHSA-r6x4-923q-g947 | uv.lock |
| medium | pyjwt | GHSA-2gx3-rcp4-g85q | uv.lock |
| medium | PyJWT | GHSA-w6j9-cwv2-h6wq | uv.lock |
| medium | oauthlib | GHSA-xpv3-w29h-x7cv | uv.lock |
| medium | oauthlib | GHSA-hj66-6f7g-4r5v | uv.lock |
| high | cryptography | GHSA-jwv3-5hgf-82ww | uv.lock |
| high | GitPython | GHSA-94p4-4cq8-9g67 | uv.lock |
| medium | cryptography | GHSA-m2h6-j472-rp4c | uv.lock |
| critical | anyio | GHSA-82r6-8w77-94w6 | uv.lock |
| medium | anyio | GHSA-5p39-cfhj-2xmp | uv.lock |
| medium | soupsieve | GHSA-gjv8-xp57-g29c | uv.lock |
| medium | soupsieve | GHSA-j934-xhv5-fg8f | uv.lock |
| medium | vitest | GHSA-82fw-gwwq-j7x9 | web/sdk/package.json |

## 通用建议
- pull_request_target:不要 checkout PR head 代码并执行;或改用 pull_request。
- 工作流中不要把 `${{ github.event.* }}` 直接插入 run:,改用 env 变量。
- 第三方 action 固定到 commit SHA;工作流加 `permissions: contents: read`。

本 PR 仅添加此报告,未改动代码。