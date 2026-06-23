#!/usr/bin/env bash
# Test CosyVoice + G1 PlayStream on Orin (needs DASHSCOPE_API_KEY in .env).
#
# Usage (on Orin):
#   bash ~/topsun_dimos/scripts/orin_test_cosyvoice_speaker.sh eth0

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "$SCRIPT_DIR/g1_orin_env.sh"

g1_orin_activate_env
g1_orin_check_clock || exit 1

exec python "$SCRIPT_DIR/g1_test_cosyvoice_speaker.py" "${@:-eth0}"
