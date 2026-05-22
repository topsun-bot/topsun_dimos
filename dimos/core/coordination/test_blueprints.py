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


import pickle
from typing import Protocol, get_type_hints

from pydantic import ValidationError
import pytest

from dimos.core._test_future_annotations_helper import (
    FutureData,
    FutureModuleIn,
    FutureModuleOut,
)
from dimos.core.coordination.blueprints import (
    Blueprint,
    BlueprintAtom,
    DisabledModuleProxy,
    ModuleRef,
    StreamRef,
    autoconnect,
)
from dimos.core.core import rpc
from dimos.core.module import Module
from dimos.core.stream import In, Out
from dimos.core.transport import LCMTransport, PubSubTransport, pLCMTransport, pSHMTransport
from dimos.spec.utils import Spec


class Scratch:
    pass


class Petting:
    pass


class CatModule(Module):
    pet_cat: In[Petting]
    scratches: Out[Scratch]


class Data1:
    pass


class Data2:
    pass


class Data3:
    pass


class ModuleA(Module):
    data1: Out[Data1]
    data2: Out[Data2]

    @rpc
    def get_name(self) -> str:
        return "A, Module A"


class ModuleB(Module):
    data1: In[Data1]
    data2: In[Data2]
    data3: Out[Data3]

    module_a: ModuleA

    @rpc
    def what_is_as_name(self) -> str:
        return self.module_a.get_name()


def test_get_connection_set() -> None:
    assert BlueprintAtom.create(CatModule, kwargs={"k": "v"}) == BlueprintAtom(
        module=CatModule,
        streams=(
            StreamRef(name="pet_cat", type=Petting, direction="in"),
            StreamRef(name="scratches", type=Scratch, direction="out"),
        ),
        module_refs=(),
        kwargs={"k": "v"},
    )


def test_autoconnect() -> None:
    blueprint_set = autoconnect(ModuleA.blueprint(), ModuleB.blueprint())

    assert blueprint_set == Blueprint(
        blueprints=(
            BlueprintAtom(
                module=ModuleA,
                streams=(
                    StreamRef(name="data1", type=Data1, direction="out"),
                    StreamRef(name="data2", type=Data2, direction="out"),
                ),
                module_refs=(),
                kwargs={},
            ),
            BlueprintAtom(
                module=ModuleB,
                streams=(
                    StreamRef(name="data1", type=Data1, direction="in"),
                    StreamRef(name="data2", type=Data2, direction="in"),
                    StreamRef(name="data3", type=Data3, direction="out"),
                ),
                module_refs=(ModuleRef(name="module_a", spec=ModuleA),),
                kwargs={},
            ),
        )
    )


def test_config() -> None:
    blueprint = autoconnect(ModuleA.blueprint(), ModuleB.blueprint())
    config = blueprint.config()
    assert config.model_fields.keys() == {"modulea", "moduleb", "g"}
    assert config.model_fields["modulea"].annotation == get_type_hints(ModuleA)["config"] | None
    assert config.model_fields["moduleb"].annotation == get_type_hints(ModuleB)["config"] | None

    with pytest.raises(ValidationError, match="invalid_key"):
        config(module_a={"invalid_key": 5})


def test_transports() -> None:
    custom_transport = LCMTransport("/custom_topic", Data1)
    blueprint_set = autoconnect(ModuleA.blueprint(), ModuleB.blueprint()).transports(
        {("data1", Data1): custom_transport}
    )

    assert ("data1", Data1) in blueprint_set.transport_map
    assert blueprint_set.transport_map[("data1", Data1)] == custom_transport


def test_global_config() -> None:
    blueprint_set = autoconnect(ModuleA.blueprint(), ModuleB.blueprint()).global_config(
        option1=True, option2=42
    )

    assert "option1" in blueprint_set.global_config_overrides
    assert blueprint_set.global_config_overrides["option1"] is True
    assert "option2" in blueprint_set.global_config_overrides
    assert blueprint_set.global_config_overrides["option2"] == 42


def test_future_annotations_support() -> None:
    """Test that modules using `from __future__ import annotations` work correctly.

    PEP 563 (future annotations) stores annotations as strings instead of actual types.
    This test verifies that BlueprintAtom.create properly resolves string annotations
    to the actual In/Out types.
    """

    # Test that streams are properly extracted from modules with future annotations
    out_blueprint = BlueprintAtom.create(FutureModuleOut, kwargs={})
    assert len(out_blueprint.streams) == 1
    assert out_blueprint.streams[0] == StreamRef(name="data", type=FutureData, direction="out")

    in_blueprint = BlueprintAtom.create(FutureModuleIn, kwargs={})
    assert len(in_blueprint.streams) == 1
    assert in_blueprint.streams[0] == StreamRef(name="data", type=FutureData, direction="in")


def test_autoconnect_merges_disabled_modules() -> None:
    bp_a = Blueprint(
        blueprints=ModuleA.blueprint().blueprints,
        disabled_modules_tuple=(ModuleA,),
    )
    bp_b = Blueprint(
        blueprints=ModuleB.blueprint().blueprints,
        disabled_modules_tuple=(ModuleB,),
    )

    merged = autoconnect(bp_a, bp_b)
    assert merged.disabled_modules_tuple == (ModuleA, ModuleB)


class CalcSpec(Spec, Protocol):
    @rpc
    def compute(self, a: int, b: int) -> int: ...


class ModuleWithOptionalRef(Module):
    data1: In[Data1]
    calc: CalcSpec | None = None


def test_optional_module_ref_detected() -> None:
    atom = BlueprintAtom.create(ModuleWithOptionalRef, kwargs={})
    assert len(atom.module_refs) == 1
    ref = atom.module_refs[0]
    assert ref.name == "calc"
    assert ref.optional is True


def test_autoconnect_eliminates_duplicates_keeps_newer() -> None:
    bp1 = Blueprint.create(ModuleA, key1="old")
    bp2 = Blueprint.create(ModuleA, key1="new")

    merged = autoconnect(bp1, bp2)

    module_a_atoms = [a for a in merged.blueprints if a.module is ModuleA]
    assert len(module_a_atoms) == 1
    assert module_a_atoms[0].kwargs == {"key1": "new"}


def test_disabled_module_proxy_pickle_roundtrip() -> None:
    proxy = DisabledModuleProxy("SomeSpec")
    restored = pickle.loads(pickle.dumps(proxy))

    assert repr(restored) == "<DisabledModuleProxy spec=SomeSpec>"
    assert restored.any_method(1, 2, 3) is None


def test_active_blueprints_filters_disabled() -> None:
    blueprint = autoconnect(ModuleA.blueprint(), ModuleB.blueprint()).disabled_modules(ModuleA)

    active_modules = {bp.module for bp in blueprint.active_blueprints}
    assert ModuleA not in active_modules
    assert ModuleB in active_modules


def test_transport_factory_sets_field() -> None:
    def my_factory(topic: str, stream_type: type) -> PubSubTransport:  # type: ignore[type-arg]
        return pSHMTransport(topic)

    bp = autoconnect(ModuleA.blueprint(), ModuleB.blueprint()).transport_factory(my_factory)
    assert bp._transport_factory is my_factory


def test_transport_factory_merged_via_autoconnect() -> None:
    """autoconnect takes the last non-None factory."""

    def factory_a(topic: str, stream_type: type) -> PubSubTransport:  # type: ignore[type-arg]
        return pSHMTransport(topic)

    def factory_b(topic: str, stream_type: type) -> PubSubTransport:  # type: ignore[type-arg]
        return pLCMTransport(topic)

    bp_with_factory = ModuleA.blueprint().transport_factory(factory_a)
    bp_plain = ModuleB.blueprint()
    bp_with_factory_b = Blueprint.create(ModuleA).transport_factory(factory_b)

    merged = autoconnect(bp_with_factory, bp_plain)
    assert merged._transport_factory is factory_a

    merged2 = autoconnect(bp_with_factory, bp_with_factory_b)
    assert merged2._transport_factory is factory_b


def test_transport_factory_priority_over_global_config() -> None:
    """Blueprint-level factory takes precedence over GlobalConfig default_transport."""
    from unittest.mock import patch

    from dimos.core.coordination.module_coordinator import _get_transport_for

    def custom_factory(topic: str, stream_type: type) -> PubSubTransport:  # type: ignore[type-arg]
        return pSHMTransport(topic)

    bp = autoconnect(ModuleA.blueprint(), ModuleB.blueprint()).transport_factory(custom_factory)

    with patch("dimos.core.coordination.module_coordinator.global_config") as mock_gc:
        mock_gc.default_transport = "lcm"
        transport = _get_transport_for(bp, "data1", Data1)

    assert isinstance(transport, pSHMTransport)


def test_explicit_transport_highest_priority() -> None:
    """Explicit .transports() overrides both factory and global config."""
    from dimos.core.coordination.module_coordinator import _get_transport_for

    explicit_transport = LCMTransport("/explicit", Data1)

    def custom_factory(topic: str, stream_type: type) -> PubSubTransport:  # type: ignore[type-arg]
        return pSHMTransport(topic)

    bp = (
        autoconnect(ModuleA.blueprint(), ModuleB.blueprint())
        .transports({("data1", Data1): explicit_transport})
        .transport_factory(custom_factory)
    )

    transport = _get_transport_for(bp, "data1", Data1)
    assert transport is explicit_transport


def test_global_config_default_transport_shm() -> None:
    """GlobalConfig default_transport=shm produces pSHMTransport."""
    from unittest.mock import patch

    from dimos.core.coordination.module_coordinator import _get_transport_for

    bp = autoconnect(ModuleA.blueprint(), ModuleB.blueprint())

    with patch("dimos.core.coordination.module_coordinator.global_config") as mock_gc:
        mock_gc.default_transport = "shm"
        transport = _get_transport_for(bp, "data1", Data1)

    assert isinstance(transport, pSHMTransport)


def test_global_config_default_transport_lcm() -> None:
    """GlobalConfig default_transport=lcm produces LCM-based transport."""
    from unittest.mock import patch

    from dimos.core.coordination.module_coordinator import _get_transport_for

    bp = autoconnect(ModuleA.blueprint(), ModuleB.blueprint())

    with patch("dimos.core.coordination.module_coordinator.global_config") as mock_gc:
        mock_gc.default_transport = "lcm"
        transport = _get_transport_for(bp, "data1", Data1)

    assert isinstance(transport, pLCMTransport)


class HumanInput(Module):
    human_input: In[Data1]


class AgentOut(Module):
    agent: Out[Data1]
    agent_idle: Out[Data2]


def test_shm_factory_pins_lcm_for_external_streams() -> None:
    """Streams with hardcoded external LCM producers stay on LCM even with shm mode."""
    from unittest.mock import patch

    from dimos.core.coordination.module_coordinator import _get_transport_for

    bp = autoconnect(HumanInput.blueprint(), AgentOut.blueprint())

    with patch("dimos.core.coordination.module_coordinator.global_config") as mock_gc:
        mock_gc.default_transport = "shm"
        t_human = _get_transport_for(bp, "human_input", Data1)
        t_agent = _get_transport_for(bp, "agent", Data1)
        t_agent_idle = _get_transport_for(bp, "agent_idle", Data2)

    assert isinstance(t_human, pLCMTransport)
    assert isinstance(t_agent, pLCMTransport)
    assert isinstance(t_agent_idle, pLCMTransport)


def test_shm_factory_uses_large_capacity_for_image() -> None:
    """SHM factory allocates proper capacity for Image streams."""
    from dimos.constants import DEFAULT_CAPACITY_COLOR_IMAGE
    from dimos.core.coordination.module_coordinator import _shm_factory
    from dimos.msgs.sensor_msgs.Image import Image

    transport = _shm_factory("/color_image", Image)

    assert isinstance(transport, pSHMTransport)
    assert transport.shm.config.default_capacity == DEFAULT_CAPACITY_COLOR_IMAGE
