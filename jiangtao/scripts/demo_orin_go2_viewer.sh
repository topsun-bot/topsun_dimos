#!/usr/bin/env bash
# 在 Mac 上连接 Orin 的 Rerun 数据和点击/WASD 控制服务。
#
# 用法：
#   bash jiangtao/scripts/demo_orin_go2_viewer.sh
#   bash jiangtao/scripts/demo_orin_go2_viewer.sh 192.168.110.127

set -euo pipefail

ORIN_HOST="${1:-${DIMOS_ORIN_VIEW_HOST:-192.168.110.127}}"
VIEWER_BIN="${DIMOS_VIEWER_BIN:-}"

if [[ -z "$VIEWER_BIN" ]]; then
    if command -v dimos-viewer >/dev/null 2>&1; then
        VIEWER_BIN="$(command -v dimos-viewer)"
    elif [[ -x "$HOME/.local/bin/dimos-viewer" ]]; then
        VIEWER_BIN="$HOME/.local/bin/dimos-viewer"
    else
        echo "dimos-viewer was not found in PATH or $HOME/.local/bin." >&2
        exit 1
    fi
fi

for port in 9877 3030; do
    if ! nc -z -w 2 "$ORIN_HOST" "$port"; then
        echo "Orin service $ORIN_HOST:$port is unavailable." >&2
        exit 1
    fi
done

echo "Rerun data:   rerun+http://$ORIN_HOST:9877/proxy"
echo "Viewer input: ws://$ORIN_HOST:3030/ws"

exec "$VIEWER_BIN" \
    --connect "rerun+http://$ORIN_HOST:9877/proxy" \
    --ws-url "ws://$ORIN_HOST:3030/ws"
