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

from unittest.mock import MagicMock

import pytest

from dimos.agents.skills.holoagent import (
    HoloAgentNavSkillContainer,
    HoloAgentSkillContainer,
)
from dimos.agents.skills.holoagent_client import HoloAgentBridgeError


class _BareHoloAgentSkills(HoloAgentSkillContainer):
    """Skill container without Module/LCM setup, for unit tests."""

    def __init__(self, client: MagicMock) -> None:
        self._client = client


def _container() -> tuple[_BareHoloAgentSkills, MagicMock]:
    client = MagicMock()
    return _BareHoloAgentSkills(client), client


def test_skills_are_annotated() -> None:
    nav_names = (
        "holoagent_health",
        "holoagent_semantic_nav",
        "holoagent_relative_move",
        "holoagent_stop_nav",
        "holoagent_navigation_signal",
    )
    for name in nav_names:
        method = getattr(HoloAgentNavSkillContainer, name)
        assert getattr(method, "__skill__", False), name
        assert method.__doc__, name
    assert getattr(HoloAgentSkillContainer.holoagent_arm, "__skill__", False)
    assert "holoagent_arm" not in HoloAgentNavSkillContainer.__dict__


def test_semantic_nav_success() -> None:
    skills, client = _container()
    client.semantic_nav.return_value = {"success": True}

    result = skills.holoagent_semantic_nav("coffee machine", floor="1F", room="pantry")

    client.semantic_nav.assert_called_once_with("1F", "pantry", "coffee machine")
    assert "semantic_nav(1F,pantry,coffee machine)" in result
    assert "ok" in result


def test_relative_move_rejects_zero() -> None:
    skills, client = _container()
    result = skills.holoagent_relative_move(0.0, 0.0, 0.0)
    client.relative_nav.assert_not_called()
    assert "refused" in result


@pytest.mark.parametrize(
    "kwargs",
    [
        {"forward": float("nan")},
        {"forward": float("inf")},
        {"left": float("-inf")},
        {"forward": 3.1},
        {"left": -3.1},
        {"rotation": 181.0},
    ],
)
def test_relative_move_rejects_non_finite_or_oversized(kwargs: dict[str, float]) -> None:
    skills, client = _container()
    result = skills.holoagent_relative_move(**kwargs)
    client.relative_nav.assert_not_called()
    assert "refused" in result


def test_semantic_nav_rejects_blank_object() -> None:
    skills, client = _container()
    result = skills.holoagent_semantic_nav("   ")
    client.semantic_nav.assert_not_called()
    assert "refused" in result


def test_relative_move_success() -> None:
    skills, client = _container()
    client.relative_nav.return_value = {"success": True}
    result = skills.holoagent_relative_move(0.5, 0.0, 15.0)
    client.relative_nav.assert_called_once_with(0.5, 0.0, 15.0)
    assert "ok" in result


def test_navigation_signal_success() -> None:
    skills, client = _container()
    client.navigation_signal.return_value = {"success": True}
    result = skills.holoagent_navigation_signal("one_point_1")
    client.navigation_signal.assert_called_once_with("one_point_1")
    assert "ok" in result


def test_bridge_errors_are_returned_as_strings() -> None:
    skills, client = _container()
    client.health.side_effect = HoloAgentBridgeError("GET http://127.0.0.1:8000/health failed")
    result = skills.holoagent_health()
    assert result.startswith("HoloAgent robot_bridge not reachable")


def test_arm_and_stop() -> None:
    skills, client = _container()
    client.arm_skill.return_value = {"success": True}
    client.stop_navigation.return_value = {"success": True}
    assert "ok" in skills.holoagent_arm("wave_above_head")
    assert "ok" in skills.holoagent_stop_nav()
    client.arm_skill.assert_called_once_with("wave_above_head")
    client.stop_navigation.assert_called_once()


def test_bridge_uses_module_config_url() -> None:
    from dimos.agents.skills.holoagent_client import HoloAgentBridgeClient
    from dimos.core.global_config import GlobalConfig

    skills = _BareHoloAgentSkills(None)
    skills._client = None
    skills.config = MagicMock()
    skills.config.g = GlobalConfig(holoagent_url="http://10.1.2.3:8000")

    client = skills._bridge()

    assert isinstance(client, HoloAgentBridgeClient)
    assert client.base_url == "http://10.1.2.3:8000"


def test_holoagent_url_default() -> None:
    from dimos.core.global_config import GlobalConfig

    assert GlobalConfig().holoagent_url == "http://127.0.0.1:8000"


@pytest.mark.parametrize(
    ("env_name", "value"),
    [
        ("DIMOS_HOLOAGENT_URL", "http://10.0.0.8:8000"),
        ("HOLOAGENT_URL", "http://10.0.0.9:8000"),
    ],
)
def test_holoagent_url_env_aliases(
    monkeypatch: pytest.MonkeyPatch, env_name: str, value: str
) -> None:
    from dimos.core.global_config import GlobalConfig

    monkeypatch.setenv(env_name, value)
    assert GlobalConfig().holoagent_url == value
