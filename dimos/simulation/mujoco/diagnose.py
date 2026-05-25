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

"""Print MuJoCo viewer / headless diagnostics for the current environment."""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys

from dimos.core.global_config import GlobalConfig
from dimos.simulation.mujoco.display_env import (
    GUI_ENV_KEYS,
    OPENGL_DIAGNOSTIC_ENV_KEYS,
    build_mujoco_subprocess_env,
    check_nvidia_driver_mismatch,
    collect_opengl_env,
    decide_offscreen_gl_backend,
    explain_mujoco_headless,
    has_graphical_display,
    offscreen_gl_try_order,
    snapshot_gui_env,
)

_SHELL_COMMANDS: tuple[tuple[str, list[str]], ...] = (
    ("glxinfo -B", ["glxinfo", "-B"]),
    ("eglinfo -B", ["eglinfo", "-B"]),
    (
        "ldconfig -p | grep -E 'libGL|libEGL|libOSMesa'",
        ["sh", "-c", "ldconfig -p | grep -E 'libGL|libEGL|libOSMesa' || true"],
    ),
    ("nvidia-smi", ["nvidia-smi"]),
)

_RECOMMENDED_COMMANDS: tuple[str, ...] = (
    "# Popup viewer (do NOT use --no-mujoco-viewer); GLX preflight runs viewer_probe first:",
    'GDK_BACKEND=x11 MUJOCO_GL=glfw CUDA_VISIBLE_DEVICES="" uv run dimos --simulation mujoco '
    "--mujoco-offscreen-gl off --viewer none run unitree-go2",
    "# Success log: MuJoCo passive viewer opened successfully",
    "# NVIDIA Driver/library version mismatch: sudo reboot (do NOT modprobe -r nvidia*)",
    "bash scripts/fix-nvidia-driver.sh",
    "sudo apt install libosmesa6 mesa-utils mesa-utils-extra libgl1-mesa-glx",
    "LIBGL_ALWAYS_SOFTWARE=1 MUJOCO_GL=osmesa uv run dimos --simulation mujoco "
    "--no-mujoco-viewer --mujoco-offscreen-gl osmesa --viewer none run unitree-go2",
    "uv run dimos --simulation mujoco --no-mujoco-viewer --mujoco-offscreen-gl off "
    "--viewer none run unitree-go2",
    "uv run python -m dimos.simulation.mujoco.viewer_probe  # standalone GLX preflight",
    "glxinfo -B | head -20",
    "eglinfo -B | head -20",
    "ldconfig -p | grep -E 'libGL|libEGL|libOSMesa'",
    "nvidia-smi",
)


def _print_env_section(title: str, env: dict[str, str]) -> None:
    print(title)
    for key in (*GUI_ENV_KEYS, *OPENGL_DIAGNOSTIC_ENV_KEYS):
        if key in env:
            print(f"  {key}={env[key]!r}")
    for key in sorted(env):
        if key.startswith("LIBGL_") and key not in (*GUI_ENV_KEYS, *OPENGL_DIAGNOSTIC_ENV_KEYS):
            print(f"  {key}={env[key]!r}")
    print()


def _run_shell_diagnostics() -> None:
    print("=== Shell OpenGL diagnostics (failures tolerated) ===")
    for label, cmd in _SHELL_COMMANDS:
        executable = cmd[0] if cmd[0] != "sh" else "sh"
        if executable != "sh" and shutil.which(executable) is None:
            print(f"--- {label} ---")
            print(f"  (skipped: {executable!r} not found in PATH)")
            print()
            continue
        print(f"--- {label} ---")
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
            output = (result.stdout or "") + (result.stderr or "")
            if output.strip():
                for line in output.rstrip().splitlines():
                    print(f"  {line}")
            else:
                print(f"  (no output, exit code {result.returncode})")
        except subprocess.TimeoutExpired:
            print("  (timed out after 30s)")
        except OSError as exc:
            print(f"  (failed: {exc})")
        print()


def _print_recommendations() -> None:
    print("=== Recommended troubleshooting ===")
    print(
        "If all MuJoCo GL backends fail (gladLoadGL / GLX BadValue / CUDA Error 804), "
        "the host OpenGL stack is likely broken (NVIDIA driver mismatch or missing Mesa OSMesa)."
    )
    print(
        "Passive viewer: preflight subprocess (viewer_probe) must exit 0 within 10s; "
        "then launch_passive on the main thread. Do not use --no-mujoco-viewer for popup."
    )
    print(
        "NVIDIA 'Driver/library version mismatch' or NVML init failure: reboot required; "
        "avoid modprobe -r nvidia* (can break the session)."
    )
    print(
        "Full MVP runbook: dimos/experimental/navigation_obstacle_mvp/README.md "
        "(popup vs headless table)."
    )
    print()
    for command in _RECOMMENDED_COMMANDS:
        print(f"  {command}")
    print()


def main() -> None:
    parser = argparse.ArgumentParser(description="MuJoCo viewer / OpenGL diagnostics")
    parser.add_argument(
        "--shell",
        action="store_true",
        help="Run glxinfo, eglinfo, ldconfig, nvidia-smi (failures tolerated)",
    )
    args = parser.parse_args()

    gui_snapshot = snapshot_gui_env()
    config = GlobalConfig()
    headless, reason = explain_mujoco_headless(config)
    subprocess_env = build_mujoco_subprocess_env()
    current_opengl_env = collect_opengl_env()

    print("=== MuJoCo viewer diagnostics ===")
    print(f"platform: {sys.platform}")
    print(f"mujoco_viewer: {config.mujoco_viewer}")
    print(f"mujoco_headless: {config.mujoco_headless}")
    print(f"mujoco_offscreen_gl: {config.mujoco_offscreen_gl}")
    print(f"offscreen_gl_resolved: {decide_offscreen_gl_backend(config)}")
    print(f"offscreen_gl_try_order: {offscreen_gl_try_order(config)}")
    print(f"has_graphical_display: {has_graphical_display()}")
    print(f"should_run_mujoco_headless: {headless}")
    print(f"reason: {reason}")
    nvidia_hint = check_nvidia_driver_mismatch()
    if nvidia_hint:
        print(f"nvidia_driver_hint: {nvidia_hint}")
    print()
    _print_env_section("--- OpenGL / display environment (current process) ---", current_opengl_env)
    _print_env_section(
        "--- GUI env snapshot (dimos main process) ---",
        {
            k: gui_snapshot[k]
            for k in (*GUI_ENV_KEYS, *OPENGL_DIAGNOSTIC_ENV_KEYS)
            if k in gui_snapshot
        },
    )
    _print_env_section(
        "--- GUI environment (MuJoCo subprocess copy) ---",
        collect_opengl_env(subprocess_env),
    )

    if args.shell:
        _run_shell_diagnostics()

    _print_recommendations()


if __name__ == "__main__":
    main()
