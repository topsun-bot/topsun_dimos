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

"""Minimal passive-viewer GL preflight (``python -m`` entry, spawn-safe)."""

from __future__ import annotations

import os
import sys
import time

_MINIMAL_VIEWER_PROBE_XML = """
<mujoco>
  <worldbody>
    <geom type="plane" size="1 1 0.1"/>
  </worldbody>
</mujoco>
"""


def run_viewer_probe() -> None:
    """Try opening/closing a minimal passive viewer; raise on failure."""
    import mujoco
    from mujoco import viewer

    from dimos.simulation.mujoco.display_env import (
        install_permissive_x11_error_handler,
        temporary_viewer_gl_env,
    )

    model = mujoco.MjModel.from_xml_string(_MINIMAL_VIEWER_PROBE_XML)
    data = mujoco.MjData(model)
    opened = False
    cleanup_error: BaseException | None = None
    install_permissive_x11_error_handler()
    with temporary_viewer_gl_env():
        try:
            handle = viewer.launch_passive(
                model,
                data,
                show_left_ui=False,
                show_right_ui=False,
            )
            with handle:
                if not handle.is_running():
                    raise RuntimeError("passive viewer is not running after launch")
                opened = True
                deadline = time.time() + 0.5
                while time.time() < deadline and handle.is_running():
                    mujoco.mj_step(model, data)
                    handle.sync()
                    time.sleep(0.01)
        except BaseException as exc:
            if opened:
                cleanup_error = exc
            else:
                raise
    if not opened:
        raise RuntimeError("passive viewer did not open")
    if cleanup_error is not None:
        # Some NVIDIA/GLX stacks raise GLXBadContext while destroying the probe window
        # even though launch_passive succeeded; treat that as a successful preflight.
        message = str(cleanup_error)
        if "GLXBadContext" not in message and "GLX" not in message:
            raise cleanup_error


def main() -> None:
    try:
        run_viewer_probe()
    except Exception:
        sys.exit(1)
    # GLFW/GLX teardown can SIGSEGV after a successful probe; skip normal exit.
    os._exit(0)


if __name__ == "__main__":
    main()
