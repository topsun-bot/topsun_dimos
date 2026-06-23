#!/usr/bin/env bash
# Test G1 body microphones via UDP multicast (not ALSA/pulse).
#
# Usage (on Orin):
#   bash ~/topsun_dimos/scripts/orin_test_body_mic.sh eth0

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "$SCRIPT_DIR/g1_orin_env.sh"

g1_orin_activate_env

exec python "$SCRIPT_DIR/g1_test_body_mic.py" "${@:-eth0}"
