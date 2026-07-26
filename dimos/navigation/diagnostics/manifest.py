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

"""Immutable run-level metadata for navigation diagnostics."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import platform
import subprocess
import time
from typing import Any

from pydantic import BaseModel

from dimos.core.global_config import GlobalConfig
from dimos.navigation.diagnostics.schema import NAVIGATION_TRACE_SCHEMA_VERSION
from dimos.navigation.diagnostics.sink import redact_sensitive

_SECRET_OPTIONS = (
    "--password",
    "--passwd",
    "--token",
    "--api-key",
    "--apikey",
    "--secret",
    "--unitree-password",
    "--unitree-username",
    "--unitree-aes-128-key",
    "--unitree-serial",
)


def write_navigation_manifest(
    run_log_dir: Path,
    *,
    run_id: str,
    blueprint: str,
    argv: Sequence[str],
    global_settings: GlobalConfig,
    resolved_blueprint_config: BaseModel | Mapping[str, Any],
    repository: Path,
) -> Path | None:
    """Write the immutable navigation manifest exactly once.

    Trace-off runs create no navigation directory. Failures are deliberately
    non-fatal to robot startup and are reported by returning ``None``.
    """
    if global_settings.navigation_trace_level == "off":
        return None

    navigation_dir = run_log_dir / "navigation"
    path = navigation_dir / "manifest.json"
    try:
        navigation_dir.mkdir(parents=True, exist_ok=True)
        config_snapshot = _config_snapshot(resolved_blueprint_config)
        manifest = {
            "schema_version": NAVIGATION_TRACE_SCHEMA_VERSION,
            "manifest_kind": "navigation_run",
            "created_wall_ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
            "run_id": run_id,
            "blueprint": blueprint,
            "git": _git_metadata(repository),
            "runtime": {
                "hostname": platform.node(),
                "platform": platform.platform(),
                "python": platform.python_version(),
                "pid": os.getpid(),
                "monotonic_clock": _clock_metadata(),
            },
            "command": redact_argv(argv),
            "connection_method": global_settings.unitree_connection_type,
            "trace": {
                key: value
                for key, value in global_settings.model_dump(mode="json").items()
                if key.startswith("navigation_trace_")
            },
            "global_config": global_settings.model_dump(mode="json"),
            "resolved_blueprint_config": config_snapshot,
            "map_inputs": _find_map_inputs(config_snapshot),
            "notes": {
                "navigation_sessions_reconstructed_offline": True,
                "map_hash_deferred_to_offline_analysis": True,
                "send_is_robot_execution_ack": False,
                "odom_is_external_ground_truth": False,
            },
        }
        with path.open("x", encoding="utf-8") as stream:
            json.dump(
                redact_sensitive(manifest),
                stream,
                ensure_ascii=False,
                allow_nan=False,
                indent=2,
                default=str,
            )
            stream.write("\n")
        return path
    except FileExistsError:
        return path
    except (OSError, TypeError, ValueError):
        return None


def redact_argv(argv: Sequence[str]) -> list[str]:
    """Redact both ``--secret value`` and ``--secret=value`` forms."""
    output: list[str] = []
    redact_next = False
    for raw_arg in argv:
        arg = str(raw_arg)
        if redact_next:
            output.append("<redacted>")
            redact_next = False
            continue
        option, separator, _value = arg.partition("=")
        lowered = option.lower()
        if lowered in _SECRET_OPTIONS:
            if separator:
                output.append(f"{option}=<redacted>")
            else:
                output.append(option)
                redact_next = True
            continue
        output.append(str(redact_sensitive(arg)))
    return output


def _git_metadata(repository: Path) -> dict[str, Any]:
    return {
        "branch": _git_value(repository, "rev-parse", "--abbrev-ref", "HEAD"),
        "commit": _git_value(repository, "rev-parse", "HEAD"),
        "dirty": bool(_git_value(repository, "status", "--porcelain")),
    }


def _config_snapshot(
    config: BaseModel | Mapping[str, Any],
) -> dict[str, Any]:
    if isinstance(config, BaseModel):
        try:
            return config.model_dump(
                mode="python",
                warnings=False,
                fallback=str,
            )
        except Exception as exc:
            return {
                "snapshot_error": type(exc).__name__,
                "fallback_omitted": "model repr may contain credentials",
            }
    return dict(config)


def _git_value(repository: Path, *args: str) -> str | None:
    try:
        completed = subprocess.run(
            ["git", *args],
            cwd=repository,
            check=False,
            capture_output=True,
            text=True,
            timeout=2.0,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if completed.returncode != 0:
        return None
    return completed.stdout.strip()


def _clock_metadata() -> dict[str, Any]:
    clock = time.get_clock_info("monotonic")
    return {
        "implementation": clock.implementation,
        "monotonic": clock.monotonic,
        "adjustable": clock.adjustable,
        "resolution_sec": clock.resolution,
        "cross_process_comparison": "same_host_only",
    }


def _find_map_inputs(value: Any, prefix: str = "") -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    if isinstance(value, Mapping):
        for key, child in value.items():
            child_prefix = f"{prefix}.{key}" if prefix else str(key)
            if str(key) in {"map_file", "map_key"} and child is not None:
                item: dict[str, Any] = {"config_path": child_prefix, "value": child}
                candidate = Path(str(child)).expanduser()
                if candidate.is_file():
                    stat = candidate.stat()
                    item["resolved_path"] = str(candidate.resolve())
                    item["size_bytes"] = stat.st_size
                    item["mtime_ns"] = stat.st_mtime_ns
                found.append(item)
            found.extend(_find_map_inputs(child, child_prefix))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            found.extend(_find_map_inputs(child, f"{prefix}[{index}]"))
    return found
