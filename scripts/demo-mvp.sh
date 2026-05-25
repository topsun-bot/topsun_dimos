#!/usr/bin/env bash
# Navigation obstacle MVP demo smoke (headless + fake LiDAR when GL unavailable).
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
export PYTHONUNBUFFERED=1
exec uv run python -m dimos.experimental.navigation_obstacle_mvp.tests.test_demo_smoke
