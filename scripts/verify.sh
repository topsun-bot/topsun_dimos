#!/usr/bin/env bash
# Local verification gate before push. GitHub Actions (self-hosted) is authoritative
# for full coverage + mypy with ROS paths; see .github/workflows/ci.yml and AGENTS.md.
#
# --- Flaky / slow PyPI (optional; same `uv sync` flags, no CI behavior change here) ---
# `dimos` pulls `open3d` from core deps; resolving it can fetch `matplotlib` and other
# wheels. If installs time out, point uv at a mirror or raise timeouts before running:
#   export UV_INDEX_URL="https://pypi.tuna.tsinghua.edu.cn/simple"
#   export UV_HTTP_TIMEOUT=300
# (Other uv index / pip-compat vars: https://docs.astral.sh/uv/configuration/ )
#
# Retries: `uv sync` is retried a few times with backoff on non-zero exit (transient
# network). Override with e.g. `VERIFY_UV_SYNC_MAX_ATTEMPTS=1` for fail-fast.
#
set -euo pipefail
# In headless / agent shells, never block waiting for a TTY password prompt from git
# (HTTPS). Fail fast if credentials are not preconfigured (SSH agent, credential helper).
export GIT_TERMINAL_PROMPT=0
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

echo "================================================================"
echo " verify.sh @ $REPO_ROOT"
echo " host: $(uname -srm)"
echo "================================================================"

UV_SYNC_MAX_ATTEMPTS="${VERIFY_UV_SYNC_MAX_ATTEMPTS:-3}"
UV_SYNC_BACKOFF_SEC="${VERIFY_UV_SYNC_BACKOFF_SEC:-3}"

run_uv_sync() {
  local attempt=1
  while true; do
    if uv sync --all-extras --no-extra dds --no-extra unitree-dds; then
      return 0
    fi
    if (( attempt >= UV_SYNC_MAX_ATTEMPTS )); then
      echo ">>> uv sync failed after ${UV_SYNC_MAX_ATTEMPTS} attempt(s)" >&2
      return 1
    fi
    echo ">>> uv sync failed (attempt ${attempt}/${UV_SYNC_MAX_ATTEMPTS}), retrying in ${UV_SYNC_BACKOFF_SEC}s..." >&2
    sleep "$UV_SYNC_BACKOFF_SEC"
    attempt=$((attempt + 1))
    UV_SYNC_BACKOFF_SEC=$((UV_SYNC_BACKOFF_SEC * 2))
  done
}

echo ">>> [1/4] uv sync (align with AGENTS.md Quick Start + CI extras; max attempts=${UV_SYNC_MAX_ATTEMPTS})"
run_uv_sync
echo "<<< OK"

echo ">>> [2/4] ruff format --check"
uv run ruff format --check dimos
echo "<<< OK"

echo ">>> [3/4] ruff check (no auto-fix)"
uv run ruff check dimos
echo "<<< OK"

echo ">>> [4/4] pytest (default fast set: excludes slow, tool, mujoco)"
# dimos/conftest.py runs LCM `autoconf()` at session start. Without a TTY,
# `prompt.confirm` returns the default (yes) and then `sudo` may fail on hosts
# without passwordless sudo (agents, headless CI shells). `configure_system`
# already skips all fixes when CI is set — match that so verify does not fail
# before tests run.
if ! [[ -t 0 ]] && ! sudo -n true 2>/dev/null; then
  export CI=1
  echo ">>> non-interactive stdin and no passwordless sudo: CI=1 for pytest (skip system autoconf)"
fi
uv run pytest -q
echo "<<< OK"

echo "================================================================"
echo " verify.sh finished OK"
echo "================================================================"
