#!/usr/bin/env bash
# Sync Orin system clock from this laptop (fixes SSL / CosyVoice / Whisper failures).
#
# Usage (laptop):
#   bash scripts/orin_sync_time.sh
#   ORIN_HOST=unitree@192.168.123.164 bash scripts/orin_sync_time.sh

set -euo pipefail

ORIN_HOST="${ORIN_HOST:-unitree@192.168.123.164}"
UTC_NOW="$(date -u +'%Y-%m-%d %H:%M:%S')"

echo "Setting Orin clock to UTC ${UTC_NOW} ..."
ssh "$ORIN_HOST" "sudo date -s '${UTC_NOW}' && date && timedatectl status 2>/dev/null || true"
echo "Done. Retry CosyVoice test on Orin."
