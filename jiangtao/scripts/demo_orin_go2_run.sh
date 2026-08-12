#!/usr/bin/env bash
# 在 Orin 上以前台方式启动 Go2 基础导航栈。
#
# 用法：
#   bash jiangtao/scripts/demo_orin_go2_run.sh wired
#   bash jiangtao/scripts/demo_orin_go2_run.sh wifi
#
# wired: Orin go2eth -> 192.168.123.161
# wifi:  Orin wlan0  -> 192.168.110.70

set -euo pipefail

MODE="${1:-wired}"
ORIN_VIEW_HOST="${DIMOS_ORIN_VIEW_HOST:-192.168.110.127}"

case "$MODE" in
    wired)
        ROBOT_IP="${GO2_WIRED_IP:-192.168.123.161}"
        EXPECTED_INTERFACE="go2eth"
        ;;
    wifi)
        ROBOT_IP="${GO2_WIFI_IP:-192.168.110.70}"
        EXPECTED_INTERFACE="wlan0"
        ;;
    *)
        echo "Usage: $0 {wired|wifi}" >&2
        exit 2
        ;;
esac

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$REPO_ROOT"

if [[ ! -x .venv/bin/dimos ]]; then
    echo "Missing $REPO_ROOT/.venv/bin/dimos; initialize the Orin environment first." >&2
    exit 1
fi

STATUS_OUTPUT="$(.venv/bin/dimos status 2>&1 || true)"
if grep -q "Run ID:" <<<"$STATUS_OUTPUT"; then
    echo "A DimOS instance is already running; refusing to start a second Go2 WebRTC session." >&2
    echo "$STATUS_OUTPUT"
    exit 1
fi

ROUTE="$(ip route get "$ROBOT_IP" 2>/dev/null | head -n 1 || true)"
echo "Selected mode:      $MODE"
echo "Go2 target:         $ROBOT_IP"
echo "Kernel route:       $ROUTE"
echo "Viewer host:        $ORIN_VIEW_HOST"

if [[ "$ROUTE" != *" dev $EXPECTED_INTERFACE "* ]]; then
    echo "Expected route via $EXPECTED_INTERFACE, but the kernel selected another route." >&2
    exit 1
fi

if ! nc -z -w 2 "$ROBOT_IP" 9991; then
    echo "Go2 WebRTC signaling is unavailable at $ROBOT_IP:9991." >&2
    exit 1
fi

if [[ -z "${UNITREE_AES_128_KEY:-}" ]]; then
    read -r -s -p "Go2 AES key: " UNITREE_AES_128_KEY
    echo
fi
export UNITREE_AES_128_KEY
export UNITREE_WEBRTC_METHOD=local

LIBGOMP=/usr/lib/aarch64-linux-gnu/libgomp.so.1
LIBGLDISPATCH=/lib/aarch64-linux-gnu/libGLdispatch.so.0
for library in "$LIBGOMP" "$LIBGLDISPATCH"; do
    if [[ ! -f "$library" ]]; then
        echo "Missing required preload library: $library" >&2
        exit 1
    fi
done
export LD_PRELOAD="$LIBGOMP:$LIBGLDISPATCH${LD_PRELOAD:+:$LD_PRELOAD}"

echo "Starting unitree-go2 in the foreground. Keep this terminal open."
echo "Mac viewer command:"
echo "  dimos-viewer --connect rerun+http://$ORIN_VIEW_HOST:9877/proxy --ws-url ws://$ORIN_VIEW_HOST:3030/ws"

exec .venv/bin/dimos \
    --robot-ip "$ROBOT_IP" \
    --unitree-webrtc-method local \
    --listen-host "$ORIN_VIEW_HOST" \
    --rerun-host "$ORIN_VIEW_HOST" \
    --rerun-open none \
    --no-rerun-web \
    --obstacle-avoidance \
    --no-free-avoid \
    run unitree-go2
