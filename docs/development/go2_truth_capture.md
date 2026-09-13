# Go2-W Truth Trajectory Capture

This is the field workflow for collecting a human-teleoperated truth DB that can
later be used as the benchmark trajectory for AI-generated patrol code.

## What We Capture

The truth source is the DimOS memory2 SQLite DB produced by `Go2Memory` inside
the existing `unitree-go2-memory` blueprint.

Minimum required streams:

- `odom`: robot trajectory; this is the main truth trajectory for comparison.
- `lidar`: replay sensor context.
- `color_image`: replay visual context.

Rerun `.rrd` recordings are optional visual evidence. They are not the truth
source for benchmark assertions.

## Mac GUI Workflow

Double-click this file from Finder:

```text
Go2TruthCapture.command
```

The app opens a small capture window.

1. Enter the Go2-W IP address.
2. Keep `Route` as `office_loop`, or change it to a meaningful route label.
3. Choose an output directory. Default: `data/truth/go2`.
4. Click `Start Recording`.
5. Use the handheld remote controller to drive Go2-W around the office loop.
6. Click `Stop and Check`.

The output files look like this:

```text
data/truth/go2/go2_office_loop_<timestamp>.db
data/truth/go2/go2_office_loop_<timestamp>.manifest.json
data/truth/go2/go2_office_loop_<timestamp>.check.json
```

The `.db` file is the truth dataset for replay benchmarks. The `.manifest.json`
records operator, route, robot IP, command, and timing metadata. The
`.check.json` records whether required streams and minimum counts were present.

## CLI Workflow

Find the robot IP:

```bash
uv run dimos go2tool discover --lan
```

Capture:

```bash
uv run dimos go2tool truth capture \
  --robot-ip 192.168.123.161 \
  --route office_loop \
  --viewer rerun \
  --min-duration-s 180 \
  --min-odom 1000 \
  --min-lidar 300 \
  --min-images 300
```

When the command is running, drive one complete route with the remote
controller. Press `Ctrl+C` after the lap is complete. The command stops DimOS
and runs the same DB check as the GUI.

Check an existing DB:

```bash
uv run dimos go2tool truth check data/truth/go2/go2_office_loop_<timestamp>.db \
  --min-duration-s 180 \
  --min-odom 1000 \
  --min-lidar 300 \
  --min-images 300
```

## Acceptance For A Usable Truth DB

A DB can be used as the benchmark truth dataset when:

- `odom`, `lidar`, and `color_image` streams are all present.
- `odom` duration covers the full route.
- Stream counts exceed the thresholds for the route length.
- The route was driven by a human operator using the handheld controller.
- The `.manifest.json` and `.check.json` files are saved next to the DB.

For the AI patrol benchmark, compare AI-generated replay/sim trajectories
against the `odom` trajectory in this DB, then use `lidar` and `color_image` for
replay context and debugging evidence.
