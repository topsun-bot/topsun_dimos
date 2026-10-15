#!/usr/bin/env bash
# Runs as root. Keep bind-mounted /state readable and deletable by the host
# user without making every other file the eval process creates world-writable.
set -u

chmod_state() {
  if [[ -d /state ]]; then
    chmod -R a+rwX /state || true
  fi
}

child=""
forward() {
  if [[ -n "${child}" ]] && kill -0 "${child}" 2>/dev/null; then
    kill "-${1}" "${child}" 2>/dev/null || true
  fi
}

trap chmod_state EXIT
trap 'forward TERM' TERM
trap 'forward INT' INT

"$@" &
child=$!
wait "${child}"
status=$?
# A trapped signal interrupts wait(1); reap the child if it is still exiting.
if kill -0 "${child}" 2>/dev/null; then
  wait "${child}"
  status=$?
fi
exit "${status}"
