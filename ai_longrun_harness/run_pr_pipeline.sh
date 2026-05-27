#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
STATE_DIR="${SCRIPT_DIR}/state"
CONFIG_FILE="${SCRIPT_DIR}/config.env"

mkdir -p "${STATE_DIR}"

# shellcheck disable=SC1090
[[ -f "${CONFIG_FILE}" ]] && source "${CONFIG_FILE}"

PR_TARGET_BRANCH="${PR_TARGET_BRANCH:-main}"
PR_REMOTE="${PR_REMOTE:-origin}"
PR_CREATE_CMD="${PR_CREATE_CMD:-gh pr create --base \"${PR_TARGET_BRANCH}\"}"

usage() {
  cat <<USAGE
Usage: $(basename "$0") [--dry-run]

Run PR pipeline for current branch.

Options:
  --dry-run   Print push/PR commands without executing them.
  -h, --help  Show this help.
USAGE
}

DRY_RUN=0
case "${1:-}" in
  --dry-run) DRY_RUN=1 ;;
  -h|--help) usage; exit 0 ;;
  "") ;;
  *) echo "[run_pr_pipeline] Unknown argument: $1" >&2; usage; exit 2 ;;
esac

cd "${REPO_ROOT}"

if ! git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  echo "[run_pr_pipeline] Not inside a git repository." >&2
  exit 1
fi

BRANCH="$(git rev-parse --abbrev-ref HEAD)"
if [[ "${BRANCH}" == "${PR_TARGET_BRANCH}" ]]; then
  echo "[run_pr_pipeline] Current branch equals target branch '${PR_TARGET_BRANCH}', refusing." >&2
  exit 1
fi

PUSH_CMD="git push -u ${PR_REMOTE} ${BRANCH}"

if [[ "${DRY_RUN}" -eq 1 ]]; then
  echo "[run_pr_pipeline] DRY RUN"
  echo "  ${PUSH_CMD}"
  echo "  ${PR_CREATE_CMD}"
else
  eval "${PUSH_CMD}"
  eval "${PR_CREATE_CMD}"
fi

cat > "${STATE_DIR}/pr_pipeline.state" <<STATE
status=ok
branch=${BRANCH}
base=${PR_TARGET_BRANCH}
dry_run=${DRY_RUN}
timestamp=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
STATE

echo "[run_pr_pipeline] Completed for ${BRANCH} -> ${PR_TARGET_BRANCH}."
