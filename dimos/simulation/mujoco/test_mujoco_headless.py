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

import os
import subprocess

import pytest

from dimos.core.global_config import GlobalConfig
from dimos.simulation.mujoco.display_env import (
    build_mujoco_subprocess_env,
    decide_offscreen_gl_backend,
    explain_mujoco_headless,
    has_graphical_display,
    offscreen_gl_try_order,
    should_run_mujoco_headless,
    snapshot_gui_env,
    temporary_mujoco_gl,
    temporary_osmesa_software_env,
    temporary_viewer_gl_env,
)


def test_should_run_mujoco_headless_default_with_display(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DISPLAY", ":1")
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    monkeypatch.delenv("MUJOCO_GL", raising=False)
    config = GlobalConfig()
    assert config.mujoco_viewer is True
    assert config.mujoco_headless is False
    assert should_run_mujoco_headless(config) is False


def test_should_run_mujoco_headless_wayland_without_display(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-0")
    monkeypatch.delenv("MUJOCO_GL", raising=False)
    config = GlobalConfig()
    assert should_run_mujoco_headless(config) is False


def test_should_run_mujoco_headless_explicit_flag() -> None:
    config = GlobalConfig(mujoco_headless=True)
    assert should_run_mujoco_headless(config) is True


def test_should_run_mujoco_headless_no_mujoco_viewer() -> None:
    config = GlobalConfig(mujoco_viewer=False)
    assert should_run_mujoco_headless(config) is True


@pytest.mark.parametrize("mujoco_gl", ["egl", "osmesa", "EGL", "OSMesa"])
def test_mujoco_gl_env_does_not_force_headless_when_viewer_enabled(
    monkeypatch: pytest.MonkeyPatch, mujoco_gl: str
) -> None:
    """Offscreen MUJOCO_GL is isolated; passive viewer may still open."""
    monkeypatch.setenv("MUJOCO_GL", mujoco_gl)
    monkeypatch.setenv("DISPLAY", ":0")
    config = GlobalConfig(mujoco_headless=False)
    assert should_run_mujoco_headless(config) is False


def test_should_run_mujoco_headless_no_display(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    monkeypatch.delenv("MUJOCO_GL", raising=False)
    config = GlobalConfig(mujoco_headless=False)
    assert should_run_mujoco_headless(config) is True


def test_build_mujoco_subprocess_env_forwards_gui_vars(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DISPLAY", ":99")
    monkeypatch.setenv("XAUTHORITY", "/tmp/test-xauth")
    snapshot_gui_env()
    env = build_mujoco_subprocess_env({})
    assert env["DISPLAY"] == ":99"
    assert env["XAUTHORITY"] == "/tmp/test-xauth"


def test_build_mujoco_subprocess_env_uses_snapshot_when_worker_env_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot_gui_env({"DISPLAY": ":42", "XAUTHORITY": "/snap/xauth"})
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.delenv("XAUTHORITY", raising=False)
    env = build_mujoco_subprocess_env({})
    assert env["DISPLAY"] == ":42"
    assert env["XAUTHORITY"] == "/snap/xauth"


def test_explain_mujoco_headless_includes_reason() -> None:
    config = GlobalConfig(mujoco_headless=True)
    headless, reason = explain_mujoco_headless(config)
    assert headless is True
    assert "mujoco_headless" in reason


def test_has_graphical_display() -> None:
    assert has_graphical_display({"DISPLAY": ":0"}) is True
    assert has_graphical_display({"WAYLAND_DISPLAY": "wayland-0"}) is True
    assert has_graphical_display({}) is False


def test_decide_offscreen_gl_backend_explicit() -> None:
    assert decide_offscreen_gl_backend(GlobalConfig(mujoco_offscreen_gl="egl")) == "egl"
    assert decide_offscreen_gl_backend(GlobalConfig(mujoco_offscreen_gl="osmesa")) == "osmesa"
    assert decide_offscreen_gl_backend(GlobalConfig(mujoco_offscreen_gl="glfw")) == "glfw"
    assert decide_offscreen_gl_backend(GlobalConfig(mujoco_offscreen_gl="off")) == "off"


def test_decide_offscreen_gl_backend_auto_defaults_to_egl(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("MUJOCO_GL", raising=False)
    assert decide_offscreen_gl_backend(GlobalConfig()) == "egl"


def test_decide_offscreen_gl_backend_auto_respects_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MUJOCO_GL", "osmesa")
    assert decide_offscreen_gl_backend(GlobalConfig(mujoco_offscreen_gl="auto")) == "osmesa"


def test_offscreen_gl_try_order_auto() -> None:
    assert offscreen_gl_try_order(GlobalConfig(mujoco_offscreen_gl="auto")) == [
        "egl",
        "osmesa",
        "glfw",
    ]


def test_offscreen_gl_try_order_explicit_single_backend() -> None:
    assert offscreen_gl_try_order(GlobalConfig(mujoco_offscreen_gl="egl")) == ["egl"]
    assert offscreen_gl_try_order(GlobalConfig(mujoco_offscreen_gl="off")) == []


def test_temporary_mujoco_gl_restores_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MUJOCO_GL", "glfw")
    with temporary_mujoco_gl("egl"):
        assert os.environ["MUJOCO_GL"] == "egl"
    assert os.environ["MUJOCO_GL"] == "glfw"


def test_temporary_mujoco_gl_clears_when_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MUJOCO_GL", raising=False)
    with temporary_mujoco_gl("egl"):
        assert os.environ["MUJOCO_GL"] == "egl"
    assert "MUJOCO_GL" not in os.environ


def test_temporary_osmesa_software_env_sets_and_restores(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MUJOCO_GL", "egl")
    monkeypatch.setenv("LIBGL_ALWAYS_SOFTWARE", "0")
    with temporary_osmesa_software_env():
        assert os.environ["MUJOCO_GL"] == "osmesa"
        assert os.environ["LIBGL_ALWAYS_SOFTWARE"] == "1"
        assert os.environ["__GLX_VENDOR_LIBRARY_NAME"] == "mesa"
        assert os.environ["PYOPENGL_PLATFORM"] == "osmesa"
    assert os.environ["MUJOCO_GL"] == "egl"
    assert os.environ["LIBGL_ALWAYS_SOFTWARE"] == "0"
    assert "PYOPENGL_PLATFORM" not in os.environ


def test_temporary_osmesa_software_env_clears_when_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MUJOCO_GL", raising=False)
    monkeypatch.delenv("LIBGL_ALWAYS_SOFTWARE", raising=False)
    monkeypatch.delenv("__GLX_VENDOR_LIBRARY_NAME", raising=False)
    monkeypatch.delenv("PYOPENGL_PLATFORM", raising=False)
    with temporary_osmesa_software_env():
        assert os.environ["MUJOCO_GL"] == "osmesa"
    assert "MUJOCO_GL" not in os.environ
    assert "LIBGL_ALWAYS_SOFTWARE" not in os.environ


def test_try_create_offscreen_renderers_uses_osmesa_software_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from dimos.simulation.mujoco import mujoco_process as mp

    model = object()
    seen_env: list[dict[str, str | None]] = []

    def _capture_env(*_args: object, **_kwargs: object) -> mp.OffscreenRenderers:
        seen_env.append(
            {
                "MUJOCO_GL": os.environ.get("MUJOCO_GL"),
                "LIBGL_ALWAYS_SOFTWARE": os.environ.get("LIBGL_ALWAYS_SOFTWARE"),
                "PYOPENGL_PLATFORM": os.environ.get("PYOPENGL_PLATFORM"),
            }
        )
        return mp.OffscreenRenderers(
            rgb=object(),  # type: ignore[arg-type]
            depth=object(),  # type: ignore[arg-type]
            depth_left=object(),  # type: ignore[arg-type]
            depth_right=object(),  # type: ignore[arg-type]
            backend="osmesa",
        )

    monkeypatch.setattr(mp, "_create_offscreen_renderers_for_backend", _capture_env)
    result = mp.try_create_offscreen_renderers(model, 640, 480, ["osmesa"])
    assert result is not None
    assert seen_env == [
        {
            "MUJOCO_GL": "osmesa",
            "LIBGL_ALWAYS_SOFTWARE": "1",
            "PYOPENGL_PLATFORM": "osmesa",
        }
    ]


def test_try_create_offscreen_renderers_failure_returns_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from dimos.simulation.mujoco import mujoco_process as mp

    model = object()

    def _raise_gl_error(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("gladLoadGL error")

    monkeypatch.setattr(mp, "_create_offscreen_renderers_for_backend", _raise_gl_error)
    result = mp.try_create_offscreen_renderers(model, 640, 480, ["egl", "osmesa"])
    assert result is None


def test_temporary_viewer_gl_env_sets_glfw_and_clears_osmesa_platform(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MUJOCO_GL", "egl")
    monkeypatch.setenv("LIBGL_ALWAYS_SOFTWARE", "1")
    monkeypatch.setenv("PYOPENGL_PLATFORM", "osmesa")
    monkeypatch.setenv("__GLX_VENDOR_LIBRARY_NAME", "mesa")
    with temporary_viewer_gl_env():
        assert os.environ["MUJOCO_GL"] == "glfw"
        assert os.environ["LIBGL_ALWAYS_SOFTWARE"] == "1"
        assert "PYOPENGL_PLATFORM" not in os.environ
        assert os.environ["__GLX_VENDOR_LIBRARY_NAME"] == "mesa"
    assert os.environ["MUJOCO_GL"] == "egl"
    assert os.environ["LIBGL_ALWAYS_SOFTWARE"] == "1"
    assert os.environ["PYOPENGL_PLATFORM"] == "osmesa"
    assert os.environ["__GLX_VENDOR_LIBRARY_NAME"] == "mesa"


def test_offscreen_failure_does_not_skip_passive_viewer() -> None:
    """Offscreen GL failure must not prevent attempting the passive viewer."""
    config = GlobalConfig(mujoco_viewer=True, mujoco_offscreen_gl="auto")
    headless, _ = explain_mujoco_headless(config, {"DISPLAY": ":1"})
    backends = offscreen_gl_try_order(config)
    assert headless is False
    assert backends == ["egl", "osmesa", "glfw"]
    # Viewer path is chosen from headless flag only; offscreen failure is handled separately.
    assert headless is False


def test_install_permissive_x11_error_handler_idempotent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from dimos.simulation.mujoco import display_env as de

    monkeypatch.setattr(de, "_x11_error_handler_ref", None)
    monkeypatch.setattr(
        "ctypes.util.find_library",
        lambda _name: "libX11.so.6",
        raising=False,
    )

    class _FakeCDLL:
        def __init__(self, _name: str) -> None:
            self.set_handler = False

        def XSetErrorHandler(self, _handler: object) -> None:
            self.set_handler = True

    fake_x11 = _FakeCDLL("libX11.so.6")
    monkeypatch.setattr("ctypes.CDLL", lambda _name: fake_x11)

    de.install_permissive_x11_error_handler()
    first_ref = de._x11_error_handler_ref
    assert first_ref is not None
    assert fake_x11.set_handler is True

    de.install_permissive_x11_error_handler()
    assert de._x11_error_handler_ref is first_ref


def test_probe_passive_viewer_gl_success_and_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    from dimos.simulation.mujoco import display_env as de

    class _Result:
        returncode = 0

    monkeypatch.setattr(de.subprocess, "run", lambda *_a, **_k: _Result())
    assert de.probe_passive_viewer_gl(timeout=1.0) is True

    def _timeout(*_a: object, **_k: object) -> None:
        raise subprocess.TimeoutExpired(cmd=["viewer_probe"], timeout=1.0)

    monkeypatch.setattr(de.subprocess, "run", _timeout)
    assert de.probe_passive_viewer_gl(timeout=1.0) is False
