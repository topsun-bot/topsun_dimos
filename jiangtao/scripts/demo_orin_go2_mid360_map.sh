#!/usr/bin/env bash
# 录制 Mid360 同源导航数据并导出带 profile 的预地图。

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
ACTION="${1:-help}"
RECORD_ROOT="${DIMOS_MID360_RECORD_ROOT:-$REPO_ROOT/jiangtao/artifacts/mid360/map-recordings}"
SENSOR_PROFILE="mid360_pointlio_v1"
EXTRINSIC_VERSION="go2_orin_navigation_20260813_v1"
VOXEL_SIZE="${DIMOS_MID360_MAP_VOXEL_SIZE:-0.05}"

setup_open3d_preload() {
    local libc10="$REPO_ROOT/.venv/lib/python3.12/site-packages/torch/lib/libc10.so"
    local libgomp=/lib/aarch64-linux-gnu/libgomp.so.1
    local libgl=/lib/aarch64-linux-gnu/libGLdispatch.so.0
    for library in "$libc10" "$libgomp" "$libgl"; do
        if [[ ! -f "$library" ]]; then
            echo "Missing required preload library: $library" >&2
            exit 1
        fi
    done
    export LD_PRELOAD="$libc10:$libgomp:$libgl${LD_PRELOAD:+:$LD_PRELOAD}"
}

usage() {
    cat <<'EOF'
Usage:
  demo_orin_go2_mid360_map.sh record-static [session-name]
  demo_orin_go2_mid360_map.sh record-t2     [session-name]
  demo_orin_go2_mid360_map.sh record-wasd   [session-name]
  demo_orin_go2_mid360_map.sh summary       <recording.db>
  demo_orin_go2_mid360_map.sh analyze       <recording.db> [min-radius-m] [min-yaw-span-deg]
  demo_orin_go2_mid360_map.sh export        <recording.db> [map-id]

record-static starts a no-control blueprint for stationary or hand-push checks.
record-t2 automatically stops a no-control recording after T2 motion and settling pass.
record-wasd starts the full Mid360 navigation stack; keep the physical remote ready.
Stop recording with Ctrl-C, then run summary and export.
Use analyze after T2 to verify timestamp, pose, and cloud/odom metadata gates.
EOF
}

timestamped_session() {
    local requested="${1:-}"
    if [[ -n "$requested" ]]; then
        printf '%s\n' "$requested"
    else
        date '+%Y%m%d-%H%M%S'
    fi
}

record() {
    local mode="$1"
    local session
    session="$(timestamped_session "${2:-}")"
    local out_dir="$RECORD_ROOT/$session"
    local db_path="$out_dir/navigation.db"
    local pcap_path="$out_dir/raw-mid360.pcap"
    local blueprint="unitree-go2-mid360-map-record-validation"
    if [[ "$mode" == "wasd" ]]; then
        blueprint="unitree-go2-mid360-map-record"
    fi
    mkdir -p "$out_dir"
    printf 'Recording directory: %s\n' "$out_dir"
    printf 'Raw Mid360 PCAP: %s\n' "$pcap_path"
    printf 'After stopping, run:\n  %q summary %q\n  %q export %q %q\n' \
        "$0" "$db_path" "$0" "$db_path" "$session"
    DIMOS_GO2_NAVIGATION_SOURCE=mid360 \
    DIMOS_MID360_BLUEPRINT="$blueprint" \
    DIMOS_MID360_RECORDING_DB="$db_path" \
    DIMOS_MID360_PCAP_PATH="$pcap_path" \
        exec "$REPO_ROOT/jiangtao/scripts/demo_orin_go2_mid360_run.sh"
}

record_t2() {
    local session
    session="$(timestamped_session "${1:-}")"
    local out_dir="$RECORD_ROOT/$session"
    local db_path="$out_dir/navigation.db"
    local timeout_s="${DIMOS_MID360_T2_TIMEOUT_S:-180}"
    local min_radius_m="${DIMOS_MID360_T2_MIN_RADIUS_M:-0.5}"
    local min_yaw_deg="${DIMOS_MID360_T2_MIN_YAW_DEG:-20}"
    local settle_s="${DIMOS_MID360_T2_SETTLE_S:-3}"
    local recorder_pid=""

    stop_recorder() {
        if [[ -n "$recorder_pid" ]] && kill -0 "$recorder_pid" 2>/dev/null; then
            # SIGTERM is handled gracefully by DimOS and is not inherited as
            # ignored by background jobs in a non-interactive shell.
            kill -TERM "$recorder_pid" 2>/dev/null || true
            wait "$recorder_pid" || true
        fi
        recorder_pid=""
    }
    trap stop_recorder EXIT

    printf 'T2 automatic gate: move >=%s m, rotate >=%s deg, settle %s s, timeout %s s\n' \
        "$min_radius_m" "$min_yaw_deg" "$settle_s" "$timeout_s"
    "$0" record-static "$session" &
    recorder_pid=$!

    set +e
    "$REPO_ROOT/.venv/bin/python" -m \
        dimos.robot.unitree.go2.mid360_recording_analysis \
        "$db_path" \
        --wait-for-motion \
        --expected-min-radius-m "$min_radius_m" \
        --expected-min-yaw-span-deg "$min_yaw_deg" \
        --motion-timeout-s "$timeout_s" \
        --motion-settle-window-s "$settle_s"
    local monitor_rc=$?
    set -e

    stop_recorder
    trap - EXIT
    if [[ ! -f "$db_path" ]]; then
        echo "T2 recorder did not create $db_path" >&2
        return 1
    fi

    set +e
    "$0" analyze "$db_path" "$min_radius_m" "$min_yaw_deg"
    local analysis_rc=$?
    set -e
    if [[ "$monitor_rc" -ne 0 ]]; then
        echo "T2 motion gate timed out or failed (rc=$monitor_rc)." >&2
        return "$monitor_rc"
    fi
    if [[ "$analysis_rc" -ne 0 ]]; then
        echo "T2 final metadata analysis failed (rc=$analysis_rc)." >&2
        return "$analysis_rc"
    fi
    echo "T2 automatic metadata gates passed. Complete the Viewer manual checks."
}

require_db() {
    local db_path="$1"
    if [[ ! -f "$db_path" ]]; then
        echo "Recording database does not exist: $db_path" >&2
        exit 1
    fi
}

case "$ACTION" in
    record-static)
        record static "${2:-}"
        ;;
    record-t2)
        record_t2 "${2:-}"
        ;;
    record-wasd)
        record wasd "${2:-}"
        ;;
    summary)
        DB_PATH="${2:-}"
        require_db "$DB_PATH"
        exec "$REPO_ROOT/.venv/bin/dimos" mem summary "$DB_PATH"
        ;;
    analyze)
        DB_PATH="${2:-}"
        require_db "$DB_PATH"
        MIN_RADIUS="${3:-0.0}"
        MIN_YAW_SPAN="${4:-0.0}"
        exec "$REPO_ROOT/.venv/bin/python" -m \
            dimos.robot.unitree.go2.mid360_recording_analysis \
            "$DB_PATH" \
            --expected-min-radius-m "$MIN_RADIUS" \
            --expected-min-yaw-span-deg "$MIN_YAW_SPAN"
        ;;
    export)
        DB_PATH="${2:-}"
        require_db "$DB_PATH"
        setup_open3d_preload
        DEFAULT_MAP_ID="$(basename "$(dirname "$DB_PATH")")"
        MAP_ID="${3:-$DEFAULT_MAP_ID}"
        OUT_DIR="${DIMOS_MID360_MAP_OUTPUT_DIR:-$(dirname "$DB_PATH")}"
        MANIFEST_PATH="$OUT_DIR/mid360-preprocessing-manifest.json"
        mkdir -p "$OUT_DIR"
        "$REPO_ROOT/.venv/bin/python" - "$VOXEL_SIZE" "$MANIFEST_PATH" <<'PY'
import json
from pathlib import Path
import sys

from dimos.robot.unitree.go2.mid360_map_profile import (
    build_mid360_preprocessing_manifest,
)

manifest = build_mid360_preprocessing_manifest(map_voxel_size_m=float(sys.argv[1]))
Path(sys.argv[2]).write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
PY
        cd "$OUT_DIR"
        exec "$REPO_ROOT/.venv/bin/dimos" map global "$DB_PATH" \
            --lidar lidar \
            --frame world \
            --voxel "$VOXEL_SIZE" \
            --device CPU:0 \
            --pgo-tol 0.30 \
            --export \
            --map-id "$MAP_ID" \
            --sensor-profile "$SENSOR_PROFILE" \
            --extrinsic-version "$EXTRINSIC_VERSION" \
            --preprocessing-manifest "$MANIFEST_PATH" \
            --out "$OUT_DIR/navigation.rrd" \
            --no-gui
        ;;
    help|-h|--help)
        usage
        ;;
    *)
        usage >&2
        exit 2
        ;;
esac
