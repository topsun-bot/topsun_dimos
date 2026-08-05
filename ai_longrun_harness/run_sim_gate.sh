#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
STATE_DIR="${SCRIPT_DIR}/state"
CONFIG_FILE="${SCRIPT_DIR}/config.env"

mkdir -p "${STATE_DIR}"

# shellcheck disable=SC1090
[[ -f "${CONFIG_FILE}" ]] && source "${CONFIG_FILE}"

usage() {
  cat <<USAGE
Usage: $(basename "$0")

Stage 2 of the hardware verification funnel (see
docs/development/hardware_verification_loop.md): run the candidate change
through simulation regression *before* it is allowed anywhere near real
hardware. Catches the low-cost bugs (bad API calls, obviously wrong
trajectories, crashes, timeouts) that don't need a real robot to find, so
real-hardware trials are spent on genuine sim2real gaps instead.

Requires SIM_REGRESSION_CMD to be set in ai_longrun_harness/config.env, e.g.:
  SIM_REGRESSION_CMD="uv run pytest -m mujoco dimos/e2e_tests"
or a project-specific driver that runs the changed skill(s) in MuJoCo/dimsim
and judges them with dimos.verification.judge against the same task specs
used by run_hw_verify.sh, so a sim pass and a hardware pass mean the same
thing.
USAGE
}

case "${1:-}" in
  -h | --help)
    usage
    exit 0
    ;;
esac

cd "${REPO_ROOT}"

if [[ -z "${SIM_REGRESSION_CMD:-}" ]]; then
  echo "[run_sim_gate] SIM_REGRESSION_CMD is not set in ${CONFIG_FILE} -- refusing to silently skip the sim gate." >&2
  echo "[run_sim_gate] See docs/development/hardware_verification_loop.md#21-stage-2." >&2
  exit 1
fi

echo "[run_sim_gate] Running: ${SIM_REGRESSION_CMD}"
if eval "${SIM_REGRESSION_CMD}"; then
  STATUS=ok
else
  STATUS=failed
fi

cat >"${STATE_DIR}/sim_gate.state" <<STATE
status=${STATUS}
command=${SIM_REGRESSION_CMD}
timestamp=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
STATE

if [[ "${STATUS}" != "ok" ]]; then
  echo "[run_sim_gate] Simulation regression failed; refusing to proceed to hardware." >&2
  exit 1
fi

echo "[run_sim_gate] Simulation regression passed."
