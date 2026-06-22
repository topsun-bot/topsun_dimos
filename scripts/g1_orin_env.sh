#!/usr/bin/env bash
# Shared G1 Orin runtime environment. Sourced by orin_run_*.sh — do not execute directly.

g1_orin_activate_env() {
    DIMOS_DIR="${DIMOS_DIR:-$HOME/topsun_dimos}"

    if command -v conda >/dev/null 2>&1; then
        conda deactivate 2>/dev/null || true
    fi
    unset LD_LIBRARY_PATH

    # shellcheck disable=SC1091
    source "$DIMOS_DIR/.venv/bin/activate"

    # Secrets (DASHSCOPE_API_KEY, etc.): copy .env.example → .env, never commit .env
    if [[ -f "$DIMOS_DIR/.env" ]]; then
        set -a
        # shellcheck disable=SC1091
        source "$DIMOS_DIR/.env"
        set +a
    fi

    export CYCLONEDDS_HOME="${CYCLONEDDS_HOME:-$HOME/cyclonedds/install}"
    UNITREE_LIB="${UNITREE_LIB:-$HOME/unitree_sdk2-main/thirdparty/lib/aarch64}"
    export LD_LIBRARY_PATH="$CYCLONEDDS_HOME/lib:$UNITREE_LIB"

    export LIDAR_HOST_IP="${LIDAR_HOST_IP:-192.168.123.164}"
    export LIDAR_IP="${LIDAR_IP:-192.168.123.120}"
    # Optional USB mic: run scripts/orin_test_mic.sh to find index, then e.g. export DIMOS_MIC_DEVICE_INDEX=2
    export DIMOS_MIC_DEVICE_INDEX="${DIMOS_MIC_DEVICE_INDEX:-}"
    # G1 body speaker: SetVolume 0–100; PCM gain boosts CosyVoice before PlayStream (1.0 = off).
    export DIMOS_G1_SPEAKER_VOLUME="${DIMOS_G1_SPEAKER_VOLUME:-100}"
    export DIMOS_G1_SPEAKER_PCM_GAIN="${DIMOS_G1_SPEAKER_PCM_GAIN:-1.5}"
}

# Warn when RTC is wrong — DashScope / HuggingFace HTTPS will fail with CERTIFICATE_VERIFY_FAILED.
g1_orin_check_clock() {
    local year
    year="$(date +%Y)"
    if [[ "$year" -lt 2024 ]]; then
        echo "[g1_orin_env] ERROR: system clock looks wrong (year=${year})." >&2
        echo "[g1_orin_env] CosyVoice/Whisper HTTPS will fail. On laptop run:" >&2
        echo "  bash ~/topsun_dimos/scripts/orin_sync_time.sh" >&2
        echo "Or on Orin: sudo date -s 'YYYY-MM-DD HH:MM:SS'" >&2
        return 1
    fi
    return 0
}
