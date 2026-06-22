#!/usr/bin/env bash
# Listen to G1 built-in ASR (rt/audio_msg). Requires wake-up conversation mode in App.
#
# Usage (on Orin):
#   bash ~/topsun_dimos/scripts/orin_test_builtin_voice.sh eth0
#   bash ~/topsun_dimos/scripts/orin_test_builtin_voice.sh eth0 120

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "$SCRIPT_DIR/g1_orin_env.sh"

g1_orin_activate_env

exec python "$SCRIPT_DIR/g1_test_builtin_voice.py" "${@:-eth0}"
