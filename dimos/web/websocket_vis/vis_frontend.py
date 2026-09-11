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

"""HTTP pages for the legacy vis port (7779)."""

from pathlib import Path

from dimos.utils.data import get_data


class VisFrontend:
    """Command Center was replaced by Cockpit.

    Serving a deleted ``command_center.html`` as 503 left the default
    ``vis_module()`` path looking broken; serve the legacy file when present,
    otherwise a 200 page that points at ``cockpit()``.
    """

    REPLACEMENT_HTML = """<!doctype html>
<html lang="en">
<head><meta charset="utf-8"><title>DimOS Cockpit</title></head>
<body>
<h1>Command Center has been replaced by Cockpit</h1>
<p>Compose <code>cockpit()</code> onto the blueprint (or run a
<code>*-cockpit</code> blueprint). The relay serves the UI at
<code>http://127.0.0.1:&lt;relay-port&gt;/</code>.</p>
</body>
</html>
"""

    @staticmethod
    def command_center_path() -> Path | None:
        try:
            index_file = get_data("command_center.html")
        except Exception:
            return None
        return index_file if index_file.exists() else None

    @staticmethod
    def command_center_page() -> tuple[int, str | None, Path | None]:
        """Return ``(status, html, file_path)``. Exactly one of html/file_path is set."""
        path = VisFrontend.command_center_path()
        if path is not None:
            return 200, None, path
        return 200, VisFrontend.REPLACEMENT_HTML, None
