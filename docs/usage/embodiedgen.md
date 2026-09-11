# EmbodiedGen bridge

[EmbodiedGen](https://github.com/HorizonRobotics/EmbodiedGen) (Apache-2.0) generates sim-ready 3D assets — image/text → URDF + mesh + collision + physics — and can convert them to MuJoCo MJCF, Genesis MJCF, or Isaac USD.

DimOS does **not** vendor EmbodiedGen, its third-party weights, or its CUDA stack. This repo only ships a thin catalog + scene-compose helper, a `dimos embodiedgen` CLI, and an optional `@skill` module. Generate assets in a separate EmbodiedGen install, then drop the export into a DimOS path.

Use the Topsun fork when you want the same layout with project-specific patches:

```bash
git clone https://github.com/topsun-bot/EmbodiedGen.git
# upstream: https://github.com/HorizonRobotics/EmbodiedGen
```

Follow that repo's `install.sh` (GPU). DimOS CI never runs those models.

## Capability matrix

| Capability | Reuse DimOS | Wrap EmbodiedGen | Skip |
|------------|-------------|------------------|------|
| MuJoCo scene load (`MujocoEngine`, Go2/G1 `load_scene_xml`) | Yes — compose MJCF/URDF into the existing room XML | — | — |
| Genesis entity load (`GenesisSimulator._load_entities`) | Yes — emit `{type, path, params}` for `mjcf`/`urdf` | — | — |
| Isaac Sim USD (`IsaacSimulator(open_usd=...)`) | Discover `.usd` if present | Convert with EmbodiedGen `MeshtoUSDConverter` | Auto-inject into a live Isaac app |
| SAPIEN / Isaac Gym / PyBullet | — | Use the generated `.urdf` in those engines | DimOS does not ship these engines |
| Image/text → 3D, rooms, layouts, 3DGS, affordance | — | Run `img3d-cli` / `text3d-cli` / `room-cli` / `layout-cli` in the EmbodiedGen env | In-process GPU generation |
| DimSim / Unity scenes | — | — | No EmbodiedGen export mapping |
| Spatial memory, navigation, public shims | Unchanged | — | — |

## Install notes (EmbodiedGen, optional)

EmbodiedGen is **not** a DimOS extra. If you want generation (not just loading):

```bash
git clone https://github.com/topsun-bot/EmbodiedGen.git
cd EmbodiedGen
# see that repo: conda env + bash install.sh basic
img3d-cli --image_path apps/assets/example_image/sample_00.jpg \
    --output_root outputs/imageto3d
```

Typical export layout (do not copy this tree into DimOS):

```
outputs/imageto3d/sample_00/
  result/   # *.urdf, mesh.obj / .glb, 3DGS .ply
  mjcf/     # MeshtoMJCFConverter → MuJoCo / Genesis
  usd/      # MeshtoUSDConverter → Isaac Sim
```

Point DimOS at the export root (any one of these):

```bash
export EMBODIEDGEN_EXPORT_DIR=$PWD/outputs/imageto3d
export EMBODIEDGEN_ROOT=$PWD                    # optional, for `dimos embodiedgen run-cli`
# also accepted: DIMOS_EMBODIEDGEN_EXPORT_DIR, --embodiedgen-export-dir
```

Registered copies live in `~/.local/share/dimos/embodiedgen` (override with `EMBODIEDGEN_CATALOG_DIR`).

## Copy-paste: load a generated asset into MuJoCo

From a DimOS checkout, using the tiny fixture that stands in for an EmbodiedGen export (no GPU):

```bash
# 1) Register the export (or use --export-dir against your real outputs/)
dimos embodiedgen register dimos/simulation/embodiedgen/fixtures/eg_box --name eg_box

# 2) Inspect
dimos embodiedgen list
dimos embodiedgen scene 'eg_box@0.5,0,0.3' --engine mujoco

# 3) Start an existing Unitree sim blueprint with the asset in the room
dimos --simulation --mujoco-embodiedgen-assets='eg_box@0.5,0,0.3' run unitree-go2
```

Genesis (same catalog; DimOS already loads `mjcf`/`urdf` entities):

```bash
dimos embodiedgen scene eg_box --engine genesis
# → [{'type': 'mjcf', 'path': '.../eg_box.xml', 'params': {'pos': [0.5, 0.0, 0.3]}}]
```

Optional agent skills (compose `EmbodiedGenSkill` into an agentic blueprint, or `dimos run … embodied-gen-skill`):

```bash
dimos mcp call list_embodiedgen_assets
dimos mcp call load_embodiedgen_asset --arg name=eg_box --arg x=0.5 --arg y=0 --arg z=0.3
```

`load_embodiedgen_asset` does not hot-reload MuJoCo. It resolves the file and prints the `--mujoco-embodiedgen-assets` restart flag. Bodies are attached when the sim process calls `load_scene_xml`.

## Config flags

| Flag / env | Purpose |
|------------|---------|
| `--embodiedgen-root` / `EMBODIEDGEN_ROOT` | Optional checkout or venv prefix (CLI discovery only) |
| `--embodiedgen-export-dir` / `EMBODIEDGEN_EXPORT_DIR` | Directory to scan for exports |
| `--embodiedgen-catalog-dir` / `EMBODIEDGEN_CATALOG_DIR` | Override the DimOS catalog |
| `--mujoco-embodiedgen-assets` | `name` or `name@x,y,z` (`;` or `,` between tokens) |

Core DimOS does not import `embodied_gen`. `dimos embodiedgen run-cli` is a subprocess wrapper and exits if `img3d-cli` is not on `PATH`.
