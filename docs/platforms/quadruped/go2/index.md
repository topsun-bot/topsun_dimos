# Unitree Go2 — Getting Started

The Unitree Go2 is DimOS's primary reference platform. Full autonomous navigation, mapping, and agentic control — no ROS required.

## Requirements

- Unitree Go2 Pro or Air (stock firmware 1.1.7+, no jailbreak needed)
- Ubuntu 22.04/24.04 with CUDA GPU (recommended), or macOS (experimental)
- Python 3.12

## Install

First, install system dependencies for your platform:
- [Ubuntu](/docs/installation/ubuntu.md)
- [macOS](/docs/installation/osx.md)
- [Nix](/docs/installation/nix.md)

Then install DimOS:

```bash
uv venv --python "3.12"
source .venv/bin/activate
uv pip install 'dimos[base,unitree]'
```

## Try It — No Hardware Needed

```bash
# Replay a recorded Go2 navigation session
# First run downloads ~2.4 GB of LiDAR/video data from LFS
dimos --replay run unitree-go2
```

Opens the command center at [localhost:7779](http://localhost:7779) with Rerun 3D visualization — watch the Go2 map and navigate an office in real time.

## Run on Your Go2

### First-time setup, connecting to wifi, finding robot IP

Use `dimos go2tool` to provision wifi and find the robot's IP. Skip if the robot is already on your network and you know its IP.

1. Power on the Go2 — it advertises over BLE immediately.

2. Provision wifi (one-time per network):

optionally use discover to make sure robot is detected

```bash
dimos go2tool discover
```

configure wifi

```bash
dimos go2tool connect-wifi --ssid <wifi> --password <password>
```

Scans BLE and connects to the only robot it finds, or prompts you to pick if there are several.

3. Find the robot's IP:

```bash
dimos go2tool discover
```

Prints `SOURCE NAME IP MAC SERIAL` for every robot it sees over BLE and LAN. Export the IP:

```bash
export ROBOT_IP=<discovered_ip>
```

### Pre-flight checks

1. Robot is reachable and low latency <10ms, 0% packet loss
```bash
ping $ROBOT_IP
```

2. Built-in obstacle avoidance is on. (DimOS handles path planning, but the onboard obstacle avoidance provides an extra safety layer around tight spots)

### Ready to run DimOS

```bash
export ROBOT_IP=<YOUR_GO2_IP>
dimos run unitree-go2
```

That's it. DimOS connects via WebRTC (no jailbreak required), starts the full navigation stack, and opens the command center in your browser.

### What's Running

| Module | What It Does |
|--------|-------------|
| **GO2Connection** | WebRTC connection to the robot — streams LiDAR, video, odometry |
| **VoxelGridMapper** | Builds a 3D voxel map using column-carving (CUDA accelerated) |
| **CostMapper** | Converts 3D map → 2D costmap via terrain slope analysis |
| **ReplanningAStarPlanner** | Continuous A* path planning with dynamic replanning |
| **WavefrontFrontierExplorer** | Autonomous exploration of unmapped areas |
| **RerunBridge** | 3D visualization in browser |
| **WebsocketVis** | Command center at localhost:7779 |

### Send Goals

From the command center ([localhost:7779](http://localhost:7779)):
- Click on the map to set navigation goals
- Toggle autonomous exploration
- Monitor robot pose, costmap, and planned path

## MuJoCo Simulation

```bash
uv pip install 'dimos[base,unitree,sim]'
dimos --simulation run unitree-go2
```

Full navigation stack in MuJoCo — same code, simulated robot.

## Agentic Control

Natural language control with an LLM agent that understands physical space:

```bash
export OPENAI_API_KEY=<YOUR_KEY>
export ROBOT_IP=<YOUR_GO2_IP>
dimos run unitree-go2-agentic
```

Then use the human CLI to talk to the agent:

```bash
humancli
> explore the space
```

The agent subscribes to camera, LiDAR, and spatial memory streams — it sees what the robot sees.

## Available Blueprints

| Blueprint | Description |
|-----------|-------------|
| `unitree-go2-basic` | Connection + visualization (no navigation) |
| `unitree-go2` | Full navigation stack (legacy: Go2 built-in 4D LiDAR + VoxelGridMapper + CostMapper + ReplanningAStar) |
| `unitree-go2-nav-onboard` | **New (jtlinux)**: Mid-360 + FAST-LIO2 + PGO + nav_stack (planner=simple); long-range drift correction |
| `unitree-go2-agentic` | Navigation + LLM agent + MCP tool access |
| `unitree-go2-agentic-ollama` | Agent with local Ollama models |
| `unitree-go2-spatial` | Navigation + spatial memory |
| `unitree-go2-detection` | Navigation + object detection |
| `unitree-go2-ros` | ROS 2 bridge mode |

## Mid-360 + nav_stack onboard mode (`unitree-go2-nav-onboard`)

For long-range navigation with drift correction, use the new `unitree-go2-nav-onboard`
blueprint. It runs a Livox Mid-360 LiDAR + FAST-LIO2 + PGO loop closure + nav_stack
(SimplePlanner / LocalPlanner / PathFollower) entirely on the Jetson Orin NX expansion
board mounted on the Go2.

| Aspect | Legacy `unitree-go2` | New `unitree-go2-nav-onboard` |
|---|---|---|
| LiDAR | Go2 built-in 4D voxelmap | Livox Mid-360 (40-line, 360°×59°, 40 m @ 10%) |
| Odometry | WebRTC-derived | FAST-LIO2 (LiDAR+IMU tightly coupled) |
| Loop closure | None | PGO (GTSAM iSAM2 + PCL ICP) |
| TF | Single world | Layered map / odom / body / sensor |
| Planner | ReplanningAStar | SimplePlanner (default) or FarPlanner |
| Controller | MovementManager P-control | PathFollower (Pure Pursuit + PID yaw) |
| Default `max_speed` | 1.0 m/s | **0.4 m/s** (conservative merge default; can tune up to 0.6 in experiments) |

### Quick start

```bash
# On the Orin NX expansion board (192.168.123.18)
export LIDAR_HOST_IP=192.168.123.18
export LIDAR_IP=192.168.123.20
unset ROBOT_IP   # auto-discovery via dimos go2tool

dimos --viewer rerun run unitree-go2-nav-onboard --daemon
dimos status
dimos log -f
```

For full deployment / runbook / rollback, see
[`jiangtao/runbook/go2-nav-onboard.md`](/jiangtao/runbook/go2-nav-onboard.md)
and [`jiangtao/plan/plan.md`](/jiangtao/plan/plan.md).

## Deep Dive

- [Navigation Stack](/docs/capabilities/navigation/native/index.md) — column-carving voxel mapping, costmap generation, A* planning
- [Visualization](/docs/usage/visualization.md) — Rerun, Foxglove, performance tuning
- [Data Streams](/docs/usage/data_streams) — RxPY streams, backpressure, quality filtering
- [Transports](/docs/usage/transports/index.md) — LCM, SHM, DDS
- [Blueprints](/docs/usage/blueprints.md) — composing modules
