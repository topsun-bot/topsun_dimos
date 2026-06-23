#!/usr/bin/env bash
# Run unitree-g1-nav-onboard on G1 Orin.
#
# Usage (on Orin):
#   screen -S nav bash --norc
#   bash ~/topsun_dimos/scripts/orin_run_nav.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "$SCRIPT_DIR/g1_orin_env.sh"

N_WORKERS="${N_WORKERS:-4}"
VIEWER="${VIEWER:-none}"
LISTEN_HOST="${LISTEN_HOST:-0.0.0.0}"

g1_orin_activate_env

echo "[orin_run_nav] LIDAR_HOST_IP=$LIDAR_HOST_IP LIDAR_IP=$LIDAR_IP n_workers=$N_WORKERS viewer=$VIEWER"
echo "[orin_run_nav] Nav natives must exist under dimos/**/result/bin/ (see laptop_prebuild_nav_for_orin.sh)."

exec dimos --n-workers "$N_WORKERS" --viewer "$VIEWER" --listen-host "$LISTEN_HOST" run unitree-g1-nav-onboard
