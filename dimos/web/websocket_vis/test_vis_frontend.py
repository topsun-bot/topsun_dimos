# Copyright 2026 Dimensional Inc.
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

from pathlib import Path

import pytest

from dimos.web.websocket_vis.vis_frontend import VisFrontend


def test_missing_command_center_is_200_cockpit_pointer(monkeypatch: pytest.MonkeyPatch) -> None:
    class _Missing:
        def exists(self) -> bool:
            return False

    monkeypatch.setattr(
        "dimos.web.websocket_vis.vis_frontend.get_data",
        lambda _name: _Missing(),
    )
    status, html, path = VisFrontend.command_center_page()
    assert status == 200
    assert path is None
    assert html is not None and "Cockpit" in html
    assert "503" not in html


def test_legacy_command_center_file_is_served(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    page = tmp_path / "command_center.html"
    page.write_text("<html>legacy</html>")
    monkeypatch.setattr(
        "dimos.web.websocket_vis.vis_frontend.get_data",
        lambda _name: page,
    )
    status, html, path = VisFrontend.command_center_page()
    assert status == 200
    assert html is None
    assert path == page
