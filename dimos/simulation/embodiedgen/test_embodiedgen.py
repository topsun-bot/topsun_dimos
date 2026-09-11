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

"""Unit tests for the EmbodiedGen bridge. Does not import or run EmbodiedGen."""

from __future__ import annotations

from pathlib import Path
import sys

import pytest
from typer.testing import CliRunner

from dimos.core.global_config import GlobalConfig
from dimos.cli.embodiedgen import app as embodiedgen_cli
from dimos.simulation.embodiedgen.compose import (
    MINIMAL_SCENE_XML,
    apply_embodiedgen_scene,
    attach_asset,
    compose_standalone_scene,
    genesis_entities,
)
from dimos.simulation.embodiedgen.discovery import (
    AssetPlacement,
    discover_assets,
    find_asset,
    parse_placements,
    register_asset,
)
from dimos.simulation.embodiedgen.runner import cli_status
from dimos.simulation.embodiedgen.skill_api import (
    list_assets_text,
    load_asset_text,
    status_text,
)

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "eg_box"


@pytest.fixture
def isolated_catalog(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    catalog = tmp_path / "catalog"
    catalog.mkdir()
    monkeypatch.setenv("EMBODIEDGEN_CATALOG_DIR", str(catalog))
    monkeypatch.delenv("EMBODIEDGEN_EXPORT_DIR", raising=False)
    monkeypatch.delenv("DIMOS_EMBODIEDGEN_EXPORT_DIR", raising=False)
    monkeypatch.delenv("EMBODIEDGEN_ROOT", raising=False)
    monkeypatch.delenv("DIMOS_EMBODIEDGEN_ROOT", raising=False)
    return catalog


def test_bridge_does_not_import_embodied_gen() -> None:
    imported = [
        name for name in sys.modules if name == "embodied_gen" or name.startswith("embodied_gen.")
    ]
    assert imported == []
    import dimos.simulation.embodiedgen as bridge

    assert bridge.discover_assets is discover_assets
    imported = [
        name for name in sys.modules if name == "embodied_gen" or name.startswith("embodied_gen.")
    ]
    assert imported == []


def test_discover_fixture_layout() -> None:
    assets = discover_assets(roots=[FIXTURE_DIR], include_catalog=False)
    assert len(assets) == 1
    asset = assets[0]
    assert asset.name == "eg_box"
    assert asset.urdf is not None and asset.urdf.name == "eg_box.urdf"
    assert asset.mjcf is not None and asset.mjcf.name == "eg_box.xml"


def test_parse_placements() -> None:
    assert parse_placements("") == []
    one = parse_placements("mug")
    assert one == [AssetPlacement(name="mug", pos=(0.5, 0.0, 0.3))]
    posed = parse_placements("mug@1,0,0.2;box")
    assert posed[0] == AssetPlacement(name="mug", pos=(1.0, 0.0, 0.2))
    assert posed[1].name == "box"


def test_register_and_find(isolated_catalog: Path) -> None:
    registered = register_asset(FIXTURE_DIR, dest_root=isolated_catalog)
    assert registered.name == "eg_box"
    assert registered.source == "catalog"
    assert isolated_catalog.joinpath("eg_box").is_dir()
    found = find_asset("eg_box", export_dir=isolated_catalog)
    assert found.mjcf is not None
    assert found.urdf is not None


def test_register_rejects_empty_dir(tmp_path: Path) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(ValueError, match="No EmbodiedGen"):
        register_asset(empty, dest_root=tmp_path / "catalog")


def test_compose_mjcf_into_scene() -> None:
    asset = discover_assets(roots=[FIXTURE_DIR], include_catalog=False)[0]
    composed = attach_asset(MINIMAL_SCENE_XML, asset, pos=(1.0, 2.0, 0.3))
    assert "embodiedgen_eg_box" in composed.xml
    assert 'pos="1 2 0.3"' in composed.xml
    assert "eg_box.xml" in composed.vfs
    assert "eg_box_geom" in composed.xml or "eg_eg_box_eg_box_geom" in composed.xml


def test_compose_urdf_fallback(tmp_path: Path) -> None:
    urdf_only = tmp_path / "urdf_only"
    result = urdf_only / "result"
    result.mkdir(parents=True)
    (result / "crate.urdf").write_text((FIXTURE_DIR / "result" / "eg_box.urdf").read_text())
    asset = discover_assets(roots=[urdf_only], include_catalog=False)[0]
    assert asset.mjcf is None
    composed = attach_asset(MINIMAL_SCENE_XML, asset)
    assert "embodiedgen_urdf_only" in composed.xml or "embodiedgen_crate" in composed.xml
    assert 'type="box"' in composed.xml
    assert "crate.urdf" in composed.vfs


def test_genesis_entities() -> None:
    entities = genesis_entities(
        [AssetPlacement(name="eg_box", pos=(0.1, 0.2, 0.3))],
        export_dir=FIXTURE_DIR,
    )
    assert len(entities) == 1
    assert entities[0]["type"] == "mjcf"
    assert str(entities[0]["path"]).endswith("eg_box.xml")
    params = entities[0]["params"]
    assert isinstance(params, dict)
    assert params["pos"] == [0.1, 0.2, 0.3]


def test_apply_scene_noop_without_config() -> None:
    cfg = GlobalConfig()
    cfg.update(mujoco_embodiedgen_assets=None)
    assert apply_embodiedgen_scene("<mujoco/>", cfg) == "<mujoco/>"


def test_apply_scene_attaches_fixture(isolated_catalog: Path) -> None:
    register_asset(FIXTURE_DIR, dest_root=isolated_catalog)
    cfg = GlobalConfig()
    cfg.update(
        embodiedgen_catalog_dir=str(isolated_catalog),
        mujoco_embodiedgen_assets="eg_box@0.4,0,0.2",
    )
    xml = apply_embodiedgen_scene(MINIMAL_SCENE_XML, cfg)
    assert "embodiedgen_eg_box" in xml
    assert "0.4 0 0.2" in xml


def test_load_scene_xml_hook(monkeypatch: pytest.MonkeyPatch, isolated_catalog: Path) -> None:
    mujoco_model = pytest.importorskip("dimos.simulation.mujoco.model")

    register_asset(FIXTURE_DIR, dest_root=isolated_catalog)
    monkeypatch.setattr(mujoco_model, "_load_base_scene_xml", lambda _cfg: MINIMAL_SCENE_XML)
    cfg = GlobalConfig()
    cfg.update(
        embodiedgen_catalog_dir=str(isolated_catalog),
        mujoco_embodiedgen_assets="eg_box",
    )
    scene = mujoco_model.load_scene_xml(cfg)
    assert "embodiedgen_eg_box" in scene


def test_cli_status_does_not_require_install() -> None:
    status = cli_status("img3d-cli")
    assert status.name == "img3d-cli"
    if not status.available:
        assert "optional" in status.detail.lower() or "EMBODIEDGEN" in status.detail


def test_cli_list_and_scene(isolated_catalog: Path, tmp_path: Path) -> None:
    runner = CliRunner()
    listed = runner.invoke(embodiedgen_cli, ["list", "--export-dir", str(FIXTURE_DIR)])
    assert listed.exit_code == 0
    assert "eg_box" in listed.stdout

    out = tmp_path / "scene.xml"
    scene = runner.invoke(
        embodiedgen_cli,
        ["scene", "eg_box@1,0,0.25", "--export-dir", str(FIXTURE_DIR), "-o", str(out)],
    )
    assert scene.exit_code == 0, scene.stdout + scene.stderr
    text = out.read_text()
    assert "embodiedgen_eg_box" in text

    genesis = runner.invoke(
        embodiedgen_cli,
        ["scene", "eg_box", "--engine", "genesis", "--export-dir", str(FIXTURE_DIR)],
    )
    assert genesis.exit_code == 0
    assert "mjcf" in genesis.stdout


def test_cli_register(isolated_catalog: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(embodiedgen_cli, ["register", str(FIXTURE_DIR), "--name", "demo_box"])
    assert result.exit_code == 0, result.stdout + result.stderr
    assert "demo_box" in result.stdout
    assert (isolated_catalog / "demo_box").is_dir()


def test_cli_run_missing_binary() -> None:
    runner = CliRunner()
    result = runner.invoke(embodiedgen_cli, ["run-cli", "--cli", "definitely-not-installed-eg"])
    assert result.exit_code == 2
    assert "optional" in result.stderr.lower() or "EMBODIEDGEN" in result.stderr


def test_skill_api_status(isolated_catalog: Path) -> None:
    text = status_text()
    assert "catalog_dir" in text
    assert "embodiedgen_root" in text


def test_skill_api_list_and_load(isolated_catalog: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    register_asset(FIXTURE_DIR, name="eg_box", dest_root=isolated_catalog)
    monkeypatch.setenv("EMBODIEDGEN_CATALOG_DIR", str(isolated_catalog))
    listed = list_assets_text()
    assert "eg_box" in listed
    loaded = load_asset_text("eg_box", 1.0, 0.0, 0.2)
    assert "mujoco-embodiedgen-assets=eg_box@1,0,0.2" in loaded
    assert "Genesis entity" in loaded


def test_skill_annotations() -> None:
    skill_mod = pytest.importorskip("dimos.agents.skills.embodiedgen")
    skill_cls = skill_mod.EmbodiedGenSkill
    assert getattr(skill_cls.list_embodiedgen_assets, "__skill__", False)
    assert getattr(skill_cls.register_embodiedgen_asset, "__skill__", False)
    assert getattr(skill_cls.load_embodiedgen_asset, "__skill__", False)
    assert getattr(skill_cls.embodiedgen_status, "__skill__", False)


def test_standalone_compose_roundtrip() -> None:
    composed = compose_standalone_scene(
        [AssetPlacement(name="eg_box")],
        export_dir=FIXTURE_DIR,
    )
    assert "<mujoco" in composed.xml
    assert "floor" in composed.xml
    assert composed.vfs


def test_global_config_defaults() -> None:
    cfg = GlobalConfig(
        embodiedgen_root=None,
        embodiedgen_export_dir=None,
        embodiedgen_catalog_dir=None,
        mujoco_embodiedgen_assets=None,
    )
    assert cfg.embodiedgen_root is None
    assert cfg.mujoco_embodiedgen_assets is None


def test_find_missing_asset(isolated_catalog: Path) -> None:
    with pytest.raises(FileNotFoundError, match="not found"):
        find_asset("no_such_asset")
