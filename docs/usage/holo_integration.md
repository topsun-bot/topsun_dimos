# Horizon nav / manip forks with DimOS

This DimOS fork already has working Unitree **Go2** and **G1** agent and nav demos. Horizon forks stay **optional external clones**. Do not vendor those trees into this repo.

Agent-oriented CLI notes: [AGENTS.md](/AGENTS.md). Cheat-sheet: `uv run python scripts/holo_bridge.py list-demos`.

## Taxonomy

| Layer | Fork | Clone | Relates to DimOS | 中文 |
|-------|------|-------|------------------|------|
| **AGENT** | [HoloAgent](https://github.com/yixinzhangagent/HoloAgent) | `yixinzhangagent/HoloAgent` | Optional ROS 2 AgentOS + FSR-VLN beside `unitree-go2-agentic` / `unitree-g1-agentic(-sim)` | AgentOS + FSR-VLN 导航与技能 |
| **MANIP** | [HoloMotion](https://github.com/yixinzhangagent/HoloMotion) | `yixinzhangagent/HoloMotion` | Optional G1 whole-body motion beside `unitree-g1-agentic(-sim)`; not `dimos run` | G1 全身运动 |
| **NAV** | [GeoFlowSlam](https://github.com/zhangyinxina-ui/GeoFlowSlam) | `zhangyinxina-ui/GeoFlowSlam` | **Primary NAV add.** RGBD-inertial + legged-odometry SLAM beside `unitree-go2` and [examples/nav-go2](/examples/nav-go2/README.md) | 腿式 RGBD-惯性 SLAM |
| **PERCEPTION** | [BIP3D](https://github.com/zhangyinxina-ui/BIP3D) | `zhangyinxina-ui/BIP3D` | 2D↔3D detection/grounding. Snapshot; active code moved to RoboOrchardLab | 2D↔3D 具身感知 |
| **PERCEPTION** | [RoboOrchardLab](https://github.com/zhangyinxina-ui/RoboOrchardLab) | `zhangyinxina-ui/RoboOrchardLab` | Training lab (`projects/bip3d_grounding`). Not a DimOS blueprint | 具身训练实验室 |
| **SIM** | [EmbodiedGen](https://github.com/zhangyinxina-ui/EmbodiedGen) | `zhangyinxina-ui/EmbodiedGen` | Sim-ready 3D worlds. Does not replace `dimos --simulation` MuJoCo | 仿真就绪 3D 世界 |
| **SIM** | [RoboTransfer](https://github.com/zhangyinxina-ui/RoboTransfer) | `zhangyinxina-ui/RoboTransfer` | Visual policy-transfer data synth. Training/data only | 视觉策略迁移数据合成 |

Print the same table from the helper: `uv run python scripts/holo_bridge.py taxonomy`.

**Out of scope** (do not integrate): Sparse4D, GUMP (AV planner), CARLA / nuplan / leaderboard, OE-Skills chip toolchain, x2 bootprint demos. SocialRobot is legacy-only — no clone helper.

## How the stacks relate

```text
Language goal
      │
      ├─ DimOS (this repo) ── @skill / MCP / LCM ── Go2 or G1
      │     unitree-go2                 (native SLAM / costmap / A*)
      │     unitree-go2-agentic         (replay or --robot-ip)
      │     unitree-g1-agentic-sim / unitree-g1-agentic
      │     examples/nav-go2            (NoMaD vision nav, script entry)
      │
      ├─ optional NAV clones (not dimos run)
      │     GeoFlowSlam   RGBD-inertial + legged odometry SLAM
      │     HoloAgent     FSR-VLN + AgentOS (ROS 2 Humble)
      │
      ├─ optional MANIP / AGENT
      │     HoloMotion    G1 whole-body (Orin Docker)
      │
      └─ optional PERCEPTION / SIM (training & data, not robot runtime)
            BIP3D / RoboOrchardLab / EmbodiedGen / RoboTransfer
```

| Path | What it is | Reuse |
|------|------------|--------|
| **DimOS agentic blueprints** | Python modules, `autoconnect()`, `@skill`, MCP on port 9990 | **Primary.** Run these first. |
| **DimOS nav** | `unitree-go2` voxel/costmap/A*; `examples/nav-go2` NoMaD | **Primary in-tree nav.** |
| **GeoFlowSlam** | IROS 2025 tightly-coupled RGBD-inertial + legged-odometry SLAM | Optional clone. Separate C++ / ROS 2 build. |
| **HoloAgent** | Horizon Embodied AgentOS + FSR-VLN + ROS 2 adapters | Optional clone. Separate venv / ROS workspace. |
| **HoloMotion** | Horizon whole-body G1 motion (Docker on Orin) | Optional. Not invoked by `dimos run`. |
| **BIP3D / RoboOrchardLab** | 2D↔3D perception + training lab | Optional. Not a runtime blueprint. |
| **EmbodiedGen / RoboTransfer** | World gen + visual policy-transfer data | Optional SIM/data. Do not mix into DimOS `.venv`. |

## Prerequisites (DimOS demos)

- `uv` and Python 3.12
- `uv sync --extra all` (or `uv pip install 'dimos[base,unitree]'`; add `sim` for MuJoCo)
- `OPENAI_API_KEY` for the default GPT-4o agent
- `--replay` for recorded Go2 data (first run may pull LFS)
- `--simulation` for G1 MuJoCo
- `--robot-ip 192.168.123.161` on real hardware (placeholder — use your robot)

```sh skip
uv venv --python "3.12"
source .venv/bin/activate
uv sync --extra all
```

## Run DimOS agent demos

```sh skip
# Discover blueprints
dimos list

# Go2 replay (no robot)
dimos --replay run unitree-go2-agentic

# Go2 hardware (replace the placeholder IP)
dimos run unitree-go2-agentic --robot-ip 192.168.123.161

# G1 MuJoCo + agent
dimos --simulation run unitree-g1-agentic-sim

# G1 hardware (replace the placeholder IP)
dimos run unitree-g1-agentic --robot-ip 192.168.123.161
```

In-tree nav (no LLM, no Horizon clone):

```sh skip
dimos --replay run unitree-go2
uv run python examples/nav-go2/go2_nomad_nav.py --replay --viewer rerun
```

Then:

```sh skip
dimos status
dimos agent-send "say hello"
dimos mcp list-tools
dimos stop
```

Same commands, printed by the helper:

```sh skip
uv run python scripts/holo_bridge.py --help
uv run python scripts/holo_bridge.py list-demos
```

## Optional: clone the Horizon forks

```sh skip
uv run python scripts/holo_bridge.py print-clone
uv run python scripts/holo_bridge.py status
uv run python scripts/holo_bridge.py next-steps
uv run python scripts/holo_bridge.py taxonomy
```

Default dest is the **parent** of this checkout (sibling dirs). Override with `--dest`, `HOLO_WORKSPACE`, or a per-repo `*_ROOT` env (`HOLOAGENT_ROOT`, `HOLOMOTION_ROOT`, `GEOFLOWSLAM_ROOT`, `BIP3D_ROOT`, `ROBOORCHARDLAB_ROOT`, `EMBODIEDGEN_ROOT`, `ROBOTRANSFER_ROOT`).

```sh skip
git clone https://github.com/yixinzhangagent/HoloAgent.git ../HoloAgent
git clone https://github.com/yixinzhangagent/HoloMotion.git ../HoloMotion
git clone https://github.com/zhangyinxina-ui/GeoFlowSlam.git ../GeoFlowSlam
git clone https://github.com/zhangyinxina-ui/BIP3D.git ../BIP3D
git clone https://github.com/zhangyinxina-ui/RoboOrchardLab.git ../RoboOrchardLab
git clone https://github.com/zhangyinxina-ui/EmbodiedGen.git ../EmbodiedGen
git clone https://github.com/zhangyinxina-ui/RoboTransfer.git ../RoboTransfer
```

These commands start **Horizon** stacks, not `dimos run`:

| Fork | First pointer (in that clone) | First dry command |
|------|-------------------------------|-------------------|
| HoloAgent | [Introduction](https://github.com/yixinzhangagent/HoloAgent/blob/main/docs/user_guide/Intruduction.md) (upstream filename spelling), [agentic runbook](https://github.com/yixinzhangagent/HoloAgent/blob/main/docs/user_guide/README_agent.md) | `bash scripts/build.sh` then `python3 agentic_robot/agentOS/sandbox_test/test_single_robot_long_instruction.py` |
| HoloMotion | [Real-world deployment](https://github.com/yixinzhangagent/HoloMotion/blob/master/docs/realworld_deployment.md) | On G1 Orin Docker: `holomotion check` (sends no robot action) |
| GeoFlowSlam | [README](https://github.com/zhangyinxina-ui/GeoFlowSlam/blob/master/README.md) | `./build.sh` then `python script/run_orbslam/run_rgbd_vi_g1.py` |
| BIP3D | [quick start](https://github.com/zhangyinxina-ui/BIP3D/blob/main/docs/quick_start.md) | Follow that file; prefer RoboOrchardLab `projects/bip3d_grounding` for new work |
| RoboOrchardLab | [README](https://github.com/zhangyinxina-ui/RoboOrchardLab/blob/master/README.md) | Training lab — not a robot start |
| EmbodiedGen | [README](https://github.com/zhangyinxina-ui/EmbodiedGen/blob/master/README.md) | `./install.sh` then follow that README |
| RoboTransfer | [README](https://github.com/zhangyinxina-ui/RoboTransfer/blob/main/README.md) | `uv run main.py` in that clone |

Do not mix Horizon ROS 2 / Docker / C++ environments into the DimOS `.venv`.

## What we did not do

- No copy of the seven Horizon trees into this repo
- No new default blueprint that replaces `unitree-go2-agentic` / `unitree-g1-agentic-sim`
- No Sparse4D / GUMP / CARLA / nuplan / OE-Skills / x2 / SocialRobot integration
- No committed credentials, robot IPs, or media

## Related

- [Agents](/docs/capabilities/agents/readme.md)
- [Go2 getting started](/docs/platforms/quadruped/go2/index.md)
- [G1](/docs/platforms/humanoid/g1/index.md)
- [CLI](/docs/usage/cli.md)
- [Native Go2 nav](/docs/capabilities/navigation/native/index.md)
- [examples/nav-go2](/examples/nav-go2/README.md)
