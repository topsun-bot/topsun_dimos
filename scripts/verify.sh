#!/usr/bin/env bash
# Single entrypoint for local + CI-style verification (see AGENTS.md).
# Never triggers Git LFS downloads: tests that need smudged LFS objects are skipped.
set -euo pipefail
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

export DIMOS_SKIP_LFS_PULL=1
export GIT_LFS_SKIP_SMUDGE=1

echo "================================================================"
echo " verify.sh @ $REPO_ROOT"
echo " host: $(uname -srm)"
echo " LFS: pulls disabled (DIMOS_SKIP_LFS_PULL=1, GIT_LFS_SKIP_SMUDGE=1)"
echo "================================================================"

echo ">>> [1/3] uv sync (all extras, no dds / unitree-dds)"
uv sync --all-extras --no-extra dds --no-extra unitree-dds
echo "<<< OK"

echo ">>> [2/3] pytest (default markers: excludes slow, tool, mujoco — see pyproject.toml)"
uv run pytest -q
echo "<<< OK"

if [[ "${VERIFY_SKIP_MYPY:-}" == "1" ]]; then
  echo ">>> [3/3] mypy dimos/ (skipped: VERIFY_SKIP_MYPY=1, e.g. GitHub-hosted without ROS)"
  echo "<<< SKIPPED"
else
  echo ">>> [3/3] mypy dimos/"
  if [[ -d /opt/ros/humble/lib/python3.10/site-packages ]]; then
    export MYPYPATH="/opt/ros/humble/lib/python3.10/site-packages"
  fi
  uv run mypy dimos/
  echo "<<< OK"
fi

echo "================================================================"
echo " verify.sh: all steps passed"
echo "================================================================"
