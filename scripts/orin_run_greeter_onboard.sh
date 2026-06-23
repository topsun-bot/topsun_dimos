#!/usr/bin/env bash
# Run unitree-g1-greeter-onboard on G1 Orin.
#
# Usage (on Orin):
#   screen -S greeter bash --norc
#   bash ~/topsun_dimos/scripts/orin_run_greeter_onboard.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "$SCRIPT_DIR/g1_orin_env.sh"

N_WORKERS="${N_WORKERS:-4}"
VIEWER="${VIEWER:-none}"
LISTEN_HOST="${LISTEN_HOST:-0.0.0.0}"

g1_orin_activate_env

echo "[orin_run_greeter] n_workers=$N_WORKERS — builtin voice: App 唤醒对话模式; TTS: DASHSCOPE_API_KEY"

exec dimos --n-workers "$N_WORKERS" --viewer "$VIEWER" --listen-host "$LISTEN_HOST" run unitree-g1-greeter-onboard
