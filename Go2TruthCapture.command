#!/usr/bin/env bash
set -euo pipefail

REPO_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$REPO_DIR"

export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"

if ! command -v uv >/dev/null 2>&1; then
  osascript -e 'display dialog "Cannot find uv. Install uv first, or run this from a Terminal where uv is available." buttons {"OK"} default button "OK" with icon caution' >/dev/null
  exit 1
fi

uv run python -m dimos.robot.unitree.go2.cli.truth_capture_gui
