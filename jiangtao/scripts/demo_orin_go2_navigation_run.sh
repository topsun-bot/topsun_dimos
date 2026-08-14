#!/usr/bin/env bash
# 通过 DIMOS_GO2_NAVIGATION_SOURCE 在启动时明确选择 Go2 或 Mid360 数据源。

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
SOURCE="${DIMOS_GO2_NAVIGATION_SOURCE:-go2}"

case "$SOURCE" in
    go2)
        exec "$REPO_ROOT/jiangtao/scripts/demo_orin_go2_run.sh" "${1:-wired}"
        ;;
    mid360)
        exec "$REPO_ROOT/jiangtao/scripts/demo_orin_go2_mid360_run.sh"
        ;;
    *)
        echo "DIMOS_GO2_NAVIGATION_SOURCE must be 'go2' or 'mid360', got: $SOURCE" >&2
        exit 2
        ;;
esac
