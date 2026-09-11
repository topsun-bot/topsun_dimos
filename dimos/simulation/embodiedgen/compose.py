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

"""Compose EmbodiedGen exports into DimOS MuJoCo scenes and Genesis entity dicts."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import xml.etree.ElementTree as ET

from dimos.core.global_config import GlobalConfig, global_config
from dimos.simulation.embodiedgen.discovery import (
    AssetPlacement,
    EmbodiedGenAsset,
    find_asset,
    parse_placements,
)

MINIMAL_SCENE_XML = """<mujoco model="embodiedgen_scene">
  <option gravity="0 0 -9.81"/>
  <asset/>
  <worldbody>
    <geom name="floor" type="plane" size="5 5 0.1" rgba="0.8 0.8 0.8 1"/>
  </worldbody>
</mujoco>
"""


@dataclass(frozen=True)
class ComposedScene:
    """Scene XML plus a MuJoCo virtual-filesystem dict (filename → bytes)."""

    xml: str
    vfs: dict[str, bytes]


def _prefix_attr(elem: ET.Element, attr: str, prefix: str) -> None:
    value = elem.get(attr)
    if value:
        elem.set(attr, prefix + value)


def _rewrite_refs(elem: ET.Element, prefix: str) -> None:
    for attr in ("name", "mesh", "material", "texture", "class", "hfield"):
        _prefix_attr(elem, attr, prefix)
    for child in list(elem):
        _rewrite_refs(child, prefix)


def _resolve_mesh_file(ref: str, search_dirs: list[Path]) -> Path | None:
    raw = Path(ref)
    if raw.is_file():
        return raw
    for directory in search_dirs:
        candidate = directory / raw.name
        if candidate.is_file():
            return candidate
        candidate = directory / raw
        if candidate.is_file():
            return candidate
    return None


def _merge_mjcf(
    host: ET.Element,
    asset: EmbodiedGenAsset,
    pos: tuple[float, float, float],
) -> dict[str, bytes]:
    assert asset.mjcf is not None
    mjcf_path = asset.mjcf
    src = ET.parse(mjcf_path).getroot()
    prefix = f"eg_{asset.name}_"
    vfs: dict[str, bytes] = {mjcf_path.name: mjcf_path.read_bytes()}

    host_asset = host.find("asset")
    if host_asset is None:
        host_asset = ET.SubElement(host, "asset")
    search_dirs = [mjcf_path.parent, asset.root, asset.root / "result", asset.root / "mjcf"]

    src_asset = src.find("asset")
    if src_asset is not None:
        for child in list(src_asset):
            old_file = child.get("file")
            if old_file:
                resolved = _resolve_mesh_file(old_file, search_dirs)
                key = prefix + Path(old_file).name
                if resolved is not None:
                    vfs[key] = resolved.read_bytes()
                child.set("file", key)
            _prefix_attr(child, "name", prefix)
            texture = child.get("texture")
            if texture:
                child.set("texture", prefix + texture)
            host_asset.append(child)

    host_world = host.find("worldbody")
    if host_world is None:
        host_world = ET.SubElement(host, "worldbody")
    wrapper = ET.SubElement(
        host_world,
        "body",
        name=f"embodiedgen_{asset.name}",
        pos=f"{pos[0]:g} {pos[1]:g} {pos[2]:g}",
    )
    src_world = src.find("worldbody")
    if src_world is not None:
        for child in list(src_world):
            _rewrite_refs(child, prefix)
            wrapper.append(child)
    return vfs


def _urdf_visual_to_geom(link: ET.Element, prefix: str) -> tuple[ET.Element, dict[str, bytes]]:
    """Convert the first URDF visual/collision into a MuJoCo geom + optional VFS mesh."""
    visual = link.find("visual")
    collision = link.find("collision")
    source = visual if visual is not None else collision
    geom = ET.Element("geom", name=f"{prefix}geom", rgba="0.7 0.4 0.2 1")
    vfs: dict[str, bytes] = {}
    if source is None:
        geom.set("type", "box")
        geom.set("size", "0.05 0.05 0.05")
        return geom, vfs

    geometry = source.find("geometry")
    if geometry is None:
        geom.set("type", "box")
        geom.set("size", "0.05 0.05 0.05")
        return geom, vfs

    box = geometry.find("box")
    cyl = geometry.find("cylinder")
    sphere = geometry.find("sphere")
    mesh = geometry.find("mesh")
    if box is not None:
        size = box.get("size", "0.1 0.1 0.1").split()
        halves = [str(float(v) / 2.0) for v in size[:3]]
        while len(halves) < 3:
            halves.append("0.05")
        geom.set("type", "box")
        geom.set("size", " ".join(halves))
    elif cyl is not None:
        radius = cyl.get("radius", "0.05")
        length = cyl.get("length", "0.1")
        geom.set("type", "cylinder")
        geom.set("size", f"{radius} {float(length) / 2.0}")
    elif sphere is not None:
        geom.set("type", "sphere")
        geom.set("size", sphere.get("radius", "0.05"))
    elif mesh is not None:
        filename = mesh.get("filename", "")
        geom.set("type", "mesh")
        geom.set("mesh", f"{prefix}mesh")
        if filename:
            vfs[f"{prefix}mesh_ref"] = filename.encode()
    else:
        geom.set("type", "box")
        geom.set("size", "0.05 0.05 0.05")
    return geom, vfs


def _merge_urdf(
    host: ET.Element,
    asset: EmbodiedGenAsset,
    pos: tuple[float, float, float],
) -> dict[str, bytes]:
    assert asset.urdf is not None
    urdf_path = asset.urdf
    root = ET.parse(urdf_path).getroot()
    prefix = f"eg_{asset.name}_"
    vfs: dict[str, bytes] = {urdf_path.name: urdf_path.read_bytes()}

    host_asset = host.find("asset")
    if host_asset is None:
        host_asset = ET.SubElement(host, "asset")
    host_world = host.find("worldbody")
    if host_world is None:
        host_world = ET.SubElement(host, "worldbody")

    link = root.find("link")
    geom, extra_vfs = _urdf_visual_to_geom(link if link is not None else ET.Element("link"), prefix)
    vfs.update({k: v for k, v in extra_vfs.items() if k != f"{prefix}mesh_ref"})

    mesh_ref = extra_vfs.get(f"{prefix}mesh_ref")
    if mesh_ref is not None:
        mesh_name = mesh_ref.decode()
        search = [urdf_path.parent, asset.root, asset.root / "result"]
        resolved = _resolve_mesh_file(mesh_name, search)
        key = prefix + Path(mesh_name).name
        if resolved is not None:
            vfs[key] = resolved.read_bytes()
            ET.SubElement(host_asset, "mesh", name=f"{prefix}mesh", file=key)
            geom.set("mesh", f"{prefix}mesh")
        else:
            # Keep the scene loadable even if the mesh file is missing.
            geom.set("type", "box")
            geom.attrib.pop("mesh", None)
            geom.set("size", "0.05 0.05 0.05")

    wrapper = ET.SubElement(
        host_world,
        "body",
        name=f"embodiedgen_{asset.name}",
        pos=f"{pos[0]:g} {pos[1]:g} {pos[2]:g}",
    )
    ET.SubElement(wrapper, "freejoint")
    wrapper.append(geom)
    return vfs


def attach_asset(
    scene_xml: str,
    asset: EmbodiedGenAsset,
    pos: tuple[float, float, float] = (0.5, 0.0, 0.3),
) -> ComposedScene:
    """Insert one EmbodiedGen asset into a MuJoCo scene XML string."""
    root = ET.fromstring(scene_xml)
    if asset.mjcf is not None:
        vfs = _merge_mjcf(root, asset, pos)
    elif asset.urdf is not None:
        vfs = _merge_urdf(root, asset, pos)
    else:
        raise ValueError(f"Asset {asset.name!r} has no MJCF or URDF to load into MuJoCo/Genesis")
    xml = ET.tostring(root, encoding="unicode")
    return ComposedScene(xml=xml, vfs=vfs)


def attach_placements(
    scene_xml: str,
    placements: list[AssetPlacement],
    *,
    config: GlobalConfig | None = None,
    export_dir: Path | None = None,
) -> ComposedScene:
    """Attach each placement to ``scene_xml``. Missing names raise FileNotFoundError."""
    vfs: dict[str, bytes] = {}
    xml = scene_xml
    for placement in placements:
        asset = find_asset(placement.name, config=config, export_dir=export_dir)
        composed = attach_asset(xml, asset, placement.pos)
        xml = composed.xml
        vfs.update(composed.vfs)
    return ComposedScene(xml=xml, vfs=vfs)


def apply_embodiedgen_scene(
    scene_xml: str,
    config: GlobalConfig | None = None,
) -> str:
    """Hook for ``load_scene_xml`` — no-op when no assets are configured."""
    cfg = config or global_config
    spec = cfg.mujoco_embodiedgen_assets
    if not spec:
        return scene_xml
    placements = parse_placements(spec)
    if not placements:
        return scene_xml
    return attach_placements(scene_xml, placements, config=cfg).xml


def mujoco_vfs_for_config(config: GlobalConfig | None = None) -> dict[str, bytes]:
    """Bytes to merge into MuJoCo's ``from_xml_string(..., assets=)`` dict."""
    cfg = config or global_config
    spec = cfg.mujoco_embodiedgen_assets
    if not spec:
        return {}
    placements = parse_placements(spec)
    if not placements:
        return {}
    # Compose against an empty host so we only collect VFS entries.
    return attach_placements(MINIMAL_SCENE_XML, placements, config=cfg).vfs


def compose_standalone_scene(
    placements: list[AssetPlacement],
    *,
    config: GlobalConfig | None = None,
    export_dir: Path | None = None,
) -> ComposedScene:
    """Minimal plane + placed EmbodiedGen assets (for CLI / tests)."""
    return attach_placements(MINIMAL_SCENE_XML, placements, config=config, export_dir=export_dir)


def genesis_entities(
    placements: list[AssetPlacement],
    *,
    config: GlobalConfig | None = None,
    export_dir: Path | None = None,
) -> list[dict[str, str | dict[str, object]]]:
    """Entity dicts accepted by ``GenesisSimulator`` / ``_load_entities``."""
    entities: list[dict[str, str | dict[str, object]]] = []
    for placement in placements:
        asset = find_asset(placement.name, config=config, export_dir=export_dir)
        path = asset.primary_for("genesis")
        if path is None:
            raise ValueError(f"Asset {asset.name!r} has no MJCF or URDF for Genesis")
        entity_type = "mjcf" if asset.mjcf is not None else "urdf"
        entities.append(
            {
                "type": entity_type,
                "path": str(path),
                "params": {"pos": list(placement.pos)},
            }
        )
    return entities


def isaac_usd_paths(
    placements: list[AssetPlacement],
    *,
    config: GlobalConfig | None = None,
    export_dir: Path | None = None,
) -> list[Path]:
    """USD files for Isaac Sim, if EmbodiedGen exported them."""
    paths: list[Path] = []
    for placement in placements:
        asset = find_asset(placement.name, config=config, export_dir=export_dir)
        if asset.usd is None:
            raise ValueError(
                f"Asset {asset.name!r} has no USD. Convert with EmbodiedGen "
                "MeshtoUSDConverter, then re-register the export."
            )
        paths.append(asset.usd)
    return paths
