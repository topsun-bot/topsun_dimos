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

Stage 4-7 of the hardware verification funnel (see
docs/development/hardware_verification_loop.md): deploy to the real robot and
judge the result with the multi-signal verifier in dimos/verification
(proprioception + onboard camera + independent external camera + optional
VLM opinion), not a single external-camera "looks right" call. On failure
this stage is responsible for writing a structured evidence bundle
(dimos.verification.evidence.build_evidence_bundle) so the next AI iteration
gets a reproducible record instead of a human's verbal description.

Requires HW_VERIFY_CMD to be set in ai_longrun_harness/config.env, pointing
at a project-specific driver that:
  1. deploys the candidate change to the robot,
  2. runs HW_VERIFY_REPEAT_TRIALS repeated trials of the target task spec
     (a single passing trial is not a merge-gate pass -- see
     dimos.verification.judge.evaluate_repeated_trials),
  3. judges each trial against the same task spec used by run_sim_gate.sh,
  4. on any failure, packages an evidence bundle for the coding agent's
     next iteration.

This script intentionally does not run anything by default: there is no safe
generic default for "control a physical robot". HW_VERIFY_ENABLED must be
explicitly set to 1 in config.env, as an additional guard against an
unattended hardware run being triggered by a stale/default config.
USAGE
}

case "${1:-}" in
  -h | --help)
    usage
    exit 0
    ;;
esac

cd "${REPO_ROOT}"

if [[ "${HW_VERIFY_ENABLED:-0}" != "1" ]]; then
  echo "[run_hw_verify] HW_VERIFY_ENABLED is not 1 in ${CONFIG_FILE} -- skipping hardware verification stage." >&2
  echo "[run_hw_verify] This is the default until a real HW_VERIFY_CMD is wired up; see docs/development/hardware_verification_loop.md." >&2
  exit 1
fi

if [[ -z "${HW_VERIFY_CMD:-}" ]]; then
  echo "[run_hw_verify] HW_VERIFY_ENABLED=1 but HW_VERIFY_CMD is not set in ${CONFIG_FILE}." >&2
  exit 1
fi

echo "[run_hw_verify] Running: ${HW_VERIFY_CMD}"
if eval "${HW_VERIFY_CMD}"; then
  STATUS=ok
else
  STATUS=failed
fi

REPEAT_TRIALS="${HW_VERIFY_REPEAT_TRIALS:-5}"

cat >"${STATE_DIR}/hw_verify.state" <<STATE
status=${STATUS}
command=${HW_VERIFY_CMD}
repeat_trials=${REPEAT_TRIALS}
timestamp=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
STATE

if [[ "${STATUS}" != "ok" ]]; then
  echo "[run_hw_verify] Hardware verification failed; see the evidence bundle emitted by HW_VERIFY_CMD for diagnosis." >&2
  exit 1
fi

echo "[run_hw_verify] Hardware verification passed (${REPEAT_TRIALS}/${REPEAT_TRIALS} trials)."
