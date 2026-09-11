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

"""find_deno / ensure_deno: locate only, never download under pytest."""

from pathlib import Path

import pytest

from dimos.utils import deno as deno_mod
from dimos.utils.deno import ensure_deno, find_deno


def test_find_deno_returns_path_when_which_hits(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(deno_mod.shutil, "which", lambda _name: "/usr/bin/deno")
    assert find_deno() == "/usr/bin/deno"


def test_find_deno_returns_none_when_missing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(deno_mod.shutil, "which", lambda _name: None)
    monkeypatch.setattr(deno_mod, "_DENO_CACHE_DIR", tmp_path)
    assert find_deno() is None


def test_ensure_deno_skips_under_pytest_when_missing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(deno_mod.shutil, "which", lambda _name: None)
    monkeypatch.setattr(deno_mod, "_DENO_CACHE_DIR", tmp_path)
    monkeypatch.setenv("PYTEST_CURRENT_TEST", "dimos/utils/test_deno.py::test")
    with pytest.raises(pytest.skip.Exception, match="deno is not available"):
        ensure_deno()
