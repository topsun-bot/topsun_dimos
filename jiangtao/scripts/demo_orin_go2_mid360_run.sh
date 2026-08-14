#!/usr/bin/env bash
# 在 Orin 上启动以 Mid360S + Point-LIO 为唯一导航数据源的 Go2 导航栈。
#
# 默认使用已在真机验证的有线拓扑，并将首次导航速度限制为原配置的 50%。
# 用法：
#   bash jiangtao/scripts/demo_orin_go2_mid360_run.sh
#   DIMOS_NERF_SPEED=0.7 bash jiangtao/scripts/demo_orin_go2_mid360_run.sh
#   DIMOS_MID360_BLUEPRINT=unitree-go2-mid360-relocalization-memory-agentic-deepseek \
#     DIMOS_RELOCALIZATION_MAP=/path/to/office.pc2.lcm \
#     bash jiangtao/scripts/demo_orin_go2_mid360_run.sh

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$REPO_ROOT"

detect_orin_view_host() {
    # SSH_CONNECTION 的第三列是本次 SSH 会话连接到的 Orin 地址，优先使用它，
    # 避免 DHCP 变化后继续绑定脚本中的旧地址。非 SSH 启动时退回 wlan0。
    if [[ -n "${SSH_CONNECTION:-}" ]]; then
        awk '{print $3}' <<<"$SSH_CONNECTION"
        return
    fi
    ip -4 -o addr show wlan0 scope global 2>/dev/null \
        | awk 'NR == 1 {split($4, address, "/"); print address[1]}'
}

GO2_IP="${GO2_WIRED_IP:-192.168.123.161}"
ORIN_LAN_IP="${DIMOS_ORIN_VIEW_HOST:-$(detect_orin_view_host)}"
POINTLIO_HOST_IP="${DIMOS_POINTLIO_HOST_IP:-192.168.123.18}"
POINTLIO_LIDAR_IP="${DIMOS_POINTLIO_LIDAR_IP:-192.168.123.20}"
NERF_SPEED="${DIMOS_NERF_SPEED:-0.5}"
VIEWER_MIN_HEIGHT="${DIMOS_MID360_VIEWER_MIN_HEIGHT_M:--0.05}"
VIEWER_MAX_HEIGHT="${DIMOS_MID360_VIEWER_MAX_HEIGHT_M:-1.50}"
GLOBAL_MAP_EMIT_EVERY="${DIMOS_MID360_GLOBAL_MAP_EMIT_EVERY:-10}"
BLUEPRINT="${DIMOS_MID360_BLUEPRINT:-unitree-go2-mid360}"
RELOCALIZATION_MAP="${DIMOS_RELOCALIZATION_MAP:-}"
RECORDING_DB="${DIMOS_MID360_RECORDING_DB:-}"
PCAP_PATH="${DIMOS_MID360_PCAP_PATH:-}"
PCAP_IFACE="${DIMOS_MID360_PCAP_IFACE:-go2eth}"
CONTROL_ENABLED=true
if [[ "$BLUEPRINT" == *validation* ]]; then
    CONTROL_ENABLED=false
fi

check_competing_navigation_stacks() {
    # Mid360 UDP, localization and robot motion must have exactly one owner.
    # A second vehicle stack can both consume the sensor stream and starve
    # Point-LIO long enough to trip the fail-closed odometry watchdog.
    local pattern
    pattern='livox_ros_driver2_node|nav_stack\.launch\.py|relocation_node|examples\.run_service|pointlio_native|voxel_ray_tracing'
    local conflicts
    conflicts="$(pgrep -af "$pattern" 2>/dev/null || true)"
    if [[ -z "$conflicts" ]]; then
        return 0
    fi

    echo "Another lidar/navigation stack is already running on this Orin:" >&2
    printf '%s\n' "$conflicts" >&2
    echo >&2
    echo "Stop the competing stack before starting DimOS. Known vehicle containers can be stopped with:" >&2
    echo "  docker stop -t 15 orin-brain-1 docker-navigation-1" >&2
    echo "The script will not stop them automatically because they may own active robot control." >&2
    return 1
}

check_competing_navigation_containers() {
    # Some vehicle services start their Livox process after the container has
    # already become healthy. A process-only preflight can therefore pass and
    # lose the Mid360 UDP socket a minute later.
    if ! command -v docker >/dev/null 2>&1; then
        return 0
    fi

    local containers
    containers="$(
        docker ps --format '{{.Names}} {{.Status}}' 2>/dev/null \
            | grep -E '^(orin-brain-1|docker-navigation-1) ' || true
    )"
    if [[ -z "$containers" ]]; then
        return 0
    fi

    echo "A known competing vehicle container is running:" >&2
    printf '%s\n' "$containers" >&2
    echo >&2
    echo "Stop it before starting DimOS:" >&2
    echo "  docker stop -t 15 orin-brain-1 docker-navigation-1" >&2
    echo "If orin-brain-1 has an automatic restart policy, disable it for this test:" >&2
    echo "  docker update --restart=no orin-brain-1" >&2
    return 1
}

if [[ ! -x .venv/bin/dimos ]]; then
    echo "Missing $REPO_ROOT/.venv/bin/dimos; initialize the Orin environment first." >&2
    exit 1
fi
if [[ -z "$ORIN_LAN_IP" ]]; then
    echo "Unable to determine the Orin viewer address." >&2
    echo "Set DIMOS_ORIN_VIEW_HOST to the address reachable from the Mac." >&2
    exit 1
fi

STATUS_OUTPUT="$(.venv/bin/dimos status 2>&1 || true)"
if grep -q "Run ID:" <<<"$STATUS_OUTPUT"; then
    echo "A DimOS instance is already running; refusing to open a second Go2 session." >&2
    echo "$STATUS_OUTPUT"
    exit 1
fi

check_competing_navigation_containers
check_competing_navigation_stacks

ROUTE_CHECKS=("$POINTLIO_LIDAR_IP go2eth")
if [[ "$CONTROL_ENABLED" == true ]]; then
    ROUTE_CHECKS+=("$GO2_IP go2eth")
fi
for pair in "${ROUTE_CHECKS[@]}"; do
    read -r target expected_interface <<<"$pair"
    route="$(ip route get "$target" 2>/dev/null | head -n 1 || true)"
    echo "Route to $target: $route"
    if [[ "$route" != *" dev $expected_interface "* ]]; then
        echo "Expected route to $target via $expected_interface." >&2
        exit 1
    fi
done

if [[ "$CONTROL_ENABLED" == true ]] && ! nc -z -w 2 "$GO2_IP" 9991; then
    echo "Go2 WebRTC signaling is unavailable at $GO2_IP:9991." >&2
    exit 1
fi
if ! ping -c 1 -W 1 "$POINTLIO_LIDAR_IP" >/dev/null; then
    echo "Mid360S is unreachable at $POINTLIO_LIDAR_IP." >&2
    exit 1
fi

RUN_OPTIONS=()
if [[ -n "$RECORDING_DB" ]]; then
    if [[ "$BLUEPRINT" != *map-record* ]]; then
        echo "DIMOS_MID360_RECORDING_DB requires a *map-record* blueprint." >&2
        exit 1
    fi
    mkdir -p "$(dirname "$RECORDING_DB")"
    RUN_OPTIONS+=("-o" "go2mid360navigationrecorder.db_path=$RECORDING_DB")
fi
if [[ "$BLUEPRINT" == *map-record* ]]; then
    if [[ -z "$PCAP_PATH" ]]; then
        echo "DIMOS_MID360_PCAP_PATH is required for a *map-record* blueprint." >&2
        exit 1
    fi
    if ! command -v tcpdump >/dev/null 2>&1; then
        echo "tcpdump is required for raw Mid360 recording." >&2
        exit 1
    fi
    mkdir -p "$(dirname "$PCAP_PATH")"
    probe_output="$(timeout 1 tcpdump -i "$PCAP_IFACE" -nn -c 1 -w /dev/null \
        "src host $POINTLIO_LIDAR_IP and udp" 2>&1 || true)"
    if grep -qiE "permission|operation not permitted|no such device" <<<"$probe_output"; then
        echo "Raw Mid360 capture preflight failed on $PCAP_IFACE: $probe_output" >&2
        echo "Grant tcpdump capture capability, then retry:" >&2
        echo "  sudo setcap cap_net_raw,cap_net_admin=eip \$(command -v tcpdump)" >&2
        exit 1
    fi
    RUN_OPTIONS+=(
        "-o" "mid360pcaprecorder.pcap_path=$PCAP_PATH"
        "-o" "mid360pcaprecorder.iface=$PCAP_IFACE"
        "-o" "mid360pcaprecorder.lidar_ip=$POINTLIO_LIDAR_IP"
    )
fi
if [[ -n "$RELOCALIZATION_MAP" ]]; then
    if [[ ! -f "$RELOCALIZATION_MAP" ]]; then
        echo "Relocalization map does not exist: $RELOCALIZATION_MAP" >&2
        exit 1
    fi
    if [[ ! -f "${RELOCALIZATION_MAP}.meta.json" ]]; then
        echo "Mid360 map profile is missing: ${RELOCALIZATION_MAP}.meta.json" >&2
        exit 1
    fi
    RUN_OPTIONS+=("-o" "relocalizationmodule.map_file=$RELOCALIZATION_MAP")
elif [[ "$BLUEPRINT" == *relocalization* ]]; then
    echo "DIMOS_RELOCALIZATION_MAP is required for blueprint: $BLUEPRINT" >&2
    exit 1
fi

if [[ "$CONTROL_ENABLED" == true && -z "${UNITREE_AES_128_KEY:-}" ]]; then
    read -r -s -p "Go2 AES key: " UNITREE_AES_128_KEY
    echo
fi
if [[ "$CONTROL_ENABLED" == true ]]; then
    export UNITREE_AES_128_KEY
fi
export UNITREE_WEBRTC_METHOD=local
export DIMOS_POINTLIO_HOST_IP="$POINTLIO_HOST_IP"
export DIMOS_POINTLIO_LIDAR_IP="$POINTLIO_LIDAR_IP"
export DIMOS_MID360_LIDAR_IP="$POINTLIO_LIDAR_IP"
export DIMOS_MID360_PCAP_IFACE="$PCAP_IFACE"
export DIMOS_MID360_VIEWER_MIN_HEIGHT_M="$VIEWER_MIN_HEIGHT"
export DIMOS_MID360_VIEWER_MAX_HEIGHT_M="$VIEWER_MAX_HEIGHT"
export DIMOS_MID360_GLOBAL_MAP_EMIT_EVERY="$GLOBAL_MAP_EMIT_EVERY"
# Orin 的 HDA 控制器可能在 SDL 探测本机声卡时卡在内核 rpm_resume；
# DimOS 的语音走机器人链路，不需要打开 Orin 本地声卡。
export SDL_AUDIODRIVER="${SDL_AUDIODRIVER:-dummy}"

LIBGOMP=/lib/aarch64-linux-gnu/libgomp.so.1
LIBGLDISPATCH=/lib/aarch64-linux-gnu/libGLdispatch.so.0
LIBC10="$(find "$REPO_ROOT/.venv/lib" -path '*/site-packages/torch/lib/libc10.so' -print -quit)"
for library in "$LIBGOMP" "$LIBGLDISPATCH" "$LIBC10"; do
    if [[ ! -f "$library" ]]; then
        echo "Missing required preload library: $library" >&2
        exit 1
    fi
done
# Jetson 的静态 TLS 槽位有限。子 worker 晚加载 OpenCV/Torch 时可能报
# "cannot allocate memory in static TLS block"，因此在 forkserver 启动前预加载。
export LD_PRELOAD="$LIBGOMP:$LIBGLDISPATCH:$LIBC10${LD_PRELOAD:+:$LD_PRELOAD}"

echo "Go2 control:        $GO2_IP"
echo "Point-LIO host:     $POINTLIO_HOST_IP"
echo "Mid360S:            $POINTLIO_LIDAR_IP"
echo "Navigation scaling: $NERF_SPEED (0.55 m/s x scale)"
echo "Viewer map height:   [$VIEWER_MIN_HEIGHT, $VIEWER_MAX_HEIGHT] m"
echo "Global map period:   every $GLOBAL_MAP_EMIT_EVERY lidar frames"
echo "Blueprint:          $BLUEPRINT"
echo "Relocalization map: ${RELOCALIZATION_MAP:-disabled}"
echo "Recording db:       ${RECORDING_DB:-disabled}"
echo "Raw Mid360 PCAP:    ${PCAP_PATH:-disabled}"
echo "Go2 control output:  $CONTROL_ENABLED"
echo "Mac viewer:"
echo "  dimos-viewer --connect rerun+http://$ORIN_LAN_IP:9877/proxy --ws-url ws://$ORIN_LAN_IP:3030/ws"

exec .venv/bin/dimos \
    --robot-ip "$GO2_IP" \
    --unitree-webrtc-method local \
    --viewer rerun \
    --listen-host "$ORIN_LAN_IP" \
    --rerun-host "$ORIN_LAN_IP" \
    --rerun-open none \
    --no-rerun-web \
    --nerf-speed "$NERF_SPEED" \
    run "$BLUEPRINT" \
    --disable security-module \
    "${RUN_OPTIONS[@]}"
