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

"""Optional subprocess launcher for an EmbodiedGen install. Never imported as a package."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import shutil
import subprocess

from dimos.core.global_config import GlobalConfig
from dimos.simulation.embodiedgen.paths import embodiedgen_root

# Upstream console scripts (HorizonRobotics/EmbodiedGen). None are required by DimOS.
EMBODIEDGEN_CLIS = (
    "img3d-cli",
    "text3d-cli",
    "texture-cli",
    "layout-cli",
    "sim-cli",
    "room-cli",
    "scene3d-cli",
    "affordance-cli",
)


@dataclass(frozen=True)
class EmbodiedGenCliStatus:
    available: bool
    executable: Path | None
    name: str
    root: Path | None
    detail: str


def find_executable(
    name: str = "img3d-cli",
    *,
    config: GlobalConfig | None = None,
    root: Path | None = None,
) -> Path | None:
    """Locate an EmbodiedGen CLI on PATH or under ``EMBODIEDGEN_ROOT``.

    Returns ``None`` when EmbodiedGen is not installed. Does not import
    ``embodied_gen``.
    """
    which = shutil.which(name)
    if which:
        return Path(which)

    install = root or embodiedgen_root(config)
    if install is None:
        return None
    candidates = [
        install / name,
        install / "bin" / name,
        install / ".venv" / "bin" / name,
    ]
    for candidate in candidates:
        if candidate.is_file() and os_access_ok(candidate):
            return candidate
    return None


def os_access_ok(path: Path) -> bool:
    return path.exists()


def cli_status(
    name: str = "img3d-cli",
    *,
    config: GlobalConfig | None = None,
) -> EmbodiedGenCliStatus:
    root = embodiedgen_root(config)
    exe = find_executable(name, config=config, root=root)
    if exe is not None:
        return EmbodiedGenCliStatus(
            available=True,
            executable=exe,
            name=name,
            root=root,
            detail=f"{name} found at {exe}",
        )
    hint = (
        "EmbodiedGen is optional. Install the topsun-bot/EmbodiedGen fork "
        "separately and set EMBODIEDGEN_ROOT, or put img3d-cli on PATH."
    )
    return EmbodiedGenCliStatus(
        available=False,
        executable=None,
        name=name,
        root=root,
        detail=hint,
    )


def run_cli(
    args: list[str],
    *,
    name: str = "img3d-cli",
    config: GlobalConfig | None = None,
    timeout_s: float = 60.0,
) -> subprocess.CompletedProcess[str]:
    """Run an EmbodiedGen CLI if installed. Raises ``FileNotFoundError`` otherwise."""
    exe = find_executable(name, config=config)
    if exe is None:
        status = cli_status(name, config=config)
        raise FileNotFoundError(status.detail)
    return subprocess.run(
        [str(exe), *args],
        check=False,
        text=True,
        capture_output=True,
        timeout=timeout_s,
    )
