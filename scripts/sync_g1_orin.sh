#!/usr/bin/env bash
# Sync G1 Orin scripts + key assets from laptop to Orin.
#
# Usage (laptop):
#   export ORIN_HOST=unitree@192.168.123.164
#   bash scripts/sync_g1_orin.sh

set -euo pipefail

log() { echo "[$(date '+%H:%M:%S')] $*"; }

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
ORIN_HOST="${ORIN_HOST:-unitree@192.168.123.164}"
ORIN_DIMOS="${ORIN_DIMOS:-/home/unitree/topsun_dimos}"

if ! ssh -o BatchMode=yes -o ConnectTimeout=5 "$ORIN_HOST" true 2>/dev/null; then
    log "ERROR: cannot SSH to $ORIN_HOST"
    exit 1
fi

log "Syncing to $ORIN_HOST:$ORIN_DIMOS ..."

rsync -avz \
    "$REPO_ROOT/scripts/g1_orin_env.sh" \
    "$REPO_ROOT/scripts/orin_dimos.sh" \
    "$REPO_ROOT/scripts/orin_setup.sh" \
    "$REPO_ROOT/scripts/orin_run_nav.sh" \
    "$REPO_ROOT/scripts/orin_run_greeter_onboard.sh" \
    "$REPO_ROOT/scripts/g1_test_speaker.py" \
    "$REPO_ROOT/scripts/orin_test_speaker.sh" \
    "$REPO_ROOT/scripts/g1_test_mic.py" \
    "$REPO_ROOT/scripts/orin_test_mic.sh" \
    "$REPO_ROOT/scripts/g1_test_cosyvoice_speaker.py" \
    "$REPO_ROOT/scripts/orin_test_cosyvoice_speaker.sh" \
    "$REPO_ROOT/scripts/orin_sync_time.sh" \
    "$REPO_ROOT/scripts/orin_test_body_mic.sh" \
    "$REPO_ROOT/scripts/g1_test_body_mic.py" \
    "$REPO_ROOT/scripts/orin_test_builtin_voice.sh" \
    "$REPO_ROOT/scripts/g1_test_builtin_voice.py" \
    "$ORIN_HOST:$ORIN_DIMOS/scripts/"

rsync -avz \
    "$REPO_ROOT/dimos/robot/unitree/g1/greeter_intent_router.py" \
    "$ORIN_HOST:$ORIN_DIMOS/dimos/robot/unitree/g1/"

rsync -avz \
    "$REPO_ROOT/dimos/robot/unitree/g1/blueprints/agentic/unitree_g1_greeter_onboard.py" \
    "$ORIN_HOST:$ORIN_DIMOS/dimos/robot/unitree/g1/blueprints/agentic/"

rsync -avz \
    "$REPO_ROOT/dimos/agents/skills/speak_skill.py" \
    "$ORIN_HOST:$ORIN_DIMOS/dimos/agents/skills/"

rsync -avz \
    "$REPO_ROOT/dimos/robot/unitree/g1/connection_spec.py" \
    "$ORIN_HOST:$ORIN_DIMOS/dimos/robot/unitree/g1/"

rsync -avz \
    "$REPO_ROOT/dimos/robot/unitree/g1/effectors/high_level/dds_sdk.py" \
    "$ORIN_HOST:$ORIN_DIMOS/dimos/robot/unitree/g1/effectors/high_level/"

ssh "$ORIN_HOST" "mkdir -p $ORIN_DIMOS/dimos/robot/unitree/g1/audio"
rsync -avz \
    "$REPO_ROOT/dimos/robot/unitree/g1/audio/" \
    "$ORIN_HOST:$ORIN_DIMOS/dimos/robot/unitree/g1/audio/"

rsync -avz \
    "$REPO_ROOT/.env.example" \
    "$ORIN_HOST:$ORIN_DIMOS/"

rsync -avz \
    "$REPO_ROOT/data/command_center.html" \
    "$ORIN_HOST:$ORIN_DIMOS/data/"

rsync -avz \
    "$REPO_ROOT/dimos/robot/unitree/g1/greeter_intent_router.py" \
    "$REPO_ROOT/dimos/robot/unitree/g1/g1_builtin_voice_input.py" \
    "$REPO_ROOT/dimos/robot/unitree/g1/g1_asr_dedupe.py" \
    "$REPO_ROOT/dimos/robot/unitree/g1/greeter_skill.py" \
    "$REPO_ROOT/dimos/robot/unitree/g1/greeter_tour_skill.py" \
    "$REPO_ROOT/dimos/robot/unitree/g1/greeter_intro_script_generator.py" \
    "$REPO_ROOT/dimos/robot/unitree/g1/greeter_skill_spec.py" \
    "$REPO_ROOT/dimos/robot/unitree/g1/greeter_tour_skill_spec.py" \
    "$ORIN_HOST:$ORIN_DIMOS/dimos/robot/unitree/g1/"

rsync -avz \
    "$REPO_ROOT/dimos/robot/unitree/g1/blueprints/navigation/unitree_g1_nav_onboard.py" \
    "$ORIN_HOST:$ORIN_DIMOS/dimos/robot/unitree/g1/blueprints/navigation/"

log "Removing obsolete scripts on Orin ..."
ssh "$ORIN_HOST" "rm -f \
    $ORIN_DIMOS/scripts/orin_build_env.sh \
    $ORIN_DIMOS/scripts/orin_prebuild_nav.sh \
    $ORIN_DIMOS/scripts/orin_add_swap.sh \
    $ORIN_DIMOS/scripts/orin_nix_allow_copy.sh \
    $ORIN_DIMOS/scripts/laptop_prebuild_nav_for_orin.sh \
    $ORIN_DIMOS/scripts/laptop_build_remaining_nav.sh \
    $ORIN_DIMOS/scripts/laptop_setup_nix_aarch64.sh \
    $ORIN_DIMOS/scripts/run_greeter_dds_lite.py \
    $ORIN_DIMOS/dimos/robot/unitree/g1/blueprints/agentic/unitree_g1_greeter_dds_lite.py \
    $ORIN_DIMOS/dimos/robot/unitree/g1/effectors/high_level/connection_spec.py \
    2>/dev/null; chmod +x $ORIN_DIMOS/scripts/g1_orin_env.sh $ORIN_DIMOS/scripts/orin_*.sh $ORIN_DIMOS/scripts/g1_test_speaker.py"

log "=== Sync done ==="
log "On Orin: N_WORKERS=6 bash ~/topsun_dimos/scripts/orin_run_greeter_onboard.sh"
log "Secrets: cp ~/topsun_dimos/.env.example ~/.env  (edit DASHSCOPE_API_KEY, then restart greeter)"
