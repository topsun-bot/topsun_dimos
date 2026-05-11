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

"""Unit tests for ``dimos.robot.cli.topic`` helpers.

These tests focus on the pure lookup logic in ``_resolve_type`` so we don't
need a running LCM bus, ROS, or any real message wire-format.
"""

from __future__ import annotations

import importlib

import pytest

from dimos.robot.cli import topic as topic_mod
from dimos.robot.cli.topic import _resolve_type


def test_resolve_type_unknown_raises():
    """Looking up a name that exists in no message module must raise ValueError."""
    with pytest.raises(ValueError, match="Could not find type 'NoSuchMessage'"):
        _resolve_type("NoSuchMessage")


def test_resolve_type_finds_attribute_in_known_module(monkeypatch: pytest.MonkeyPatch) -> None:
    """When the type is exposed as an attribute on one of the known packages,
    ``_resolve_type`` should return it.

    We attach a sentinel class to ``dimos.msgs.std_msgs`` for the duration of
    the test (using monkeypatch so it is removed automatically).
    """
    pkg = importlib.import_module("dimos.msgs.std_msgs")

    class _SentinelMsg:
        pass

    monkeypatch.setattr(pkg, "_SentinelMsg", _SentinelMsg, raising=False)

    assert _resolve_type("_SentinelMsg") is _SentinelMsg


def test_resolve_type_search_order_first_match_wins(monkeypatch: pytest.MonkeyPatch) -> None:
    """If two modules both expose a type with the same name, the module
    earlier in ``_modules_to_try`` wins. This pins the *contract* (first
    match wins) without hard-coding the actual module order, so a refactor
    that reorders the list doesn't break the test for unrelated reasons."""

    # Build a controlled 2-element search list: a "first" and a "second" module.
    first = importlib.import_module("dimos.msgs.std_msgs")
    second = importlib.import_module("dimos.msgs.sensor_msgs")
    monkeypatch.setattr(
        topic_mod,
        "_modules_to_try",
        [first.__name__, second.__name__],
    )

    class _FirstHit:
        pass

    class _SecondHit:
        pass

    monkeypatch.setattr(first, "_DupeMsg", _FirstHit, raising=False)
    monkeypatch.setattr(second, "_DupeMsg", _SecondHit, raising=False)

    # The first module in the search list must win.
    assert _resolve_type("_DupeMsg") is _FirstHit


@pytest.mark.xfail(
    reason=(
        "Known bug: dimos.msgs.* are namespace packages (no __init__.py), so "
        "after `import dimos.msgs.geometry_msgs.PoseStamped`, the parent "
        "package's PoseStamped attribute is the SUBMODULE, not the class. "
        "_resolve_type returns the module, which then breaks downstream "
        "callers that expect a class with `lcm_encode` etc. Without the "
        "submodule import, the lookup raises ValueError instead. Tracking "
        "fix separately; this test pins the desired post-fix behavior."
    ),
    strict=True,
)
def test_resolve_type_real_message_class():
    """`_resolve_type('PoseStamped')` should return the PoseStamped *class*."""
    # Pre-import the submodule the same way the CLI's import chain would.
    importlib.import_module("dimos.msgs.geometry_msgs.PoseStamped")

    result = _resolve_type("PoseStamped")
    # Desired contract: result is the class, not the submodule.
    assert isinstance(result, type), f"expected a class, got {type(result).__name__}: {result!r}"
    assert result.__name__ == "PoseStamped"


def test_resolve_type_skips_unimportable_modules(monkeypatch: pytest.MonkeyPatch) -> None:
    """Modules that fail to import should be skipped silently rather than
    aborting the whole lookup. We simulate this by injecting a bogus module
    name at the head of the search list and proving the real lookup still
    succeeds."""

    real_modules = list(topic_mod._modules_to_try)
    monkeypatch.setattr(
        topic_mod,
        "_modules_to_try",
        ["dimos.msgs.this_module_does_not_exist", *real_modules],
    )

    # Now plant a sentinel in a real module further down the list.
    pkg = importlib.import_module("dimos.msgs.std_msgs")

    class _SentinelMsg:
        pass

    monkeypatch.setattr(pkg, "_SentinelMsg2", _SentinelMsg, raising=False)

    # The bogus module raises ImportError, the loop continues, the real one wins.
    assert _resolve_type("_SentinelMsg2") is _SentinelMsg
