# HoloAgent / HoloMotion with DimOS

This DimOS fork already has working Unitree **Go2** and **G1** agent demos. Horizon [HoloAgent](https://github.com/yixinzhangagent/HoloAgent) and [HoloMotion](https://github.com/yixinzhangagent/HoloMotion) stay **optional external clones**. Do not vendor those trees into this repo.

Agent-oriented CLI notes: [AGENTS.md](/AGENTS.md).

## How the stacks relate

```text
Language goal
      │
      ├─ DimOS (this repo) ── @skill / MCP / LCM ── Go2 or G1
      │     unitree-go2-agentic   (replay or --robot-ip)
      │     unitree-g1-agentic-sim / unitree-g1-agentic
      │
      └─ HoloAgent (clone) ── ROS 2 Humble AgentOS ── Unitree adapters
            └── G1 whole-body motion via HoloMotion (clone or Docker)
```

| Path | What it is | Reuse |
|------|------------|--------|
| **DimOS agentic blueprints** | Python modules, `autoconnect()`, `@skill`, MCP on port 9990 | **Primary.** Run these first. |
| **HoloAgent** | Horizon Embodied AgentOS + FSR-VLN + ROS 2 robot adapters. Inspired by DimOS skills/blueprints. | Optional clone. Separate venv / ROS workspace. |
| **HoloMotion** | Horizon whole-body G1 motion policy (Docker on Orin, or train/eval from the repo). | Optional. Used by Horizon G1 motion demos, not by `dimos run`. |

`examples/nav-go2` is a **non-agent** NoMaD navigation experiment. Use it when you want vision trajectories, not LLM skills.

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

# Go2 hardware
dimos run unitree-go2-agentic --robot-ip 192.168.123.161

# G1 MuJoCo + agent
dimos --simulation run unitree-g1-agentic-sim

# G1 hardware
dimos run unitree-g1-agentic --robot-ip 192.168.123.161
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
```

Default dest is the **parent** of this checkout (sibling dirs). Override with `--dest`, `HOLO_WORKSPACE`, `HOLOAGENT_ROOT`, or `HOLOMOTION_ROOT`.

```sh skip
git clone https://github.com/yixinzhangagent/HoloAgent.git ../HoloAgent
git clone https://github.com/yixinzhangagent/HoloMotion.git ../HoloMotion
```

These commands start **Horizon** stacks, not `dimos run`:

| Fork | First docs | First dry command |
|------|------------|-------------------|
| HoloAgent | [Introduction](https://github.com/yixinzhangagent/HoloAgent/blob/main/docs/user_guide/Intruduction.md) (upstream filename spelling), [agentic runbook](https://github.com/yixinzhangagent/HoloAgent/blob/main/docs/user_guide/README_agent.md) | `bash scripts/build.sh` then `python3 agentic_robot/agentOS/sandbox_test/test_single_robot_long_instruction.py` |
| HoloMotion | [Real-world deployment](https://github.com/yixinzhangagent/HoloMotion/blob/master/docs/realworld_deployment.md) | On G1 Orin Docker: `holomotion check` (sends no robot action) |

HoloAgent expects ROS 2 Humble, `colcon`, and robot-side services. HoloMotion v1.4.1 real-robot deploy is the Orin Docker image (`holomotion check` → `offline` → `teleop`). Do not mix those environments into the DimOS `.venv`.

## What we did not do

- No copy of HoloAgent/HoloMotion source into this tree
- No new default blueprint that replaces `unitree-go2-agentic` / `unitree-g1-agentic-sim`
- No committed credentials, robot IPs, or media

## Related

- [Agents](/docs/capabilities/agents/readme.md)
- [Go2 getting started](/docs/platforms/quadruped/go2/index.md)
- [G1](/docs/platforms/humanoid/g1/index.md)
- [CLI](/docs/usage/cli.md)
- [examples/nav-go2](/examples/nav-go2/README.md)
