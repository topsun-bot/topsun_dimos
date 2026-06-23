#!/usr/bin/env bash
# G1 Orin idempotent setup (run on Orin).
#
#   bash ~/topsun_dimos/scripts/orin_setup.sh           # nix copy + sanity checks
#   bash ~/topsun_dimos/scripts/orin_setup.sh --swap    # also add 4G swap (pre-nix-build)
#   bash ~/topsun_dimos/scripts/orin_setup.sh --full    # fresh machine: CycloneDDS + uv sync
#
# Nav binaries: build on laptop with scripts/laptop_prebuild_nav_for_orin.sh (not on Orin).

set -euo pipefail

log() { echo "[$(date '+%H:%M:%S')] $*"; }

DO_SWAP=false
DO_FULL=false
for arg in "$@"; do
    case "$arg" in
        --swap) DO_SWAP=true ;;
        --full) DO_FULL=true ;;
        -h|--help)
            sed -n '2,8p' "$0"
            exit 0
            ;;
        *) log "Unknown option: $arg"; exit 1 ;;
    esac
done

DIMOS_DIR="${DIMOS_DIR:-$HOME/topsun_dimos}"
CYCLONEDDS_HOME="${CYCLONEDDS_HOME:-$HOME/cyclonedds/install}"
CUSTOM=/etc/nix/nix.custom.conf
MARKER="# dimos: allow nix copy from laptop"

YEAR=$(date +%Y)
if [[ "$YEAR" -lt 2020 ]]; then
    if [[ -n "${DIMOS_ORIN_DATE:-}" ]]; then
        sudo date -s "$DIMOS_ORIN_DATE"
    else
        log "WARNING: clock is $YEAR — export DIMOS_ORIN_DATE='2026-06-12 12:00:00'"
    fi
fi

if $DO_SWAP; then
    SWAPFILE="${SWAPFILE:-/swapfile_dimos}"
    if ! swapon --show 2>/dev/null | grep -qF "$SWAPFILE"; then
        log "Adding 4G swap at $SWAPFILE ..."
        if [[ ! -f "$SWAPFILE" ]]; then
            sudo fallocate -l 4G "$SWAPFILE" 2>/dev/null \
                || sudo dd if=/dev/zero of="$SWAPFILE" bs=1M count=4096 status=progress
            sudo chmod 600 "$SWAPFILE"
            sudo mkswap "$SWAPFILE"
        fi
        sudo swapon "$SWAPFILE" 2>/dev/null || true
    fi
elif [[ "$(awk '/SwapTotal/ {print $2}' /proc/meminfo)" -lt 1048576 ]]; then
    log "NOTE: swap < 1GB — pass --swap if you must nix-build on Orin"
fi

if [[ -f "$CUSTOM" ]] && grep -qF "$MARKER" "$CUSTOM" 2>/dev/null; then
    log "nix copy: already configured"
else
    log "Configuring nix for laptop → Orin copy ..."
    sudo tee -a "$CUSTOM" <<EOF

$MARKER
require-sigs = false
trusted-users = root $(whoami)
extra-platforms = aarch64-linux
EOF
    sudo systemctl restart nix-daemon
    sleep 2
fi

if $DO_FULL; then
    if ! ping -c 1 -W 2 8.8.8.8 >/dev/null 2>&1; then
        log "Need internet for --full. Try: sudo ip route replace default via 192.168.123.222"
        exit 1
    fi
    sudo apt-get update
    sudo apt-get install -y git cmake build-essential portaudio19-dev libportaudio2

    if [[ ! -f "$CYCLONEDDS_HOME/lib/libddsc.so" ]]; then
        CYCLONEDDS_SRC="$HOME/cyclonedds"
        log "Building CycloneDDS ..."
        rm -rf "$CYCLONEDDS_SRC"
        git clone https://github.com/eclipse-cyclonedds/cyclonedds.git "$CYCLONEDDS_SRC"
        cd "$CYCLONEDDS_SRC" && git checkout 0.10.5
        mkdir -p build && cd build
        cmake .. -DCMAKE_INSTALL_PREFIX="$CYCLONEDDS_HOME" -DBUILD_EXAMPLES=OFF -DBUILD_TESTING=OFF
        cmake --build . -j"$(nproc)"
        cmake --install .
    else
        log "CycloneDDS already at $CYCLONEDDS_HOME"
    fi

    if ! command -v uv >/dev/null 2>&1; then
        curl -LsSf https://astral.sh/uv/install.sh | sh
        # shellcheck disable=SC1091
        source "$HOME/.local/bin/env"
    fi
    cd "$DIMOS_DIR"
    log "uv sync (unitree-dds + navigation) ..."
    uv sync --extra unitree-dds --extra navigation
fi

if [[ ! -f "$CYCLONEDDS_HOME/lib/libddsc.so" ]]; then
    log "WARNING: CycloneDDS missing — run: bash ~/topsun_dimos/scripts/orin_setup.sh --full"
fi
if [[ ! -x "$DIMOS_DIR/.venv/bin/dimos" ]]; then
    log "WARNING: dimos venv missing — run: bash ~/topsun_dimos/scripts/orin_setup.sh --full"
fi

log "=== Orin setup OK ==="
log "Nav binaries: laptop → bash scripts/laptop_prebuild_nav_for_orin.sh"
log "Run nav:      bash ~/topsun_dimos/scripts/orin_run_nav.sh"
