# Unitree Go2 Navigation (NoMaD)

Go2 navigation experiments on DimOS: subscribe to live `color_image`, run [NoMaD](https://general-navigation-models.github.io/nomad/) exploration diffusion, publish robot-centric local waypoints, and optionally publish a debug **traversability** grid.

## Architecture

```
unitree_go2_basic
    color_image  ──►  NoMaDTrajectoryLocalPlannerModule
                           │
                           ├─ TrajectoryLocalPlannerModule (shared DimOS I/O + route selection)
                           ├─ NoMaDEngine (goal-masked diffusion, N samples)
                           ├─ local_waypoints  (Path, base_link)
                           │       └─► WaypointFollowerModule ──► cmd_vel ──► Go2
                           ├─ candidate_paths  (list[Path], all samples)
                           └─ traversability_map  (OccupancyGrid, base_link)
```

| Stream | Type | Description |
|--------|------|-------------|
| `color_image` | `sensor_msgs.Image` | RGB from Go2 (auto-connected) |
| `local_waypoints` | `nav_msgs.Path` | Selected egocentric local route in `base_link` |
| `cmd_vel` | `geometry_msgs.Twist` | Velocity commands from `WaypointFollowerModule` |
| `candidate_paths` | `list[nav_msgs.Path]` | All diffusion trajectory samples in `base_link` |
| `traversability_map` | `nav_msgs.OccupancyGrid` | Debug egocentric traversability in `base_link` |

**Grid semantics (ROS cost convention):**

- `0` — highly traversable (many diffusion trajectories pass through)
- `1–99` — partial consensus
- `100` — in range but no trajectory visited the cell
- `-1` — reserved (unused in current rasterizer)

## Prerequisites

1. DimOS environment: `uv sync --all-extras --no-extra dds`
2. [visualnav-transformer](https://github.com/robodhruv/visualnav-transformer) cloned and set up (`diffusion_policy`, checkpoints)
3. Download `nomad.pth` into `visualnav-transformer/deployment/model_weights/`
4. **NoMaD Python deps** in the **same** `.venv` as DimOS — `uv pip install -r examples/nav-go2/requirements-nomad.txt` and **[DEPENDENCIES.md](./DEPENDENCIES.md)**

```bash
export VISUALNAV_ROOT=/path/to/visualnav-transformer
# optional:
export NOMAD_MODEL_CONFIG=$VISUALNAV_ROOT/train/config/nomad.yaml
```

Edit `examples/nav-go2/config/nomad_nav.yaml` to set `checkpoint_path` (and optionally `visualnav_root`). Paths may be relative to that file.

Use the same Python environment that has `torch`, `diffusers>=0.27`, `vint_train`, and `diffusion_policy` installed (do **not** use upstream `diffusers==0.11.1` in the DimOS venv).

## Go2 stack

This example always uses `unitree_go2_basic`. NoMaD subscribes to `color_image`,
which is provided by the Go2 connection inside that blueprint, and the bundled
stack also wires lidar / odom, visualization, WebSocket vis, and clock sync.

CLI parsing lives in `go2_nomad_nav.py` (`parse_nav_go2_config`, `NavGo2RunConfig`).

## Run

```bash
# Replay (no robot)
uv run python examples/nav-go2/go2_nomad_nav.py --replay --viewer rerun

# MuJoCo simulation
uv run python examples/nav-go2/go2_nomad_nav.py --simulation --viewer rerun

# Real Go2
export ROBOT_IP=192.168.123.161
uv run python examples/nav-go2/go2_nomad_nav.py --viewer rerun

# CLI overrides (paths and inference_hz are in config/nomad_nav.yaml)
uv run python examples/nav-go2/go2_nomad_nav.py --replay \
  --visualnav-root ~/visualnav-transformer \
  --num-samples 8
```

This example runs through the script entrypoint above, not `dimos --simulation
run ...`. The `--simulation` flag maps the Go2 connection to MuJoCo.

## Files

| File | Role |
|------|------|
| `go2_nomad_nav.py` | Blueprint entry script and argument parsing (`NavGo2RunConfig`) |
| `config/nomad_nav.yaml` | NoMaD paths, inference, and follower (`control_*`) settings |
| `controller.py` | Pure-pursuit follower: `local_waypoints` → `cmd_vel` (path update gating) |
| `trajectory_local_planner_module.py` | Shared DimOS `Module` (subscribe/select/publish/debug-rasterize) |
| `engine/nomad/local_planner_module.py` | NoMaD adapter for `TrajectoryLocalPlannerModule` |
| `engine/nomad/inference.py` | NoMaD exploration inference wrapper |
| `engine/nomad/config.py` | NoMaD paths and inference parameters |
| `trajectory_inference.py` | Shared trajectory model result/protocol utilities |
| `trajectory_planner_config.py` | Shared local planner/debug-map config |
| `traversability_grid.py` | Trajectory samples → `OccupancyGrid` |

## How traversability is derived

NoMaD exploration mode (see upstream `deployment/src/explore.py`) samples `num_samples` future trajectories in the robot body frame. Cells along those polylines receive votes; higher agreement → lower cost (more traversable).

This is a **local, vision-based** traversability estimate — complementary to lidar `CostMapper` maps in `dimos/mapping/`.

## Adding another trajectory model

The DimOS stream/route-selection/debug-rasterization path is model-agnostic. To
add NavDP, keep `TrajectoryLocalPlannerModule` unchanged and add:

- `engine/navdp/config.py`: subclass `TrajectoryLocalPlannerConfig`
- `engine/navdp/inference.py`: implement `TrajectoryNavigationEngine`
- `engine/navdp/local_planner_module.py`: thin subclass that returns `NavDPEngine`

The engine should return `TrajectoryInferenceResult` with body-frame trajectory
samples shaped `(num_samples, num_steps, 2)`.

## Tests

```bash
cd examples/nav-go2 && uv run pytest test_traversability_grid.py -v
```

## Related

- [DEPENDENCIES.md](./DEPENDENCIES.md) — NoMaD 依赖安装与常见错误修复
- [mapping-go2](../mapping-go2/) — exploration + occupancy saving
- `dimos/navigation/replanning_a_star/` — A* planner on gradient costmaps
