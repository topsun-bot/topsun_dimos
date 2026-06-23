#!/usr/bin/env bash
# Test microphone input on G1 Orin (lists devices + 5s recording + optional Whisper).
#
# Usage (on Orin):
#   bash ~/topsun_dimos/scripts/orin_test_mic.sh
#   bash ~/topsun_dimos/scripts/orin_test_mic.sh scan       # fast: pulse/default only
#   bash ~/topsun_dimos/scripts/orin_test_mic.sh scan-ape   # slow: includes tegra APE
#   bash ~/topsun_dimos/scripts/orin_test_mic.sh 27
#   bash ~/topsun_dimos/scripts/orin_test_mic.sh 2

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "$SCRIPT_DIR/g1_orin_env.sh"

g1_orin_activate_env

exec python "$SCRIPT_DIR/g1_test_mic.py" "$@"
