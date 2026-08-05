#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
STATE_DIR="${SCRIPT_DIR}/state"
CONFIG_FILE="${SCRIPT_DIR}/config.env"
MULTI_AGENT_SCRIPT="${SCRIPT_DIR}/run_multi_agent_loop.sh"
SIM_GATE_SCRIPT="${SCRIPT_DIR}/run_sim_gate.sh"
HW_VERIFY_SCRIPT="${SCRIPT_DIR}/run_hw_verify.sh"
PR_PIPELINE_SCRIPT="${SCRIPT_DIR}/run_pr_pipeline.sh"

mkdir -p "${STATE_DIR}"

usage() {
  cat <<USAGE
Usage: $(basename "$0") [--auto-commit] [--commit-message "msg"] [--dry-run-pr]

Single-command automation from current local branch to PR pipeline targeting main:
1) run multi-agent plan/dev/test loop
2) run simulation regression gate (if SIM_GATE_ENABLED=1 in config.env)
3) run hardware verification gate (if HW_VERIFY_ENABLED=1 in config.env)
4) run PR pipeline

Gates 2/3 implement the verification funnel described in
docs/development/hardware_verification_loop.md and are opt-in (disabled by
default) so existing PR-only usage is unaffected until a project wires up
SIM_REGRESSION_CMD / HW_VERIFY_CMD in ai_longrun_harness/config.env.

Options:
  --auto-commit           Auto-commit local dirty changes before pipeline.
  --commit-message <msg>  Commit message used with --auto-commit.
  --dry-run-pr            Pass --dry-run to PR pipeline script.
  -h, --help              Show this help.
USAGE
}

AUTO_COMMIT=0
DRY_RUN_PR=0
COMMIT_MESSAGE=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --auto-commit)
      AUTO_COMMIT=1
      shift
      ;;
    --commit-message)
      [[ $# -lt 2 ]] && { echo "[run_to_main] --commit-message requires a value." >&2; exit 2; }
      COMMIT_MESSAGE="$2"
      shift 2
      ;;
    --dry-run-pr)
      DRY_RUN_PR=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "[run_to_main] Unknown argument: $1" >&2
      usage
      exit 2
      ;;
  esac
done

# shellcheck disable=SC1090
[[ -f "${CONFIG_FILE}" ]] && source "${CONFIG_FILE}"

PR_TARGET_BRANCH="${PR_TARGET_BRANCH:-main}"
AUTO_COMMIT_MESSAGE="${AUTO_COMMIT_MESSAGE:-chore: prepare auto pipeline run}"
[[ -n "${COMMIT_MESSAGE}" ]] && AUTO_COMMIT_MESSAGE="${COMMIT_MESSAGE}"
SIM_GATE_ENABLED="${SIM_GATE_ENABLED:-0}"
HW_VERIFY_ENABLED="${HW_VERIFY_ENABLED:-0}"

cd "${REPO_ROOT}"

if ! git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  echo "[run_to_main] Not inside a git repository." >&2
  exit 1
fi

BRANCH="$(git rev-parse --abbrev-ref HEAD)"
if [[ "${BRANCH}" == "${PR_TARGET_BRANCH}" ]]; then
  echo "[run_to_main] Refusing to run from target branch '${PR_TARGET_BRANCH}'." >&2
  exit 1
fi

if [[ ! -x "${MULTI_AGENT_SCRIPT}" ]]; then
  echo "[run_to_main] Missing executable: ${MULTI_AGENT_SCRIPT}" >&2
  exit 1
fi

if [[ ! -x "${PR_PIPELINE_SCRIPT}" ]]; then
  echo "[run_to_main] Missing executable: ${PR_PIPELINE_SCRIPT}" >&2
  exit 1
fi

if [[ "${SIM_GATE_ENABLED}" == "1" && ! -x "${SIM_GATE_SCRIPT}" ]]; then
  echo "[run_to_main] SIM_GATE_ENABLED=1 but missing executable: ${SIM_GATE_SCRIPT}" >&2
  exit 1
fi

if [[ "${HW_VERIFY_ENABLED}" == "1" && ! -x "${HW_VERIFY_SCRIPT}" ]]; then
  echo "[run_to_main] HW_VERIFY_ENABLED=1 but missing executable: ${HW_VERIFY_SCRIPT}" >&2
  exit 1
fi

if [[ -n "$(git status --porcelain)" ]]; then
  if [[ "${AUTO_COMMIT}" -eq 1 ]]; then
    git add -A
    git commit -m "${AUTO_COMMIT_MESSAGE}"
    echo "[run_to_main] Auto-committed working tree changes."
  else
    echo "[run_to_main] Working tree is dirty. Commit/stash first or use --auto-commit." >&2
    exit 1
  fi
fi

START_TS="$(date -u +"%Y-%m-%dT%H:%M:%SZ")"

echo "[run_to_main] Running multi-agent loop..."
"${MULTI_AGENT_SCRIPT}"

if [[ "${SIM_GATE_ENABLED}" == "1" ]]; then
  echo "[run_to_main] Running simulation regression gate..."
  "${SIM_GATE_SCRIPT}"
else
  echo "[run_to_main] Simulation regression gate disabled (SIM_GATE_ENABLED!=1); skipping."
fi

if [[ "${HW_VERIFY_ENABLED}" == "1" ]]; then
  echo "[run_to_main] Running hardware verification gate..."
  "${HW_VERIFY_SCRIPT}"
else
  echo "[run_to_main] Hardware verification gate disabled (HW_VERIFY_ENABLED!=1); skipping."
fi

echo "[run_to_main] Running PR pipeline..."
if [[ "${DRY_RUN_PR}" -eq 1 ]]; then
  "${PR_PIPELINE_SCRIPT}" --dry-run
else
  "${PR_PIPELINE_SCRIPT}"
fi

END_TS="$(date -u +"%Y-%m-%dT%H:%M:%SZ")"

cat > "${STATE_DIR}/to_main.state" <<STATE
status=ok
branch=${BRANCH}
base=${PR_TARGET_BRANCH}
auto_commit=${AUTO_COMMIT}
dry_run_pr=${DRY_RUN_PR}
sim_gate_enabled=${SIM_GATE_ENABLED}
hw_verify_enabled=${HW_VERIFY_ENABLED}
started_at=${START_TS}
finished_at=${END_TS}
STATE

echo "[run_to_main] Completed: ${BRANCH} -> ${PR_TARGET_BRANCH}."
