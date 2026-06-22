#!/usr/bin/env bash
# Test G1 body speaker via DDS AudioClient (no full greeter stack).
#
# Usage (on Orin):
#   bash ~/topsun_dimos/scripts/orin_test_speaker.sh eth0

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "$SCRIPT_DIR/g1_orin_env.sh"

g1_orin_activate_env

exec python "$SCRIPT_DIR/g1_test_speaker.py" "${@:-eth0}"
