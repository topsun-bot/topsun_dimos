#!/usr/bin/env bash
# Enable aarch64-linux nix builds on an x86_64 Ubuntu laptop (QEMU binfmt).
# Required before laptop_prebuild_nav_for_orin.sh — without this you get:
#   "Exec format error" when nix tries to run aarch64 bash on x86.
#
# Usage (laptop, once):
#   bash scripts/laptop_setup_nix_aarch64.sh
#   . /nix/var/nix/profiles/default/etc/profile.d/nix-daemon.sh
#   nix run nixpkgs#hello --system aarch64-linux

set -euo pipefail

log() { echo "[$(date '+%H:%M:%S')] $*"; }

if [[ "$(uname -m)" != "x86_64" ]]; then
    log "This script is for x86_64 laptops only."
    exit 0
fi

log "Installing QEMU user-static + binfmt (sudo required)..."
sudo apt-get update
sudo apt-get install -y qemu-user-static binfmt-support

if systemctl is-active systemd-binfmt.service >/dev/null 2>&1; then
    sudo systemctl restart systemd-binfmt.service
fi

if ls /proc/sys/fs/binfmt_misc/qemu-aarch64* >/dev/null 2>&1; then
    log "binfmt qemu-aarch64 registered."
else
    log "binfmt entry not found — trying multiarch registration..."
    if command -v docker >/dev/null 2>&1; then
        sudo docker run --rm --privileged multiarch/qemu-user-static --reset -p yes
    else
        log "WARNING: install docker or reboot after qemu-user-static install."
    fi
fi

if ! grep -q 'extra-platforms = aarch64-linux' /etc/nix/nix.custom.conf 2>/dev/null; then
    log "Adding trusted-users / extra-platforms to /etc/nix/nix.custom.conf ..."
    sudo tee -a /etc/nix/nix.custom.conf <<EOF

# dimos: cross-build aarch64 for G1 Orin
trusted-users = root $(whoami)
extra-platforms = aarch64-linux
EOF
    sudo systemctl restart nix-daemon
fi

if [[ -f /nix/var/nix/profiles/default/etc/profile.d/nix-daemon.sh ]]; then
    # shellcheck disable=SC1091
    . /nix/var/nix/profiles/default/etc/profile.d/nix-daemon.sh
fi

if command -v nix >/dev/null 2>&1; then
    log "Smoke test: nix run hello for aarch64-linux (may download once)..."
    nix run nixpkgs#hello --system aarch64-linux
    log "OK — laptop can build aarch64-linux derivations."
else
    log "Nix not in PATH. Source nix-daemon.sh then run:"
    log "  nix run nixpkgs#hello --system aarch64-linux"
fi
