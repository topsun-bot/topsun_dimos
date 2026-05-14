#!/usr/bin/env bash
set -euo pipefail

if [[ "$(uname -s)" != "Darwin" ]]; then
  echo "This helper is only for macOS." >&2
  exit 1
fi

if [[ $# -eq 0 ]]; then
  set -- dimos --replay run unitree-go2
fi

ROUTE_NET="224.0.0.0/4"
ORIGINAL_IFACE="$(
  netstat -rn -f inet \
    | awk '$1 == "224.0.0/4" || $1 == "224.0.0.0/4" {print $4; exit}'
)"

restore_route() {
  local exit_code=$?

  sudo -n route -n delete -net "$ROUTE_NET" >/dev/null 2>&1 || true

  if [[ -n "${ORIGINAL_IFACE}" && "${ORIGINAL_IFACE}" != "lo0" ]]; then
    sudo -n route -n add -net "$ROUTE_NET" -interface "$ORIGINAL_IFACE" >/dev/null 2>&1 || {
      echo "Warning: failed to restore multicast route to ${ORIGINAL_IFACE}." >&2
      echo "If local network discovery looks broken, toggle Wi-Fi or run:" >&2
      echo "  sudo route -n add -net ${ROUTE_NET} -interface ${ORIGINAL_IFACE}" >&2
    }
  fi

  exit "$exit_code"
}

# Ask for sudo once while the terminal is still interactive. The cleanup path uses
# sudo -n so it will not hang waiting for a password during process teardown.
sudo -v

trap restore_route EXIT INT TERM

sudo route -n delete -net "$ROUTE_NET" >/dev/null 2>&1 || true
sudo route -n add -net "$ROUTE_NET" -interface lo0 >/dev/null

echo "Temporarily routed ${ROUTE_NET} to lo0 for local LCM."
echo "Running: $*"

"$@"
