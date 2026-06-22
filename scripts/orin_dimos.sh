#!/usr/bin/env bash
# Run any dimos CLI on Orin with the same env as orin_run_nav.sh.
#
# Usage (second SSH terminal while nav is running):
#   bash ~/topsun_dimos/scripts/orin_dimos.sh log -f
#   bash ~/topsun_dimos/scripts/orin_dimos.sh topic echo /odometry
#   bash ~/topsun_dimos/scripts/orin_dimos.sh status
#   bash ~/topsun_dimos/scripts/orin_dimos.sh stop

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "$SCRIPT_DIR/g1_orin_env.sh"

g1_orin_activate_env

if [[ $# -eq 0 ]]; then
    echo "Usage: bash $0 <dimos-args...>"
    echo "Example: bash $0 log -f"
    exit 1
fi

exec dimos "$@"
