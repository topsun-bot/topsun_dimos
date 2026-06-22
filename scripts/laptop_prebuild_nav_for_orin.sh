#!/usr/bin/env bash
# Cross-build nav native modules for G1 Orin on a laptop (x86_64), then copy to Orin.
# Use when Orin freezes during `nix build` (OOM / slow download+unpack).
#
# Prerequisites (laptop):
#   - Nix with flakes: https://nixos.org/download.html
#   - One-time QEMU for aarch64 on x86 (fixes "Exec format error"):
#       bash scripts/laptop_setup_nix_aarch64.sh
#   - /etc/nix/nix.custom.conf: trusted-users + extra-platforms (setup script adds these)
#   - SSH to Orin: ssh unitree@192.168.123.164 (passwordless or agent)
#   - This repo at LAPTOP_DIMOS (default: parent of scripts/)
#   - Laptop: Clash ON so github.com is reachable; export HTTPS_PROXY if needed
#   - Orin NAT: Clash DIRECT for 192.168.123.0/24 (only affects Orin traffic)
#
# Usage (on laptop):
#   export ORIN_HOST=unitree@192.168.123.164
#   bash scripts/laptop_prebuild_nav_for_orin.sh
#
# Optional — build/copy one module only:
#   ORIN_NAV_MODULE=terrain bash scripts/laptop_prebuild_nav_for_orin.sh

set -euo pipefail

log() { echo "[$(date '+%H:%M:%S')] $*"; }

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LAPTOP_DIMOS="${LAPTOP_DIMOS:-$(cd "$SCRIPT_DIR/.." && pwd)}"
ORIN_HOST="${ORIN_HOST:-unitree@192.168.123.164}"
ORIN_DIMOS="${ORIN_DIMOS:-/home/unitree/topsun_dimos}"
SSH_OPTS="-o BatchMode=yes -o ConnectTimeout=10 -o ServerAliveInterval=15 -o ServerAliveCountMax=3"

log "Preflight: checking nix, SSH to $ORIN_HOST, Orin nix config..."
AARCH64="--system aarch64-linux"
NIX_JOBS="--max-jobs 1 --cores 4"

if ! command -v nix >/dev/null 2>&1; then
    log "ERROR: nix not found on laptop. Install from https://nixos.org/download.html"
    exit 1
fi

if ! ssh $SSH_OPTS "$ORIN_HOST" true 2>/dev/null; then
    log "ERROR: cannot SSH to $ORIN_HOST (set ORIN_HOST, check key/agent)."
    exit 1
fi
log "  SSH OK"

if ! ssh $SSH_OPTS "$ORIN_HOST" command -v nix >/dev/null 2>&1; then
    log "ERROR: nix not on Orin — install Nix on Orin first (needed for nix copy)."
    exit 1
fi

if ! ssh $SSH_OPTS "$ORIN_HOST" 'grep -q "require-sigs = false" /etc/nix/nix.custom.conf 2>/dev/null'; then
    log "ERROR: Orin must allow unsigned nix copy."
    log "  On Orin run once: bash ~/topsun_dimos/scripts/orin_setup.sh"
    exit 1
fi

FLAKE_CACHE="${FLAKE_CACHE:-$HOME/.cache/dimos-orin-flakes}"

clone_flake() {
    local name="$1" url="$2" tag="$3"
    if [[ -d "$FLAKE_CACHE/$name/.git" ]]; then
        return 0
    fi
    log "Cloning $name ($tag) ..."
    mkdir -p "$FLAKE_CACHE"
    if [[ -n "${HTTP_PROXY:-}" ]]; then
        git -c http.proxy="$HTTP_PROXY" clone --depth 1 --branch "$tag" "$url" "$FLAKE_CACHE/$name"
    else
        git clone --depth 1 --branch "$tag" "$url" "$FLAKE_CACHE/$name"
    fi
}

# Cross-build requires trusted-users + extra-platforms in nix.custom.conf
if ! nix eval --impure --expr 'builtins.currentSystem' --system aarch64-linux >/dev/null 2>&1; then
    log "ERROR: aarch64-linux cross-build not allowed."
    log "  Run: bash scripts/laptop_setup_nix_aarch64.sh"
    exit 1
fi

if [[ "$(uname -m)" == "x86_64" ]] && ! ls /proc/sys/fs/binfmt_misc/qemu-aarch64* >/dev/null 2>&1; then
    log "ERROR: QEMU binfmt for aarch64 not registered (causes 'Exec format error')."
    log "  Run once: bash scripts/laptop_setup_nix_aarch64.sh"
    exit 1
fi

if ! curl -sfI --max-time 20 https://github.com >/dev/null 2>&1; then
    log "WARNING: https://github.com not reachable from this shell."
    log "  Turn on Clash on the laptop and set proxy, e.g.:"
    log "    export HTTPS_PROXY=http://127.0.0.1:7897"
    log "    export HTTP_PROXY=http://127.0.0.1:7897"
    if [[ -z "${HTTPS_PROXY:-}${HTTP_PROXY:-}" ]]; then
        log "ERROR: no proxy set and github.com unreachable — nix cannot fetch flakes."
        exit 1
    fi
fi

copy_result_to_orin() {
    local name="$1"
    local laptop_dir="$2"
    local orin_dir="$3"
    local exe_name="$4"

    local store_path
    store_path="$(cd "$laptop_dir" && nix path-info ./result)"

    log "=== $name: nix copy to Orin ==="
    log "  store: $store_path"
    log "  (full closure ~3 GiB — first progress line may take 1–3 min)"
    nix copy --to "ssh://$ORIN_HOST" -v "$store_path"

    ssh $SSH_OPTS "$ORIN_HOST" bash -s "$orin_dir" "$store_path" "$exe_name" <<'REMOTE'
set -euo pipefail
orin_dir="$1"
store_path="$2"
exe_name="$3"
mkdir -p "$orin_dir"
ln -sfn "$store_path" "$orin_dir/result"
test -x "$orin_dir/result/bin/$exe_name"
echo "OK on Orin: $orin_dir/result/bin/$exe_name"
REMOTE
}

build_and_copy() {
    local key="$1"
    local name="$2"
    local laptop_dir="$3"
    local build_cmd="$4"
    local orin_dir="$5"
    local exe_name="$6"

    if [[ -n "${ORIN_NAV_MODULE:-}" && "$ORIN_NAV_MODULE" != "$key" ]]; then
        return 0
    fi

    if ssh "$ORIN_HOST" "test -x $orin_dir/result/bin/$exe_name" 2>/dev/null; then
        log "SKIP $name — already on Orin"
        return 0
    fi

    log "=== BUILD $name (aarch64 on laptop) ==="
    # shellcheck disable=SC2086
    (cd "$laptop_dir" && eval "$build_cmd")

    copy_result_to_orin "$name" "$laptop_dir" "$orin_dir" "$exe_name"
}

NAV="$LAPTOP_DIMOS/dimos/navigation/nav_stack/modules"
LIDAR_CPP="$LAPTOP_DIMOS/dimos/hardware/sensors/lidar/fastlio2/cpp"

build_and_copy \
    fastlio2 FastLIO2 \
    "$LIDAR_CPP" \
    "nix build .#fastlio2_native --no-write-lock-file $AARCH64 $NIX_JOBS" \
    "$ORIN_DIMOS/dimos/hardware/sensors/lidar/fastlio2/cpp" \
    fastlio2_native

build_and_copy \
    terrain TerrainAnalysis \
    "$NAV/terrain_analysis" \
    "clone_flake terrain-analysis https://github.com/dimensionalOS/dimos-module-terrain-analysis.git v0.1.1 && nix build '$FLAKE_CACHE/terrain-analysis' --no-write-lock-file $AARCH64 $NIX_JOBS" \
    "$ORIN_DIMOS/dimos/navigation/nav_stack/modules/terrain_analysis" \
    terrain_analysis

build_and_copy \
    local_planner LocalPlanner \
    "$NAV/local_planner" \
    "clone_flake local-planner https://github.com/dimensionalOS/dimos-module-local-planner.git v0.6.0 && nix build '$FLAKE_CACHE/local-planner' --no-write-lock-file $AARCH64 $NIX_JOBS" \
    "$ORIN_DIMOS/dimos/navigation/nav_stack/modules/local_planner" \
    local_planner

build_and_copy \
    path_follower PathFollower \
    "$NAV/path_follower" \
    "clone_flake path-follower https://github.com/dimensionalOS/dimos-module-path-follower.git v0.2.0 && nix build '$FLAKE_CACHE/path-follower' --no-write-lock-file $AARCH64 $NIX_JOBS" \
    "$ORIN_DIMOS/dimos/navigation/nav_stack/modules/path_follower" \
    path_follower

build_and_copy \
    pgo PGO \
    "$NAV/pgo/cpp" \
    "nix build .#default --no-write-lock-file $AARCH64 $NIX_JOBS" \
    "$ORIN_DIMOS/dimos/navigation/nav_stack/modules/pgo/cpp" \
    pgo

log "=== DONE — binaries on Orin under $ORIN_DIMOS ==="
log "On Orin: bash ~/topsun_dimos/scripts/orin_run_nav.sh"
