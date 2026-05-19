# Unitree official MuJoCo simulator (optional)

**Default:** `mujoco_backend=dimos` — in-repo MuJoCo (lidars, head camera, stairs rooms, Sport API → SHM). See [go2_stair_mujoco_sport.md](go2_stair_mujoco_sport.md).

DimOS can optionally drive [unitree_mujoco](https://github.com/unitreerobotics/unitree_mujoco) with `--mujoco-backend unitree` (DDS + official Go2 MJCF, no DimOS lidar stack).

## Setup

```bash
git clone https://github.com/unitreerobotics/unitree_mujoco third_party/unitree_mujoco

# Go2 stairs / mapping blueprints (MuJoCo + DDS + torch for VoxelGridMapper/memory2)
uv sync --extra go2-sim

# MuJoCo only (no Go2 blueprint stack): uv sync --extra sim
```

### CycloneDDS (required for `unitree-sdk2py`)

The Python package `cyclonedds` must compile against the **C library**. There is **no** `cyclonedds` Homebrew formula on macOS.

**macOS — build from source (recommended):**

```bash
cd /path/to/topsun_dimos
./bin/install-cyclonedds
# Copy the export lines the script prints (default: ~/.local/dimos-cyclonedds).
# Do not build under a path with spaces in the name — CycloneDDS CMake will fail.
export CYCLONEDDS_HOME="$HOME/.local/dimos-cyclonedds"
export DYLD_LIBRARY_PATH="$CYCLONEDDS_HOME/lib:${DYLD_LIBRARY_PATH:-}"
uv sync --extra go2-sim
```

Requires `cmake` and Xcode CLT (`xcode-select --install`). Same steps as [unitree_sdk2_python](https://github.com/unitreerobotics/unitree_sdk2_python#faq).

**macOS / Linux — Nix (no sudo):** see [docs/usage/transports/dds.md](../usage/transports/dds.md).

**Linux apt:** `sudo apt install cyclonedds-dev` then set `CYCLONEDDS_HOME` — see dds.md.

If `uv sync` fails with `Could not locate cyclonedds`, export `CYCLONEDDS_HOME` **before** syncing.

At runtime, CycloneDDS uses **domain id 1**. DimOS picks **`lo0`** on macOS and **`lo`** on Linux
(official `simulate_python/config.py` uses `lo`, which does not exist on macOS).

If your repo path contains **spaces** (e.g. `New project 2`), the launcher copies Go2 MJCF
meshes to `~/.local/dimos-mujoco-work/` automatically — MuJoCo cannot load assets from paths with spaces.

**macOS note:** DimOS uses its own DDS bridge (`dimos_bridge.py`) instead of importing
`unitree_sdk2py_bridge` directly, because upstream `RecurrentThread` relies on Linux
`timerfd_create` (not available on macOS).

## CLI

| Flag | Default | Meaning |
|------|---------|---------|
| `--simulation` | off | Enable MuJoCo |
| `--mujoco-backend unitree` | `unitree` | Official Unitree sim + DDS |
| `--mujoco-backend dimos` | | Legacy DimOS sim (perception-rich) |
| `--mujoco-room stairs` | | `scene_dimos_stairs.xml` when vendored |

```bash
# Stairs locomotion (ONNX + Sport SHM mirror; no lidar/video in unitree backend)
dimos --simulation run unitree-go2-stairs

# Full perception stack (legacy sim)
dimos --simulation --mujoco-backend dimos run unitree-go2-stairs
```

## Architecture

```mermaid
flowchart LR
  DimOS[GO2Connection] -->|cmd_vel + SPORT_MOD SHM| Launcher[dimos launcher.py]
  Launcher --> ONNX[Go1 ONNX policy]
  ONNX --> MJ[Unitree Go2 MJCF]
  Launcher --> DDS[DDS LowState / SportModeState]
  DimOS -->|odom SHM| Launcher
```

- **Locomotion**: Unitree sim is low-level only; DimOS runs the same Go1 ONNX policy as legacy sim, reading velocity and stair Sport gains from shared memory.
- **State**: Official bridge publishes `rt/sportmodestate` and `rt/lowstate`; odometry for DimOS modules is written to SHM from `qpos`.
- **Perception**: The Unitree launcher publishes synthetic lidar (MuJoCo raycast or depth cameras on a welded sensor rig) and RGB video into the same SHM path as DimOS MuJoCo, so `VoxelGridMapper` / Rerun `global_map` work.

**Navigation goals**: Rerun alone does not send click goals. Use the WebSocket dashboard (started with the stack) at **http://127.0.0.1:7779** — click the map to set `goal_request`. The Rerun window is for 3D debug view.

Wait until the 2D map shows obstacles (or ~10 s after start) before clicking a goal. If you click too early, the goal is **queued** and planning starts automatically when the first `global_costmap` arrives.

## Stairs scene

`dimos/simulation/unitree_mujoco/scenes/scene_dimos_stairs.xml` includes Unitree `go2.xml` plus 10 treads (15 cm riser) aligned with `build_scene_stairs.py`. Start pose: `--mujoco-start-pos 2.5,0.0` (blueprint default).

## Low-level / SDK2 examples

With the launcher running, you can also send `rt/lowcmd` from `third_party/unitree_mujoco/example/python/` (stand, walk) on domain 1 — DimOS ONNX and external LowCmd must not fight; the DimOS launcher ignores external LowCmd while active.
