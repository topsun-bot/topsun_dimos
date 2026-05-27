#!/usr/bin/env bash
# Run Quality Gate regression layer only (same as PR workflow quality-gate-regression).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"
export PYTHONPATH=.
exec python3 -m pytest test/regression -c test/pytest.ini -m regression "$@"
