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

import json
from pathlib import Path
from typing import Literal

import click
from pydantic import BaseModel, ValidationError
import pytest

from dimos.core.coordination.blueprints import autoconnect
from dimos.core.module import Module, ModuleConfig
from dimos.robot.cli.dimos import _KeyValueType, arg_help, load_config_args


def test_blueprint_arg_help():
    class ConfigA(ModuleConfig):
        min_interval_sec: float = 0.1
        entity_prefix: str = "world"
        viewer_mode: Literal["native", "web", "connect", "none"] = "native"

    class TestModuleA(Module):
        config: ConfigA

    class ConfigB(ModuleConfig):
        memory_limit: str = "25%"
        ip: str = "127.0.0.1"

    class TestModuleB(Module):
        config: ConfigB

    blueprint = autoconnect(TestModuleA.blueprint(), TestModuleB.blueprint())
    output = arg_help(blueprint.config(), blueprint)
    # List output produces better diff in pytest error output.
    assert output.split("\n") == [
        "    testmodulea:",
        "      * testmodulea.default_rpc_timeout: float (default: 120.0)",
        "      * testmodulea.frame_id_prefix: str | None (default: None)",
        "      * testmodulea.frame_id: str | None (default: None)",
        "      * testmodulea.min_interval_sec: float (default: 0.1)",
        "      * testmodulea.entity_prefix: str (default: world)",
        "      * testmodulea.viewer_mode: typing.Literal['native', 'web', 'connect', 'none'] (default: native)",
        "    testmoduleb:",
        "      * testmoduleb.default_rpc_timeout: float (default: 120.0)",
        "      * testmoduleb.frame_id_prefix: str | None (default: None)",
        "      * testmoduleb.frame_id: str | None (default: None)",
        "      * testmoduleb.memory_limit: str (default: 25%)",
        "      * testmoduleb.ip: str (default: 127.0.0.1)",
        "",
    ]


def test_blueprint_arg_help_extra_args():
    """Test defaults passed to .blueprint() override."""

    class ConfigA(ModuleConfig):
        frame_id_prefix: str | None = None
        min_interval_sec: float = 0.1
        entity_prefix: str = "world"
        viewer_mode: Literal["native", "web", "connect", "none"] = "native"

    class TestModuleA(Module):
        config: ConfigA

    class ConfigB(ModuleConfig):
        memory_limit: str = "25%"
        ip: str = "127.0.0.1"

    class TestModuleB(Module):
        config: ConfigB

    module_a = TestModuleA.blueprint(frame_id_prefix="foo", viewer_mode="web")
    blueprint = autoconnect(module_a, TestModuleB.blueprint(ip="1.1.1.1"))
    output = arg_help(blueprint.config(), blueprint)
    # List output produces better diff in pytest error output.
    assert output.split("\n") == [
        "    testmodulea:",
        "      * testmodulea.default_rpc_timeout: float (default: 120.0)",
        "      * testmodulea.frame_id_prefix: str | None (default: foo)",
        "      * testmodulea.frame_id: str | None (default: None)",
        "      * testmodulea.min_interval_sec: float (default: 0.1)",
        "      * testmodulea.entity_prefix: str (default: world)",
        "      * testmodulea.viewer_mode: typing.Literal['native', 'web', 'connect', 'none'] (default: web)",
        "    testmoduleb:",
        "      * testmoduleb.default_rpc_timeout: float (default: 120.0)",
        "      * testmoduleb.frame_id_prefix: str | None (default: None)",
        "      * testmoduleb.frame_id: str | None (default: None)",
        "      * testmoduleb.memory_limit: str (default: 25%)",
        "      * testmoduleb.ip: str (default: 1.1.1.1)",
        "",
    ]


def test_blueprint_arg_help_required():
    """Test required arguments."""

    class Config(ModuleConfig):
        foo: int
        spam: str = "eggs"

    class TestModule(Module):
        config: Config

    blueprint = TestModule.blueprint()
    output = arg_help(blueprint.config(), blueprint)
    assert output.split("\n") == [
        "    testmodule:",
        "      * testmodule.default_rpc_timeout: float (default: 120.0)",
        "      * testmodule.frame_id_prefix: str | None (default: None)",
        "      * testmodule.frame_id: str | None (default: None)",
        "      * [Required] testmodule.foo: int",
        "      * testmodule.spam: str (default: eggs)",
        "",
    ]


def test_blueprint_arg_help_required_satisfied_by_blueprint_kwargs():
    """A required field provided in `.blueprint(...)` should NOT show as [Required]."""

    class Config(ModuleConfig):
        foo: int
        spam: str = "eggs"

    class TestModule(Module):
        config: Config

    blueprint = autoconnect(TestModule.blueprint(foo=7))
    output = arg_help(blueprint.config(), blueprint)
    assert output.split("\n") == [
        "    testmodule:",
        "      * testmodule.default_rpc_timeout: float (default: 120.0)",
        "      * testmodule.frame_id_prefix: str | None (default: None)",
        "      * testmodule.frame_id: str | None (default: None)",
        "      * testmodule.foo: int (default: 7)",
        "      * testmodule.spam: str (default: eggs)",
        "",
    ]


def test_blueprint_arg_help_skips_generic_alias():
    """`list[int]` / `dict[str, int]` produce types.GenericAlias and must be hidden."""

    class Config(ModuleConfig):
        # GenericAlias-typed fields can't be specified on the CLI
        tags: list[str] = []
        weights: dict[str, float] = {}
        # Plain scalars must still appear
        threshold: float = 0.5

    class TestModule(Module):
        config: Config

    blueprint = TestModule.blueprint()
    output = arg_help(blueprint.config(), blueprint)

    lines = output.split("\n")
    # No GenericAlias-typed fields should leak through
    assert not any("tags" in line for line in lines)
    assert not any("weights" in line for line in lines)
    # Plain scalar field should still be there
    assert any("testmodule.threshold: float (default: 0.5)" in line for line in lines)


def test_blueprint_arg_help_multi_module_indent_and_module_prefix():
    """Verify each module gets its own block with module-prefixed field names."""

    class ConfigA(ModuleConfig):
        a_value: int = 1

    class ModuleA(Module):
        config: ConfigA

    class ConfigB(ModuleConfig):
        b_value: int = 2

    class ModuleB(Module):
        config: ConfigB

    blueprint = autoconnect(ModuleA.blueprint(), ModuleB.blueprint())
    output = arg_help(blueprint.config(), blueprint)
    lines = output.split("\n")

    # Module headers are present
    assert "    modulea:" in lines
    assert "    moduleb:" in lines
    # Each scalar appears under its own module prefix only
    assert any("modulea.a_value" in line for line in lines)
    assert any("moduleb.b_value" in line for line in lines)
    assert not any("modulea.b_value" in line for line in lines)
    assert not any("moduleb.a_value" in line for line in lines)


def test_blueprint_arg_help_skips_g_field():
    """The `g: GlobalConfig` field is registered on every module config but
    must never appear in the generated help — recursing into it would explode
    the output and pollute every blueprint's help."""

    class Config(ModuleConfig):
        only_field: int = 42

    class TestModule(Module):
        config: Config

    blueprint = TestModule.blueprint()
    output = arg_help(blueprint.config(), blueprint)
    # The literal "g:" header would appear if the skip was broken
    assert "    g:" not in output.split("\n")
    # And it should not be listed as a scalar field on the module either
    assert "testmodule.g" not in output


# ---------------------------------------------------------------------------
# _KeyValueType — the click ParamType for `dimos mcp call --arg key=value`
# ---------------------------------------------------------------------------


def _convert(raw: str) -> tuple[str, object]:
    """Helper to invoke _KeyValueType.convert() with no Click context."""
    return _KeyValueType().convert(raw, param=None, ctx=None)


def test_key_value_type_string_fallback():
    """Non-JSON values should fall back to the raw string."""
    assert _convert("name=alice") == ("name", "alice")
    # Strings that look like words (not JSON) stay as strings
    assert _convert("status=running") == ("status", "running")


def test_key_value_type_json_scalars():
    """Numbers, booleans, null should be JSON-decoded."""
    assert _convert("count=3") == ("count", 3)
    assert _convert("ratio=0.25") == ("ratio", 0.25)
    assert _convert("enabled=true") == ("enabled", True)
    assert _convert("enabled=false") == ("enabled", False)
    assert _convert("missing=null") == ("missing", None)


def test_key_value_type_json_collections():
    """Lists and objects should be JSON-decoded."""
    assert _convert('tags=["a","b"]') == ("tags", ["a", "b"])
    assert _convert('opts={"k":1}') == ("opts", {"k": 1})


def test_key_value_type_value_with_equals_sign():
    """Only the FIRST '=' should split key from value — values can contain '='."""
    # base64-style strings, equations, query strings, etc. all contain '='
    key, value = _convert("query=a=b=c")
    assert key == "query"
    assert value == "a=b=c"


def test_key_value_type_empty_value_is_empty_string():
    key, value = _convert("name=")
    assert key == "name"
    assert value == ""


def test_key_value_type_missing_equals_raises():
    """Argument missing '=' separator must raise click.BadParameter."""
    with pytest.raises(click.BadParameter, match="expected KEY=VALUE"):
        _convert("not_a_kv_pair")


# ---------------------------------------------------------------------------
# load_config_args() — JSON file + DIMOS_* env vars + CLI `key.path=val` overrides
# ---------------------------------------------------------------------------


class _LoadConfigSubModel(BaseModel):
    dimoscli_enabled: bool = False
    dimoscli_threshold: float = 0.5


class _LoadConfigSchema(BaseModel):
    """Schema for load_config_args() tests.

    All fields use a `dimoscli_` prefix so that ``load_config_args`` (which
    iterates ``os.environ``) cannot pick up unrelated host or CI env vars
    that happen to share short common names like ``NAME`` or ``COUNT``.
    """

    dimoscli_name: str = "default"
    dimoscli_count: int = 0
    dimoscli_nested: _LoadConfigSubModel = _LoadConfigSubModel()


def test_load_config_args_missing_file_returns_empty(tmp_path: Path) -> None:
    missing = tmp_path / "does_not_exist.json"
    assert load_config_args(_LoadConfigSchema, [], missing) == {}


def test_load_config_args_invalid_json_treated_as_empty(tmp_path: Path) -> None:
    config_path = tmp_path / "config.json"
    config_path.write_text("{not valid json")
    # Falls back to empty kwargs (and then validates {} against schema, which is OK
    # because every field has a default)
    assert load_config_args(_LoadConfigSchema, [], config_path) == {}


def test_load_config_args_loads_from_json_file(tmp_path: Path) -> None:
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps({"dimoscli_name": "alice", "dimoscli_count": 7}))
    result = load_config_args(_LoadConfigSchema, [], config_path)
    assert result["dimoscli_name"] == "alice"
    assert result["dimoscli_count"] == 7


def test_load_config_args_cli_overrides_file(tmp_path: Path) -> None:
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps({"dimoscli_name": "from_file", "dimoscli_count": 1}))
    result = load_config_args(
        _LoadConfigSchema,
        ["dimoscli_name=from_cli", "dimoscli_count=42"],
        config_path,
    )
    assert result["dimoscli_name"] == "from_cli"
    assert result["dimoscli_count"] == "42"  # CLI args are unparsed strings


def test_load_config_args_cli_supports_nested_dotted_path(tmp_path: Path) -> None:
    config_path = tmp_path / "config.json"
    config_path.write_text("{}")
    result = load_config_args(
        _LoadConfigSchema,
        ["dimoscli_nested.dimoscli_enabled=true", "dimoscli_nested.dimoscli_threshold=0.9"],
        config_path,
    )
    assert result["dimoscli_nested"] == {
        "dimoscli_enabled": "true",
        "dimoscli_threshold": "0.9",
    }


def test_load_config_args_env_vars(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config_path = tmp_path / "config.json"
    config_path.write_text("{}")
    # Top-level field via env var
    monkeypatch.setenv("DIMOSCLI_NAME", "from_env")
    # Nested field via "__" separator
    monkeypatch.setenv("DIMOSCLI_NESTED__DIMOSCLI_ENABLED", "true")
    # Unrelated env var with no matching field prefix is ignored
    monkeypatch.setenv("UNRELATED__FIELD", "ignored")

    result = load_config_args(_LoadConfigSchema, [], config_path)
    assert result["dimoscli_name"] == "from_env"
    assert result["dimoscli_nested"] == {"dimoscli_enabled": "true"}
    assert "unrelated" not in result


def test_load_config_args_cli_overrides_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_path = tmp_path / "config.json"
    config_path.write_text("{}")
    monkeypatch.setenv("DIMOSCLI_NAME", "from_env")
    result = load_config_args(_LoadConfigSchema, ["dimoscli_name=from_cli"], config_path)
    assert result["dimoscli_name"] == "from_cli"


def test_load_config_args_validates_schema(tmp_path: Path) -> None:
    """Misspelled / wrongly-typed fields should raise a pydantic ValidationError
    so users catch typos before the daemon spawns workers."""
    config_path = tmp_path / "config.json"
    config_path.write_text("{}")
    # `dimoscli_count` expects int — passing an unparseable string trips validation
    with pytest.raises(ValidationError):
        load_config_args(_LoadConfigSchema, ["dimoscli_count=not_a_number"], config_path)
