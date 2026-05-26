#!/usr/bin/env bash
set -euo pipefail

PACK="${1:-demo_office}"
BLUEPRINT="${2:-unitree-go2-agentic}"

dimos stop 2>/dev/null || true
dimos landmarks import "$PACK" --force
dimos --replay run "$BLUEPRINT" --landmark-pack="$PACK" --daemon

sleep 15   # wait for modules to be ready

echo "=== Landmarks ==="
dimos mcp call query_landmarks --arg query=all

echo "=== Navigate to kitchen ==="
dimos tell -t 120 "去厨房"

echo "=== Navigate to computer ==="
dimos tell -t 120 "去找电脑"

echo "=== Stop ==="
dimos tell "停止，恢复站立"

echo "Done. Run 'dimos stop' when finished."
