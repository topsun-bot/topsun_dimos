# Navigation Obstacle MVP

This package stages local obstacle behaviors that should not yet change the
global navigation stack. The first behavior is threshold crossing: low obstacles
that the current planner would treat as blocked can be converted into a
bounded physical maneuver when they match conservative size and alignment
limits.

## Threshold Crossing

The mechanism separates three concerns:

- `ThresholdCrossingPlanner` classifies the obstacle and returns a decision.
- `CrossingAction` describes the physical sequence: posture setup, slow
  approach, crossing velocity, stop, and posture restore.
- `ThresholdCrossingSkillContainer.cross_threshold()` exposes the behavior to
  MCP/agent control with `dry_run=True` by default.

Default limits:

- Ignore obstacles below `0.10 m`; normal navigation should handle them.
- Cross obstacles from `0.10 m` up to `0.18 m`.
- Reject obstacles wider than `0.45 m`, poorly aligned, or without side
  clearance.

Example dry run:

```bash
dimos mcp call cross_threshold \
  --arg height_m=0.12 \
  --arg width_m=0.16 \
  --arg distance_m=0.35 \
  --arg lateral_clearance_m=0.20
```

Execution requires a blueprint that includes both a Go2 connection and
`ThresholdCrossingSkillContainer`, with its `cmd_vel` output connected to the
robot connection.

Direct MCP smoke shape:

```bash
dimos run unitree-go2-basic threshold-crossing-skill-container mcp-server
```
