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

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
import os
import subprocess
import sys
import threading
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    from dimos.core.global_config import GlobalConfig

OffscreenGlPreference = Literal["auto", "glfw", "egl", "osmesa", "off"]
OffscreenGlBackend = Literal["glfw", "egl", "osmesa"]

# Environment variables required for GLFW / XWayland / Wayland GUI contexts.
GUI_ENV_KEYS: tuple[str, ...] = (
    "DISPLAY",
    "WAYLAND_DISPLAY",
    "XAUTHORITY",
    "XDG_RUNTIME_DIR",
    "XDG_SESSION_TYPE",
    "GDK_BACKEND",
    "QT_QPA_PLATFORM",
)

# OpenGL / Mesa software-rendering overrides (printed by diagnose, set for OSMesa fallback).
OPENGL_DIAGNOSTIC_ENV_KEYS: tuple[str, ...] = (
    "MUJOCO_GL",
    "__GLX_VENDOR_LIBRARY_NAME",
    "LIBGL_ALWAYS_SOFTWARE",
    "LIBGL_ALWAYS_INDIRECT",
    "LIBGL_DRIVERS_PATH",
    "PYOPENGL_PLATFORM",
)

OSMESA_SOFTWARE_ENV_KEYS: tuple[str, ...] = (
    "LIBGL_ALWAYS_SOFTWARE",
    "__GLX_VENDOR_LIBRARY_NAME",
    "PYOPENGL_PLATFORM",
)

# Captured once from the dimos main process (after load_dotenv) so forkserver
# workers can forward GUI vars even if their inherited environ was stripped.
_gui_env_snapshot: dict[str, str] = {}


def snapshot_gui_env(environ: os._Environ[str] | None = None) -> dict[str, str]:
    """Record GUI-related variables from *environ* (default: os.environ)."""
    global _gui_env_snapshot
    env = environ if environ is not None else os.environ
    _gui_env_snapshot = {key: env[key] for key in GUI_ENV_KEYS if env.get(key)}
    if env.get("MUJOCO_GL"):
        _gui_env_snapshot["MUJOCO_GL"] = env["MUJOCO_GL"]
    return dict(_gui_env_snapshot)


def has_graphical_display(environ: os._Environ[str] | None = None) -> bool:
    """Return True when a Linux/Wayland or X11 display socket is available."""
    env = environ if environ is not None else os.environ
    return bool(env.get("DISPLAY") or env.get("WAYLAND_DISPLAY"))


def build_mujoco_subprocess_env(
    base_env: dict[str, str] | os._Environ[str] | None = None,
) -> dict[str, str]:
    """Copy *base_env* and ensure GUI-related variables are forwarded."""
    merged = dict(base_env if base_env is not None else os.environ)
    for key, value in _gui_env_snapshot.items():
        merged[key] = value
    for key in GUI_ENV_KEYS:
        value = os.environ.get(key)
        if value is not None:
            merged[key] = value
    mujoco_gl = os.environ.get("MUJOCO_GL")
    if mujoco_gl is not None:
        merged["MUJOCO_GL"] = mujoco_gl
    return merged


def explain_mujoco_headless(
    config: GlobalConfig, environ: os._Environ[str] | None = None
) -> tuple[bool, str]:
    """Return (headless, human-readable reason) for MuJoCo passive viewer mode."""
    env = environ if environ is not None else os.environ

    if config.mujoco_headless:
        return True, "GlobalConfig.mujoco_headless=True (--mujoco-headless / MUJOCO_HEADLESS)"
    if not config.mujoco_viewer:
        return True, "GlobalConfig.mujoco_viewer=False (--no-mujoco-viewer)"

    if sys.platform != "darwin" and not has_graphical_display(env):
        return True, "no DISPLAY or WAYLAND_DISPLAY in subprocess environment"

    return (
        False,
        "interactive viewer requested (DISPLAY/WAYLAND available; offscreen GL uses isolated backend)",
    )


def should_run_mujoco_headless(
    config: GlobalConfig, environ: os._Environ[str] | None = None
) -> bool:
    headless, _reason = explain_mujoco_headless(config, environ)
    return headless


def decide_offscreen_gl_backend(
    config: GlobalConfig,
    environ: os._Environ[str] | None = None,
) -> OffscreenGlBackend | Literal["off"]:
    """Resolve GlobalConfig.mujoco_offscreen_gl to a concrete backend (never ``auto``)."""
    preference = config.mujoco_offscreen_gl
    if preference == "off":
        return "off"
    if preference in ("glfw", "egl", "osmesa"):
        return preference

    env = environ if environ is not None else os.environ
    env_gl = env.get("MUJOCO_GL", "").lower()
    if env_gl in ("glfw", "egl", "osmesa"):
        return env_gl  # type: ignore[return-value]

    return "egl"


def offscreen_gl_try_order(
    config: GlobalConfig,
    environ: os._Environ[str] | None = None,
) -> list[OffscreenGlBackend]:
    """Backends to attempt in order when creating offscreen ``mujoco.Renderer`` instances."""
    resolved = decide_offscreen_gl_backend(config, environ)
    if resolved == "off":
        return []
    if config.mujoco_offscreen_gl == "auto" and resolved == "egl":
        return ["egl", "osmesa", "glfw"]
    return [resolved]


VIEWER_LAUNCH_TIMEOUT_SEC = 10.0

VIEWER_GL_ENV_KEYS: tuple[str, ...] = (
    "MUJOCO_GL",
    "PYOPENGL_PLATFORM",
    "__GLX_VENDOR_LIBRARY_NAME",
    "LIBGL_ALWAYS_INDIRECT",
    "LIBGL_ALWAYS_SOFTWARE",
)


def check_nvidia_driver_mismatch() -> str | None:
    """Return a human-readable hint when ``nvidia-smi`` reports driver/library mismatch."""
    try:
        result = subprocess.run(
            ["nvidia-smi"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    combined = (result.stdout or "") + (result.stderr or "")
    if "Driver/library version mismatch" in combined or "Failed to initialize NVML" in combined:
        return (
            "NVIDIA driver/library version mismatch (nvidia-smi failed). "
            "Reboot after driver updates (do not modprobe -r nvidia* on a live session); "
            "GLX BadValue / could not create window often follow until fixed."
        )
    return None


_x11_error_handler_ref: Any | None = None


def install_permissive_x11_error_handler() -> None:
    """Ignore X11 GLX teardown errors so passive-viewer preflight can exit cleanly."""
    global _x11_error_handler_ref
    if _x11_error_handler_ref is not None:
        return
    import ctypes
    import ctypes.util

    libname = ctypes.util.find_library("X11")
    if not libname:
        return
    x11 = ctypes.CDLL(libname)
    XErrorHandler = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p)

    @XErrorHandler
    def _handler(display: ctypes.c_void_p, event: ctypes.c_void_p) -> int:
        return 0

    x11.XSetErrorHandler(_handler)
    _x11_error_handler_ref = _handler


@contextmanager
def temporary_viewer_gl_env():
    """GL env for passive viewer (GLFW/GLX); isolated from offscreen EGL/OSMesa."""
    previous = {key: os.environ.get(key) for key in VIEWER_GL_ENV_KEYS}
    os.environ["MUJOCO_GL"] = "glfw"
    os.environ.pop("PYOPENGL_PLATFORM", None)
    if os.environ.get("LIBGL_ALWAYS_SOFTWARE") == "1":
        os.environ["__GLX_VENDOR_LIBRARY_NAME"] = "mesa"
    elif os.environ.get("__GLX_VENDOR_LIBRARY_NAME") == "mesa":
        os.environ.pop("__GLX_VENDOR_LIBRARY_NAME", None)
    os.environ.pop("LIBGL_ALWAYS_INDIRECT", None)
    try:
        yield
    finally:
        for key in VIEWER_GL_ENV_KEYS:
            value = previous[key]
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def probe_passive_viewer_gl(timeout: float = VIEWER_LAUNCH_TIMEOUT_SEC) -> bool:
    """Return True when a child process can create a GLFW/GLX window within *timeout* seconds."""
    env = build_mujoco_subprocess_env()
    try:
        result = subprocess.run(
            [sys.executable, "-m", "dimos.simulation.mujoco.viewer_probe"],
            env=env,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return False
    return result.returncode == 0


def _launch_passive_on_main_thread(
    model: Any,
    data: Any,
    *,
    show_left_ui: bool,
    show_right_ui: bool,
) -> Any:
    """Call ``viewer.launch_passive`` on the main thread (GLFW/GLX requirement)."""
    from mujoco import viewer

    try:
        import glfw
    except ImportError:
        glfw = None  # type: ignore[assignment]

    previous_callback = None
    if glfw is not None:
        if hasattr(glfw, "get_error_callback"):
            previous_callback = glfw.get_error_callback()

        def _glfw_error(code: int, message: str) -> None:
            raise RuntimeError(f"GLFW error {code}: {message}")

        glfw.set_error_callback(_glfw_error)
    try:
        return viewer.launch_passive(
            model,
            data,
            show_left_ui=show_left_ui,
            show_right_ui=show_right_ui,
        )
    finally:
        if glfw is not None and previous_callback is not None:
            glfw.set_error_callback(previous_callback)


@contextmanager
def launch_passive_with_timeout(
    model: Any,
    data: Any,
    *,
    timeout: float = VIEWER_LAUNCH_TIMEOUT_SEC,
    show_left_ui: bool = False,
    show_right_ui: bool = False,
) -> Iterator[Any]:
    """Open passive viewer on the main thread (GLFW/GLX must not run in a worker thread).

    Runs a short-lived preflight subprocess first so a broken GLX stack cannot block the
    simulation process indefinitely (``ERROR: could not create window`` hangs).
    """
    if threading.current_thread() is not threading.main_thread():
        raise RuntimeError(
            "launch_passive_with_timeout must run on the main thread (GLFW/GLX requirement)"
        )

    install_permissive_x11_error_handler()

    if not probe_passive_viewer_gl(timeout=timeout):
        nvidia_hint = check_nvidia_driver_mismatch()
        detail = (
            "MuJoCo passive viewer GLX/GLFW preflight failed "
            f"(no window within {timeout}s)"
        )
        if nvidia_hint:
            detail = f"{detail}; {nvidia_hint}"
        raise RuntimeError(detail)

    handle = _launch_passive_on_main_thread(
        model,
        data,
        show_left_ui=show_left_ui,
        show_right_ui=show_right_ui,
    )
    with handle:
        yield handle


@contextmanager
def temporary_mujoco_gl(backend: OffscreenGlBackend):
    """Temporarily set ``MUJOCO_GL`` without affecting the passive viewer's GLX/GLFW context."""
    previous = os.environ.get("MUJOCO_GL")
    os.environ["MUJOCO_GL"] = backend
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop("MUJOCO_GL", None)
        else:
            os.environ["MUJOCO_GL"] = previous


@contextmanager
def temporary_osmesa_software_env():
    """Set OSMesa + Mesa software GL overrides; restore previous values on exit."""
    keys = ("MUJOCO_GL", *OSMESA_SOFTWARE_ENV_KEYS)
    previous = {key: os.environ.get(key) for key in keys}
    os.environ["MUJOCO_GL"] = "osmesa"
    os.environ["LIBGL_ALWAYS_SOFTWARE"] = "1"
    os.environ["__GLX_VENDOR_LIBRARY_NAME"] = "mesa"
    os.environ["PYOPENGL_PLATFORM"] = "osmesa"
    try:
        yield
    finally:
        for key in keys:
            value = previous[key]
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def patch_mujoco_renderer_del() -> None:
    """Silence ``AttributeError: '_mjr_context'`` when ``Renderer.__init__`` fails."""
    try:
        import mujoco
    except ImportError:
        return

    if getattr(mujoco.Renderer, "_dimos_del_patched", False):
        return

    original_del = mujoco.Renderer.__del__

    def _safe_renderer_del(self: object) -> None:
        if not hasattr(self, "_mjr_context"):
            return
        try:
            original_del(self)
        except AttributeError:
            pass

    mujoco.Renderer.__del__ = _safe_renderer_del  # type: ignore[method-assign]
    mujoco.Renderer._dimos_del_patched = True


def suppress_repeat_glfw_gl_errors() -> None:
    """After the first GLFW GL context failure, hide duplicate ``GLFWError`` warnings."""
    import warnings

    try:
        from glfw import GLFWError
    except ImportError:
        return

    warnings.filterwarnings("ignore", category=GLFWError)


def collect_opengl_env(environ: os._Environ[str] | None = None) -> dict[str, str]:
    """Return OpenGL-related env vars present in *environ* (default: os.environ)."""
    env = environ if environ is not None else os.environ
    collected: dict[str, str] = {}
    for key in (*GUI_ENV_KEYS, *OPENGL_DIAGNOSTIC_ENV_KEYS):
        value = env.get(key)
        if value is not None:
            collected[key] = value
    for key, value in env.items():
        if key.startswith("LIBGL_") and key not in collected:
            collected[key] = value
    return collected
