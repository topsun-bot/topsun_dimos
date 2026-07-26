---
title: "Navigation diagnostics"
---

Navigation diagnostics records the Go2 planning and command chain without adding
a diagnostics module or new stream subscriptions. Each existing producer writes
to its own bounded, asynchronous trace under:

```text
<run-dir>/navigation/
```

Tracing is disabled by default. The available levels are:

- `off`: no queue, writer thread, trace directory, or additional subscription.
- `summary`: navigation sessions, plans, low-rate odometry and control summaries.
- `full`: full-rate planner odometry and controls, command-chain events, and
  event-triggered costmap snapshots.
- `forensic`: `full` plus bounded point-cloud ROI samples. It also requires
  `--navigation-trace-forensic-ack`.

Start a run with full tracing:

```bash
dimos --navigation-trace-level full run unitree-go2
```

If a queue, byte budget, writer, directory, or free-space check fails, only the
affected producer's diagnostics is degraded or disabled. Navigation and command
publication continue. Credentials in the command, configuration, and event text
are redacted.

After the run has stopped, generate the report:

```bash
dimos nav analyze <run-dir>
```

Use `--session <navigation-session-id>` to analyze one goal, or `--open-rerun`
to open the generated Rerun recording. The command refuses to analyze a live
run so report generation cannot compete with robot control.

Each report contains:

- `summary.json`: metrics, integrity, dropped-data windows, evidence levels,
  command-chain matching, and cautious root-cause classification.
- `report.md`: a human-readable summary and evidence limits.
- `plots/`: planned versus odometry-estimated trajectory, cross-track error,
  heading and commands, latency, costmap/path/point-cloud overlay, and obstacle
  timeline.
- `trace.rrd`: paths, odometry estimate, controls, costmaps, and available
  point-cloud evidence.

Odometry is a robot pose estimate, not external ground truth. A successful
WebRTC send is evidence that the command entered the send path, not proof that
the robot executed it.

## Stationary Go2 validation

The real-robot gate is read-only: do not call `move`, `navigate`, patrol, or
send an agent command during this run. Keep credentials in environment
variables; never put them in a shell history entry, a run-directory override,
or a report:

```bash
export UNITREE_USERNAME='...'
export UNITREE_PASSWORD='...'
export UNITREE_SERIAL='...'
dimos --unitree-webrtc-method remote \
  --unitree-username "$UNITREE_USERNAME" \
  --unitree-password "$UNITREE_PASSWORD" \
  --unitree-serial "$UNITREE_SERIAL" \
  --unitree-region cn \
  --navigation-trace-level summary \
  run unitree-go2-relocalization-memory-agentic-deepseek \
  --disable security-module \
  -o relocalizationmodule.map_file=recording_go2
```

In another terminal, capture for ten minutes with the read-only external
capture (it does not create a publishing transport):

```bash
uv run python -m dimos.navigation.diagnostics.static_capture \
  /tmp/go2-stationary.json --duration-sec 600 --check
```

`--check` prints the individual gate checks and exits non-zero if the capture
was interrupted, shorter than ten minutes, lacked map/costmap data, contained a
non-zero command, or exceeded the configured odometry envelope.

Stop the run normally, then analyze its run directory:

```bash
dimos nav analyze <run-dir>
```

The stationary gate passes only when odometry remains within the agreed
millimetre-scale envelope, navigation and mux command streams contain no
non-zero command, maps continue arriving, and producer traces have no missing
footer or writer error. A run interrupted by power-down is evidence for
shutdown behavior only; it is not a completed ten-minute gate. For movement or
obstacle experiments, use a separate run after the stationary gate passes and
record the exact external action and time in the report notes.
