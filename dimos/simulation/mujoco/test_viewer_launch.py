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

"""Minimal passive-viewer smoke test (requires DISPLAY and MuJoCo sim extra)."""

from __future__ import annotations

from contextlib import contextmanager, nullcontext
import time
from unittest.mock import MagicMock

import pytest

from dimos.simulation.mujoco.display_env import (
    has_graphical_display,
    temporary_viewer_gl_env,
)


@pytest.mark.mujoco
def test_launch_passive_opens_with_viewer_gl_env() -> None:
    """Open MuJoCo passive viewer for ~3s when a display is available."""
    if not has_graphical_display():
        pytest.skip("no DISPLAY or WAYLAND_DISPLAY")

    mujoco = pytest.importorskip("mujoco")
    from mujoco import viewer

    xml = """
    <mujoco>
      <worldbody>
        <geom type="plane" size="1 1 0.1"/>
        <body pos="0 0 0.5">
          <freejoint/>
          <geom type="box" size="0.1 0.1 0.1"/>
        </body>
      </worldbody>
    </mujoco>
    """
    model = mujoco.MjModel.from_xml_string(xml)
    data = mujoco.MjData(model)

    opened = False
    with temporary_viewer_gl_env():
        with viewer.launch_passive(
            model, data, show_left_ui=False, show_right_ui=False
        ) as m_viewer:
            opened = True
            deadline = time.time() + 3.0
            while time.time() < deadline and m_viewer.is_running():
                mujoco.mj_step(model, data)
                m_viewer.sync()
                time.sleep(0.01)

    assert opened


@pytest.mark.mujoco
def test_run_simulation_attempts_viewer_before_offscreen(monkeypatch: pytest.MonkeyPatch) -> None:
    """Viewer is launched before offscreen renderers when mujoco_viewer=True."""
    mujoco = pytest.importorskip("mujoco")
    from dimos.core.global_config import GlobalConfig
    from dimos.simulation.mujoco import mujoco_process as mp

    order: list[str] = []

    class _FakeViewer:
        def __init__(self) -> None:
            self.cam = MagicMock()

        def is_running(self) -> bool:
            return len(order) < 3

        def sync(self) -> None:
            pass

        def __enter__(self) -> _FakeViewer:
            order.append("viewer_open")
            return self

        def __exit__(self, *_args: object) -> None:
            order.append("viewer_close")

    model = mujoco.MjModel.from_xml_string(
        """
        <mujoco>
          <worldbody>
            <geom type="plane" size="1 1 0.1"/>
            <body pos="0 0 0.3">
              <freejoint/>
              <geom type="sphere" size="0.1"/>
            </body>
          </worldbody>
        </mujoco>
        """
    )
    data = mujoco.MjData(model)

    monkeypatch.setenv("DISPLAY", ":1")
    monkeypatch.setattr(mp, "load_scene_xml", lambda _config: "<mujoco/>")
    monkeypatch.setattr(
        mp,
        "load_model",
        lambda *_args, **_kwargs: (model, data),
    )

    @contextmanager
    def _fake_launch_passive(*_args: object, **_kwargs: object):
        order.append("viewer_open")
        yield _FakeViewer()
        order.append("viewer_close")

    monkeypatch.setattr(mujoco, "mj_name2id", lambda *_a, **_k: 0)
    monkeypatch.setattr(mp, "launch_passive_with_timeout", _fake_launch_passive)
    monkeypatch.setattr(
        mp,
        "try_create_offscreen_renderers",
        lambda *_a, **_k: order.append("offscreen") or None,
    )
    monkeypatch.setattr(mp, "should_enable_fake_obstacle_lidar", lambda *_a, **_k: False)
    monkeypatch.setattr(
        mp.PersonPositionController,
        "__init__",
        lambda self, _model: None,
    )
    monkeypatch.setattr(mp.PersonPositionController, "tick", lambda self, _data: None)
    monkeypatch.setattr(mp.PersonPositionController, "stop", lambda self: None)

    shm = MagicMock()
    shm.should_stop.side_effect = [False, False, True]
    config = GlobalConfig(mujoco_viewer=True, mujoco_offscreen_gl="auto")

    mp._run_simulation(config, shm)

    assert order.index("viewer_open") < order.index("offscreen")


def _make_minimal_model() -> object:
    mujoco = pytest.importorskip("mujoco")
    model = mujoco.MjModel.from_xml_string(
        "<mujoco><worldbody><geom type='plane' size='1 1 0.1'/></worldbody></mujoco>"
    )
    return model


def _make_minimal_data() -> object:
    mujoco = pytest.importorskip("mujoco")
    model = _make_minimal_model()
    return mujoco.MjData(model)  # type: ignore[arg-type]


def test_viewer_probe_main_exits_zero_on_success(monkeypatch: pytest.MonkeyPatch) -> None:
    from dimos.simulation.mujoco import viewer_probe as vp

    monkeypatch.setattr(vp, "run_viewer_probe", lambda: None)
    exit_calls: list[int] = []
    monkeypatch.setattr(vp.os, "_exit", lambda code: exit_calls.append(code))
    monkeypatch.setattr(vp.sys, "exit", lambda code: pytest.fail(f"sys.exit({code}) called"))

    vp.main()
    assert exit_calls == [0]


def test_viewer_probe_main_sys_exit_on_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    from dimos.simulation.mujoco import viewer_probe as vp

    def _fail() -> None:
        raise RuntimeError("GLX failed")

    monkeypatch.setattr(vp, "run_viewer_probe", _fail)
    exit_calls: list[int] = []
    monkeypatch.setattr(vp.os, "_exit", lambda code: pytest.fail(f"os._exit({code}) called"))

    def _sys_exit(code: int) -> None:
        exit_calls.append(code)
        raise SystemExit(code)

    monkeypatch.setattr(vp.sys, "exit", _sys_exit)

    with pytest.raises(SystemExit):
        vp.main()
    assert exit_calls == [1]


def test_run_viewer_probe_installs_permissive_handler(monkeypatch: pytest.MonkeyPatch) -> None:
    pytest.importorskip("mujoco")
    from dimos.simulation.mujoco import display_env as de, viewer_probe as vp

    calls: list[str] = []

    def _track_install() -> None:
        calls.append("install")

    monkeypatch.setattr(de, "install_permissive_x11_error_handler", _track_install)

    class _FakeHandle:
        def is_running(self) -> bool:
            return True

        def sync(self) -> None:
            pass

        def __enter__(self) -> _FakeHandle:
            return self

        def __exit__(self, *_args: object) -> None:
            pass

    monkeypatch.setattr(de, "temporary_viewer_gl_env", lambda: nullcontext())
    monkeypatch.setattr(
        "mujoco.viewer.launch_passive",
        lambda *_a, **_k: _FakeHandle(),
    )

    vp.run_viewer_probe()
    assert calls == ["install"]
