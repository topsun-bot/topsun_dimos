#!/usr/bin/env python3
# Copyright 2025-2026 Dimensional Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#
# /// script
# requires-python = ">=3.10"
# dependencies = ["unitree-webrtc-connect>=2.1.2"]
# ///
"""Fetch a Unitree robot's per-device AES-128 key from the bound cloud account.

Usage:
    uv run scripts/fetch_unitree_aes_key.py <account> <password> <sn>
    uv run scripts/fetch_unitree_aes_key.py <account> <password> --all

The key is printed to stdout. Diagnostics are printed to stderr.
"""

from __future__ import annotations

import argparse
import sys
from typing import NoReturn


def _die(message: str, code: int = 1) -> NoReturn:
    print(message, file=sys.stderr)
    raise SystemExit(code)


def _sn_match_key(sn: str) -> str:
    # The machine screen font makes O/0 easy to confuse. Use this only as a
    # fallback after exact matching so real serials still round-trip untouched.
    return sn.strip().upper().replace("O", "0")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Fetch a Go2/G1 AES-128 key from a Unitree cloud account."
    )
    parser.add_argument("account", help="Unitree account email or phone number")
    parser.add_argument("password", help="Unitree account password")
    parser.add_argument(
        "sn",
        nargs="?",
        help="Robot serial number, e.g. B42D2000Q4O8LD03",
    )
    parser.add_argument(
        "--region",
        choices=("cn", "global"),
        default="cn",
        help="Unitree cloud region. China accounts usually use cn. Default: cn",
    )
    parser.add_argument(
        "--device-type",
        choices=("Go2", "G1"),
        default="Go2",
        help="Unitree device type. Default: Go2",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="List bound devices to stderr before selecting the requested SN.",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Print all bound devices and their keys as TSV.",
    )
    return parser.parse_args()


def main() -> None:
    args = _parse_args()

    try:
        from unitree_webrtc_connect.unitree_cloud import UnitreeCloud, UnitreeCloudError
    except ImportError as e:
        _die(
            "unitree-webrtc-connect>=2.1.2 is required. "
            "Run with: uv run scripts/fetch_unitree_aes_key.py ...\n"
            f"Import error: {e}"
        )

    try:
        cloud = UnitreeCloud(region=args.region, device_type=args.device_type)
        cloud.login_email(args.account, args.password)
        devices = cloud.list_devices()
    except UnitreeCloudError as e:
        _die(f"Unitree cloud error: {e}")
    except Exception as e:
        _die(f"Failed to query Unitree cloud: {type(e).__name__}: {e}")

    if args.list:
        for d in devices:
            has_key = "yes" if d.key else "no"
            print(
                f"SN={d.sn} alias={d.alias or '-'} model={d.model or d.series or '-'} "
                f"online={d.online} has_key={has_key}",
                file=sys.stderr,
            )

    if args.all:
        print("sn\talias\tmodel\tonline\tkey")
        for d in devices:
            print(f"{d.sn}\t{d.alias or '-'}\t{d.model or d.series or '-'}\t{d.online}\t{d.key}")
        return

    if not args.sn:
        _die("SN is required unless --all is set.")

    requested = args.sn.strip().upper()
    matches = [d for d in devices if d.sn.strip().upper() == requested]
    if not matches:
        normalized = _sn_match_key(requested)
        matches = [d for d in devices if _sn_match_key(d.sn) == normalized]

    if not matches:
        print(f"No bound device matched SN {args.sn!r}. Bound devices:", file=sys.stderr)
        for d in devices:
            print(
                f"  SN={d.sn} alias={d.alias or '-'} model={d.model or d.series or '-'}",
                file=sys.stderr,
            )
        raise SystemExit(1)

    if len(matches) > 1:
        print(f"SN {args.sn!r} matched multiple devices after O/0 normalization:", file=sys.stderr)
        for d in matches:
            print(f"  SN={d.sn} alias={d.alias or '-'}", file=sys.stderr)
        raise SystemExit(1)

    device = matches[0]
    if not device.key:
        _die(f"Device {device.sn} is bound, but the cloud returned an empty key.")

    print(
        f"matched SN={device.sn} alias={device.alias or '-'} model={device.model or device.series or '-'}",
        file=sys.stderr,
    )
    print(device.key)


if __name__ == "__main__":
    main()
