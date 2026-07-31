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

"""Standalone CLI for sending natural language commands to a running DimOS agent.

Usage:
    tell-robot "where is the charging station?"
    tell-robot "go to the kitchen and look around"
    tell-robot --timeout 30 "what do you see?"
"""

from __future__ import annotations

import argparse
import sys

from dimos.cli.tell import tell_robot


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Send a natural language command to the running DimOS robot agent",
    )
    parser.add_argument(
        "message",
        nargs="+",
        help="Natural language message to send (e.g. 'where is the charging station?')",
    )
    parser.add_argument(
        "-t",
        "--timeout",
        type=float,
        default=60.0,
        help="Max wait time in seconds (default: 60)",
    )
    parser.add_argument(
        "-q",
        "--quiet",
        action="store_true",
        help="Suppress status messages, only show agent responses",
    )
    args = parser.parse_args()

    message = " ".join(args.message)
    result = tell_robot(message, timeout=args.timeout, quiet=args.quiet)
    sys.exit(0 if result >= 0 else 1)


if __name__ == "__main__":
    main()
